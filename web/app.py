"""
Wren AI-style web interface — supports CSV upload + MDL generation.
Run: cd web && ./start.sh
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import AsyncGenerator

import anthropic
import yaml
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

# ── paths ─────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"        # holds data.duckdb (only .duckdb file here)
UPLOADS_DIR = ROOT / "uploads"  # raw CSVs
MODELS_DIR = ROOT / "models"
MDL_PATH = ROOT / "target" / "mdl.json"
PROFILES_PATH = Path.home() / ".wren" / "profiles.yml"
PROJECT_YML = ROOT / "wren_project.yml"
DESCRIPTIONS_PATH = ROOT / "descriptions.json"
QUERIES_PATH = ROOT / "queries.yml"
import sys as _sys, shutil as _shutil
_wren_which = _shutil.which("wren")
if _wren_which:
    WREN_BIN = Path(_wren_which)
elif _sys.platform == "win32":
    WREN_BIN = Path.home() / ".venvs" / "wren" / "Scripts" / "wren.exe"
else:
    WREN_BIN = Path.home() / ".venvs" / "wren" / "bin" / "wren"
INSTRUCTIONS_PATH = ROOT / "instructions.md"

DATA_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(exist_ok=True)
MODELS_DIR.mkdir(exist_ok=True)

# ── load .env ──────────────────────────────────────────────────────────────────
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if _line.strip() and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# ── wren imports ───────────────────────────────────────────────────────────────
sys.path.insert(0, str(ROOT))
from wren.engine import WrenEngine          # noqa: E402
from wren.model.data_source import DataSource  # noqa: E402
from wren.type_mapping import parse_type    # noqa: E402

# ── executor for blocking I/O ─────────────────────────────────────────────────
_executor = ThreadPoolExecutor(max_workers=2)

async def run_blocking(fn, *args):
    return await asyncio.get_event_loop().run_in_executor(_executor, fn, *args)

# ── app state (mutable after CSV upload) ──────────────────────────────────────
class _State:
    engine: WrenEngine | None = None
    mdl: dict = {}
    schema_context: str = ""
    ready: bool = False

state = _State()

# ── helpers ───────────────────────────────────────────────────────────────────

def sanitize_name(stem: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9]", "_", stem.lower())
    name = re.sub(r"_+", "_", name).strip("_")
    if name and name[0].isdigit():
        name = "t_" + name
    return name or "table"


def load_descriptions() -> dict:
    if DESCRIPTIONS_PATH.exists():
        return json.loads(DESCRIPTIONS_PATH.read_text())
    return {"tables": {}}


def save_descriptions(desc: dict) -> None:
    DESCRIPTIONS_PATH.write_text(json.dumps(desc, indent=2))


def apply_descriptions(mdl: dict, desc: dict) -> dict:
    tables = desc.get("tables", {})
    for model in mdl.get("models", []):
        name = model["name"]
        if name not in tables:
            continue
        td = tables[name]
        if td.get("description"):
            model.setdefault("properties", {})["description"] = td["description"]
        col_descs = td.get("columns", {})
        for col in model.get("columns", []):
            if col["name"] in col_descs and col_descs[col["name"]]:
                col.setdefault("properties", {})["description"] = col_descs[col["name"]]
    return mdl


def build_schema_context(mdl: dict) -> str:
    models = mdl.get("models", [])
    if not models:
        return "No tables loaded yet. Upload CSV files to get started."
    lines = [f"You have access to {len(models)} table(s):\n"]
    for model in models:
        name = model["name"]
        desc = (model.get("properties") or {}).get("description", "")
        lines.append(f"Table: {name}")
        if desc:
            lines.append(f"Description: {desc}")
        lines.append("Columns:")
        for col in model.get("columns", []):
            col_name = col["name"]
            col_type = col.get("type", "TEXT")
            col_desc = (col.get("properties") or {}).get("description", "")
            entry = f"  - {col_name} ({col_type})"
            if col_desc:
                entry += f": {col_desc}"
            lines.append(entry)
        lines.append("")
    return "\n".join(lines)


def reload_engine() -> None:
    """Reload WrenEngine from current MDL file and update state."""
    if not MDL_PATH.exists():
        state.ready = False
        return
    with open(MDL_PATH) as f:
        mdl = json.load(f)
    mdl = apply_descriptions(mdl, load_descriptions())
    mdl_b64 = base64.b64encode(json.dumps(mdl).encode()).decode()
    if state.engine:
        try:
            state.engine.close()
        except Exception:
            pass
    state.engine = WrenEngine(
        manifest_str=mdl_b64,
        data_source=DataSource.local_file,
        connection_info={"url": str(DATA_DIR), "format": "duckdb"},
    )
    state.mdl = mdl
    state.schema_context = build_schema_context(mdl)
    state.ready = True


# ── initial load (best-effort on startup) ─────────────────────────────────────
try:
    reload_engine()
except Exception:
    pass

# ── CSV → DuckDB processing ───────────────────────────────────────────────────

def _process_csvs_blocking(csv_paths: list[Path]) -> dict[str, list[dict]]:
    """Load CSVs into data/data.duckdb. Returns {table_name: [col_info]}."""
    import duckdb

    db_path = DATA_DIR / "data.duckdb"
    if db_path.exists():
        db_path.unlink()

    conn = duckdb.connect(str(db_path))
    schemas: dict[str, list[dict]] = {}

    for csv_path in csv_paths:
        table_name = sanitize_name(csv_path.stem)
        # ensure unique table names
        orig = table_name
        counter = 1
        while table_name in schemas:
            table_name = f"{orig}_{counter}"
            counter += 1

        escaped = str(csv_path).replace("'", "''")
        conn.execute(f"""
            CREATE TABLE "{table_name}" AS
            SELECT * FROM read_csv('{escaped}', header=true, auto_detect=true, ignore_errors=true)
        """)
        cols_raw = conn.execute(f'DESCRIBE "{table_name}"').fetchall()
        row_count = conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
        schemas[table_name] = {
            "columns": [{"name": c[0], "type": c[1], "nullable": c[2] == "YES"} for c in cols_raw],
            "row_count": row_count,
        }

    conn.close()
    return schemas


def _write_models(schemas: dict) -> None:
    """Write MDL model YAML files from DuckDB schema."""
    # wipe existing models
    for d in MODELS_DIR.iterdir():
        if d.is_dir():
            shutil.rmtree(d)

    for table_name, info in schemas.items():
        model_dir = MODELS_DIR / table_name
        model_dir.mkdir(parents=True, exist_ok=True)

        columns_yaml = []
        for col in info["columns"]:
            raw_type = col["type"]
            try:
                normalized = parse_type(raw_type, "duckdb")
            except Exception:
                normalized = "TEXT"
            entry = f'  - name: "{col["name"]}"\n    type: {normalized}'
            if not col["nullable"]:
                entry += "\n    not_null: true"
            columns_yaml.append(entry)

        yaml_content = f"""name: {table_name}
