"""Shared utilities for Streamlit pages."""

from __future__ import annotations

import base64
import json
import math
import re
import shutil
from pathlib import Path

import duckdb
import yaml

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
UPLOADS_DIR = ROOT / "uploads"
MODELS_DIR = ROOT / "models"
MDL_PATH = ROOT / "target" / "mdl.json"
DESCRIPTIONS_PATH = ROOT / "descriptions.json"
QUERIES_PATH = ROOT / "queries.yml"
RELATIONSHIPS_PATH = ROOT / "relationships.yml"
PROJECT_YML = ROOT / "wren_project.yml"
INSTRUCTIONS_PATH = ROOT / "instructions.md"

for _d in [DATA_DIR, UPLOADS_DIR, MODELS_DIR, ROOT / "target"]:
    _d.mkdir(parents=True, exist_ok=True)

_TYPE_MAP = {
    "VARCHAR": "TEXT", "TEXT": "TEXT", "STRING": "TEXT", "CHAR": "TEXT",
    "CLOB": "TEXT", "BLOB": "TEXT", "JSON": "TEXT", "UUID": "TEXT", "INTERVAL": "TEXT",
    "INTEGER": "INTEGER", "INT": "INTEGER", "INT4": "INTEGER", "SIGNED": "INTEGER",
    "SMALLINT": "INTEGER", "INT2": "INTEGER", "SHORT": "INTEGER",
    "TINYINT": "INTEGER", "INT1": "INTEGER",
    "BIGINT": "BIGINT", "INT8": "BIGINT", "LONG": "BIGINT", "HUGEINT": "BIGINT",
    "UBIGINT": "BIGINT", "UINTEGER": "INTEGER", "USMALLINT": "INTEGER", "UTINYINT": "INTEGER",
    "DOUBLE": "DOUBLE", "FLOAT8": "DOUBLE",
    "FLOAT": "FLOAT", "FLOAT4": "FLOAT", "REAL": "FLOAT",
    "DECIMAL": "DECIMAL", "NUMERIC": "DECIMAL",
    "DATE": "DATE",
    "TIMESTAMP": "TIMESTAMP", "TIMESTAMP WITH TIME ZONE": "TIMESTAMP",
    "TIMESTAMPTZ": "TIMESTAMP", "TIME": "TIME",
    "BOOLEAN": "BOOLEAN", "BOOL": "BOOLEAN",
}

JOIN_TYPES = ["ONE_TO_ONE", "ONE_TO_MANY", "MANY_TO_ONE", "MANY_TO_MANY"]


def normalize_type(raw: str) -> str:
    t = raw.upper().split("(")[0].strip()
    return _TYPE_MAP.get(t, "TEXT")


def sanitize_name(stem: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9]", "_", stem.lower())
    name = re.sub(r"_+", "_", name).strip("_")
    if name and name[0].isdigit():
        name = "t_" + name
    return name or "table"


# ── descriptions ──────────────────────────────────────────────────────────────

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


# ── relationships ─────────────────────────────────────────────────────────────

def load_relationships() -> list:
    if not RELATIONSHIPS_PATH.exists():
        return []
    data = yaml.safe_load(RELATIONSHIPS_PATH.read_text()) or {}
    return data.get("relationships") or []


def save_relationships(rels: list) -> None:
    RELATIONSHIPS_PATH.write_text(
        yaml.dump({"relationships": rels}, default_flow_style=False, allow_unicode=True)
    )


# ── schema context ────────────────────────────────────────────────────────────

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
    rels = mdl.get("relationships", [])
    if rels:
        lines.append(f"Relationships ({len(rels)}) — you can JOIN these tables:")
        for r in rels:
            lines.append(f"  - {r['name']}: {r['condition']} ({r['joinType']})")
        lines.append("")
    return "\n".join(lines)


# ── MDL ───────────────────────────────────────────────────────────────────────

def build_mdl_from_duckdb() -> dict:
    """Build MDL JSON directly from DuckDB schema + relationships.yml."""
    db_path = DATA_DIR / "data.duckdb"
    if not db_path.exists():
        return {}
    conn = duckdb.connect(str(db_path), read_only=True)
    tables = [row[0] for row in conn.execute("SHOW TABLES").fetchall()]
    models = []
    for table_name in tables:
        cols_raw = conn.execute(f'DESCRIBE "{table_name}"').fetchall()
        columns = [{"name": c[0], "type": normalize_type(c[1])} for c in cols_raw]
        models.append({
            "name": table_name,
            "tableReference": {"catalog": "data", "schema": "main", "table": table_name},
            "columns": columns,
        })
    conn.close()
    mdl: dict = {"catalog": "wren", "schema": "public", "models": models}
    rels = load_relationships()
    if rels:
        mdl["relationships"] = rels
    return mdl


def load_mdl() -> dict:
    if not MDL_PATH.exists():
        return {}
    with open(MDL_PATH) as f:
        return json.load(f)


def write_mdl(mdl: dict) -> None:
    MDL_PATH.write_text(json.dumps(mdl, indent=2))


# ── WrenEngine ────────────────────────────────────────────────────────────────

def init_engine(mdl: dict):
    """Initialize WrenEngine from MDL dict. Returns None if unavailable or no DuckDB."""
    db_path = DATA_DIR / "data.duckdb"
    if not db_path.exists() or not mdl:
        return None
    try:
        from wren.engine import WrenEngine
        from wren.model.data_source import DataSource
        mdl_b64 = base64.b64encode(json.dumps(mdl).encode()).decode()
        return WrenEngine(
            manifest_str=mdl_b64,
            data_source=DataSource.local_file,
            connection_info={"url": str(DATA_DIR), "format": "duckdb"},
        )
    except Exception:
        return None


def load_app_state() -> dict:
    mdl = load_mdl()
    if not mdl:
        mdl = build_mdl_from_duckdb()
        if mdl:
            write_mdl(mdl)
    if not mdl:
        return {"mdl": {}, "schema_context": "", "ready": False, "engine": None}
    # Merge current relationships into MDL (in case relationships changed after last build)
    rels = load_relationships()
    if rels:
        mdl["relationships"] = rels
    elif "relationships" in mdl:
        del mdl["relationships"]
    mdl_with_desc = apply_descriptions(mdl, load_descriptions())
    engine = init_engine(mdl)
    return {
        "mdl": mdl_with_desc,
        "schema_context": build_schema_context(mdl_with_desc),
        "ready": True,
        "engine": engine,
    }


# ── CSV processing ────────────────────────────────────────────────────────────

def process_csvs(csv_paths: list[Path]) -> dict:
    """Load CSVs into data/data.duckdb. Returns {table_name: {columns, row_count}}."""
    db_path = DATA_DIR / "data.duckdb"
    if db_path.exists():
        db_path.unlink()
    conn = duckdb.connect(str(db_path))
    schemas: dict = {}
    for csv_path in csv_paths:
        table_name = sanitize_name(Path(csv_path).stem)
        orig, counter = table_name, 1
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


# ── helpers ───────────────────────────────────────────────────────────────────

def safe_val(v):
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, (int, float, bool, str)):
        return v
    return str(v)


def recall_queries(question: str) -> tuple | None:
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


def save_feedback(question: str, sql: str) -> int:
    data = yaml.safe_load(QUERIES_PATH.read_text()) if QUERIES_PATH.exists() else {}
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
    return len(pairs)


# ── system prompt ─────────────────────────────────────────────────────────────

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
- Use exact model/table names as listed in the schema above
- For multi-table queries, use JOIN with the exact condition shown in the Relationships section
- Use `table` chart_type when result is a single value or text
- Use `pie` for proportional breakdowns with ≤8 categories
- Use `bar` for comparisons across categories
- Use `line` for time-series data
- SQL must be valid DuckDB SQL syntax
- aggregation_period values are EXACTLY: 'Day', 'MTD', 'Month', 'Week' (case-sensitive, no other values exist)
- For kingdom/country-wide queries always filter: place_type = 'country' AND place_name = 'Saudi Arabia'
- For top-N, use ORDER BY + LIMIT
- Do not add markdown fences around the JSON
"""