table_reference:
  catalog: data
  schema: main
  table: "{table_name}"
columns:
{chr(10).join(columns_yaml)}
"""
        (model_dir / "metadata.yml").write_text(yaml_content)

    # clear relationships
    (ROOT / "relationships.yml").write_text("relationships: []\n")


def _update_project_yml() -> None:
    content = """schema_version: 3
name: my_project
version: "1.0"
catalog: wren
schema: public
data_source: local_file
"""
    PROJECT_YML.write_text(content)


def _build_mdl() -> tuple[bool, str]:
    """Run wren context validate + build. Returns (success, output)."""
    result = subprocess.run(
        ["wren", "context", "validate", "--path", str(ROOT)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return False, result.stderr or result.stdout

    result = subprocess.run(
        ["wren", "context", "build", "--path", str(ROOT)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return False, result.stderr or result.stdout

    return True, result.stdout.strip()


# ── SSE helper ────────────────────────────────────────────────────────────────

def sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


# ── anthropic client ──────────────────────────────────────────────────────────
_anthropic = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

SYSTEM_PROMPT_TEMPLATE = """You are a data analyst assistant. You translate natural language questions into SQL queries and choose the best visualization.

{schema_context}

{recalled_block}

Respond with ONLY a valid JSON object (no markdown, no text outside JSON):

{{
  "sql": "<SQL query>",
  "answer": "<1-2 sentence plain English summary>",
  "chart_type": "<bar|line|pie|table>",
  "chart_title": "<descriptive chart title>",
  "chart_x": "<column name for x-axis / labels, or null>",
  "chart_y": "<column name for y-axis / values, or null>",
  "chart_y_label": "<human-readable y-axis label>"
}}

Rules:
- Use `table` chart_type when result is a single value or text
- Use `pie` for proportional breakdowns with ≤8 categories
- Use `bar` for comparisons across categories
- Use `line` for time-series data
- SQL must be valid DuckDB SQL
- aggregation_period values are EXACTLY: 'Day', 'MTD', 'Month', 'Week' (case-sensitive, no other values exist)
- For kingdom/country-wide queries always filter: place_type = 'country' AND place_name = 'Saudi Arabia'
- For top-N, use ORDER BY + LIMIT
- Do not add markdown fences around the JSON
"""

# ── query recall from queries.yml ─────────────────────────────────────────────

def _safe_val(v):
    """Serialize a DataFrame cell to a JSON-safe Python value."""
    import math
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, (int, float, bool, str)):
        return v
    return str(v)


def _recall_queries(question: str) -> tuple[str, str] | None:
    if not QUERIES_PATH.exists():
        return None
    data = yaml.safe_load(QUERIES_PATH.read_text()) or {}
    pairs = data.get("pairs") or []
    q_words = set(question.lower().split())
    best, best_score = None, 0.0
    for pair in pairs:
        nl = pair.get("nl", "")
        sql = pair.get("sql", "")
        if not nl or not sql:
            continue
        nl_words = set(nl.lower().split())
        score = len(q_words & nl_words) / max(len(q_words | nl_words), 1)
        if score > best_score:
            best_score = score
            best = (nl, sql)
    return best if best_score > 0.25 else None

# ── fastapi app ───────────────────────────────────────────────────────────────
app = FastAPI(title="Wren Data Intelligence")

_INDEX_HTML = (Path(__file__).parent / "templates" / "index.html").read_text()
_SETUP_HTML = (Path(__file__).parent / "templates" / "setup.html").read_text()
_METADATA_HTML = (Path(__file__).parent / "templates" / "metadata.html").read_text()
_INSTRUCTIONS_HTML = (Path(__file__).parent / "templates" / "instructions.html").read_text()


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(_INDEX_HTML)


@app.get("/setup", response_class=HTMLResponse)
async def setup_page():
    return HTMLResponse(_SETUP_HTML)


@app.get("/api/status")
async def status():
    models = [m["name"] for m in state.mdl.get("models", [])]
    return JSONResponse({"ready": state.ready, "models": models})


@app.post("/api/upload")
async def upload_and_setup(files: list[UploadFile] = File(...)):
    """Accept CSV files, process, stream SSE progress."""

    async def generate() -> AsyncGenerator[str, None]:
        try:
            # ── save uploaded files ────────────────────────────────────────
            yield sse({"step": f"Received {len(files)} file(s). Saving..."})

            csv_paths: list[Path] = []
            for file in files:
                dest = UPLOADS_DIR / file.filename
                content = await file.read()
                dest.write_bytes(content)
                csv_paths.append(dest)
                size_kb = len(content) // 1024
                yield sse({"step": f"✓ Saved {file.filename} ({size_kb:,} KB)"})

            # ── load into DuckDB ───────────────────────────────────────────
            yield sse({"step": "Loading CSVs into DuckDB (auto-detecting types)..."})
            schemas = await run_blocking(_process_csvs_blocking, csv_paths)

            for tname, info in schemas.items():
                yield sse({
                    "step": f"✓ Table '{tname}' — {info['row_count']:,} rows, {len(info['columns'])} columns"
                })

            # ── generate model YAMLs ───────────────────────────────────────
            yield sse({"step": "Generating MDL model files..."})
            await run_blocking(_write_models, schemas)

            for tname, info in schemas.items():
                yield sse({"step": f"✓ Model '{tname}' written ({len(info['columns'])} columns)"})

            # ── update wren_project.yml ────────────────────────────────────
            await run_blocking(_update_project_yml)
            yield sse({"step": "Updated wren_project.yml"})

            # ── validate + build ───────────────────────────────────────────
            yield sse({"step": "Validating MDL..."})
            ok, msg = await run_blocking(_build_mdl)
            if not ok:
                yield sse({"error": f"Build failed: {msg}"})
                return
            yield sse({"step": f"✓ {msg}"})

            # ── reload engine ──────────────────────────────────────────────
            yield sse({"step": "Reloading query engine..."})
            await run_blocking(reload_engine)
            yield sse({"step": "✓ Engine ready"})

            # ── done ───────────────────────────────────────────────────────
            model_summary = [
                {
                    "name": t,
                    "rows": info["row_count"],
                    "columns": len(info["columns"]),
                }
                for t, info in schemas.items()
            ]
            yield sse({"done": True, "models": model_summary})

        except Exception as e:
            import traceback
            yield sse({"error": str(e), "traceback": traceback.format_exc()})

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/query")
async def query(request: Request):
    body = await request.json()
    question = body.get("question", "").strip()
    if not question:
        return JSONResponse({"error": "No question provided"}, status_code=400)
    if not state.ready:
        return JSONResponse({"error": "No data loaded. Please upload CSV files first."}, status_code=400)

    async def generate() -> AsyncGenerator[str, None]:
        try:
            # Step 1: check memory
            yield sse({"step": "Checking memory for similar queries..."})
            recalled = await run_blocking(_recall_queries, question)
            recalled_block = ""
            if recalled:
                yield sse({"step": f"Found similar query: \"{recalled[0]}\""})
                recalled_block = (
                    f"Similar confirmed query:\nQ: {recalled[0]}\nSQL:\n{recalled[1]}\n\n"
                    "Use this as a reference if the question is similar."
                )
            else:
                yield sse({"step": "No similar query found — generating from schema..."})

            # Step 2: generate SQL via Claude
            yield sse({"step": "Generating SQL..."})
            instructions = INSTRUCTIONS_PATH.read_text().strip() if INSTRUCTIONS_PATH.exists() else ""
            instructions_block = f"\n\nBusiness rules and context:\n{instructions}" if instructions and instructions != "# User Instructions\n\nAdd custom rules or guidelines for LLM-based query generation here." else ""
            system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
                schema_context=state.schema_context,
                recalled_block=recalled_block + instructions_block,
            )
            try:
                msg = await run_blocking(
                    lambda: _anthropic.messages.create(
                        model="claude-sonnet-4-6",
                        max_tokens=1024,
                        system=system_prompt,
                        messages=[{"role": "user", "content": question}],
                    )
                )
                raw = msg.content[0].text.strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                plan = json.loads(raw)
            except json.JSONDecodeError as e:
                yield sse({"error": f"Claude returned invalid JSON: {e}"})
                return
            except Exception as e:
                yield sse({"error": f"Claude error: {e}"})
                return

            sql = plan.get("sql", "")
            yield sse({"sql": sql})

            # Step 3: execute
            yield sse({"step": "Running query..."})
            try:
                import duckdb as _duckdb
                conn = _duckdb.connect(str(DATA_DIR / "data.duckdb"), read_only=True)
                df = conn.execute(sql).fetchdf()
                conn.close()
                columns = list(df.columns)
                rows = []
                for record in df.to_dict(orient="records"):
                    row = {}
                    for k, v in record.items():
                        row[k] = _safe_val(v)
                    rows.append(row)
                num_rows = len(rows)
            except Exception as e:
                yield sse({"error": f"Query error: {e}", "sql": sql})
                return

            yield sse({
                "done": True,
                "sql": sql,
                "answer": plan.get("answer", ""),
                "chart_type": plan.get("chart_type", "table"),
                "chart_title": plan.get("chart_title", ""),
                "chart_x": plan.get("chart_x"),
                "chart_y": plan.get("chart_y"),
                "chart_y_label": plan.get("chart_y_label", ""),
                "columns": columns,
                "rows": rows,
                "row_count": num_rows,
            })

        except Exception as e:
            import traceback
            yield sse({"error": str(e), "traceback": traceback.format_exc()})

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/run-sql")
async def run_sql(request: Request):
    body = await request.json()
    sql = body.get("sql", "").strip()
    if not sql:
        return JSONResponse({"error": "No SQL provided"}, status_code=400)

    def _execute(sql: str):
        import duckdb as _duckdb
        conn = _duckdb.connect(str(DATA_DIR / "data.duckdb"), read_only=True)
        try:
            df = conn.execute(sql).fetchdf()
            columns = list(df.columns)
            rows = []
            for record in df.to_dict(orient="records"):
                row = {}
                for k, v in record.items():
                    if hasattr(v, "isoformat"):
                        row[k] = v.isoformat()
                    elif v is None or isinstance(v, (int, float, bool, str)):
                        row[k] = v
                    else:
                        row[k] = str(v)
                rows.append(row)
            return {"columns": columns, "rows": rows, "row_count": len(rows)}
        finally:
            conn.close()

    try:
        result = await run_blocking(_execute, sql)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── instructions endpoints ────────────────────────────────────────────────────

@app.get("/instructions", response_class=HTMLResponse)
async def instructions_page():
    return HTMLResponse(_INSTRUCTIONS_HTML)


@app.get("/api/queries")
async def get_queries():
    if not QUERIES_PATH.exists():
        return JSONResponse({"pairs": []})
    data = yaml.safe_load(QUERIES_PATH.read_text()) or {}
    pairs = [{"nl": p.get("nl") or p.get("question",""), "sql": p.get("sql","")}
             for p in (data.get("pairs") or []) if p.get("nl") or p.get("question")]
    return JSONResponse({"pairs": pairs})


@app.delete("/api/queries")
async def delete_query(request: Request):
    body = await request.json()
    nl = body.get("nl", "").strip()
    if not nl:
        return JSONResponse({"error": "nl required"}, status_code=400)
    data = yaml.safe_load(QUERIES_PATH.read_text()) or {}
    pairs = [p for p in (data.get("pairs") or [])
             if (p.get("nl") or p.get("question")) != nl]
    data["pairs"] = pairs
    QUERIES_PATH.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True))
    if WREN_BIN.exists():
        asyncio.get_event_loop().run_in_executor(
            _executor, lambda: subprocess.run(
                [str(WREN_BIN), "memory", "index"], capture_output=True, cwd=str(ROOT)
            ))
    return JSONResponse({"ok": True, "total_pairs": len(pairs)})


@app.get("/api/instructions")
async def get_instructions():
    content = INSTRUCTIONS_PATH.read_text() if INSTRUCTIONS_PATH.exists() else ""
    return JSONResponse({"content": content})


@app.post("/api/instructions")
async def save_instructions(request: Request):
    body = await request.json()
    content = body.get("content", "")
    INSTRUCTIONS_PATH.write_text(content)
    return JSONResponse({"ok": True})


# ── metadata endpoints ────────────────────────────────────────────────────────

@app.get("/metadata", response_class=HTMLResponse)
async def metadata_page():
    return HTMLResponse(_METADATA_HTML)


@app.get("/api/metadata")
async def get_metadata():
    if not MDL_PATH.exists():
        return JSONResponse({"models": []})
    with open(MDL_PATH) as f:
        mdl = json.load(f)
    desc = load_descriptions()
    models_out = []
    for model in mdl.get("models", []):
        name = model["name"]
        td = desc.get("tables", {}).get(name, {})
        columns_out = []
        for col in model.get("columns", []):
            col_desc = td.get("columns", {}).get(col["name"], "")
            columns_out.append({
                "name": col["name"],
                "type": col.get("type", "TEXT"),
                "description": col_desc,
            })
        models_out.append({
            "name": name,
            "description": td.get("description", ""),
            "columns": columns_out,
        })
    return JSONResponse({"models": models_out})


@app.post("/api/metadata")
async def save_metadata(request: Request):
    body = await request.json()
    save_descriptions(body)
    await run_blocking(reload_engine)
    return JSONResponse({"ok": True})


# ── feedback endpoint ─────────────────────────────────────────────────────────

@app.post("/api/feedback")
async def save_feedback(request: Request):
    body = await request.json()
    question = (body.get("nl") or body.get("question", "")).strip()
    sql = body.get("sql", "").strip()
    if not question or not sql:
        return JSONResponse({"error": "question and sql required"}, status_code=400)

    data = yaml.safe_load(QUERIES_PATH.read_text()) or {}
    pairs = data.get("pairs") or []
    for pair in pairs:
        if pair.get("nl") == question or pair.get("question") == question:
            pair.pop("question", None)
            pair["nl"] = question
            pair["sql"] = sql
            break
    else:
        pairs.append({"nl": question, "sql": sql})
    data["pairs"] = pairs
    QUERIES_PATH.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True))

    if WREN_BIN.exists():
        asyncio.get_event_loop().run_in_executor(
            _executor, lambda: subprocess.run(
                [str(WREN_BIN), "memory", "index"], capture_output=True, cwd=str(ROOT)
            ))

    return JSONResponse({"ok": True, "total_pairs": len(pairs)})
