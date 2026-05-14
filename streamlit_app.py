"""Wren Data Intelligence — Streamlit app (main query page)."""

from __future__ import annotations

import json
import os
import time

import anthropic
import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

from app_utils import (
    DATA_DIR,
    INSTRUCTIONS_PATH,
    SYSTEM_PROMPT_TEMPLATE,
    load_app_state,
    recall_queries,
    safe_val,
    save_feedback,
)

st.set_page_config(
    page_title="Wren Data Intelligence",
    page_icon="📡",
    layout="wide",
)

# ── session state ─────────────────────────────────────────────────────────────
if "app_state" not in st.session_state:
    st.session_state.app_state = load_app_state()
if "messages" not in st.session_state:
    st.session_state.messages = []
if "confirmed" not in st.session_state:
    st.session_state.confirmed = set()
if "usage_stats" not in st.session_state:
    st.session_state.usage_stats = {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_latency_ms": 0,
    }

state = st.session_state.app_state

# ── secrets ───────────────────────────────────────────────────────────────────
def _secret(key: str) -> str:
    try:
        return st.secrets.get(key) or os.environ.get(key, "")
    except Exception:
        return os.environ.get(key, "")

api_key = _secret("ANTHROPIC_API_KEY")

# ── Langfuse (optional — active only when keys are configured) ────────────────
_langfuse = None
try:
    from langfuse import Langfuse
    _lf_pub = _secret("LANGFUSE_PUBLIC_KEY")
    _lf_sec = _secret("LANGFUSE_SECRET_KEY")
    _lf_host = _secret("LANGFUSE_HOST") or "https://cloud.langfuse.com"
    if _lf_pub and _lf_sec:
        _langfuse = Langfuse(public_key=_lf_pub, secret_key=_lf_sec, host=_lf_host)
except Exception:
    pass

# Claude Sonnet 4.6 pricing (per million tokens)
_COST_INPUT_PER_M = 3.0
_COST_OUTPUT_PER_M = 15.0


# ── query handler ─────────────────────────────────────────────────────────────

def handle_query(question: str) -> dict:
    recalled = recall_queries(question)
    recalled_block = ""
    if recalled:
        recalled_block = (
            f"Similar confirmed query:\nQ: {recalled[0]}\nSQL:\n{recalled[1]}\n\n"
            "Use this as a reference if the question is similar."
        )
    instructions = INSTRUCTIONS_PATH.read_text().strip() if INSTRUCTIONS_PATH.exists() else ""
    _default = "# User Instructions\n\nAdd custom rules or guidelines for LLM-based query generation here."
    if instructions and instructions != _default:
        recalled_block += f"\n\nBusiness rules and context:\n{instructions}"

    system = SYSTEM_PROMPT_TEMPLATE.format(
        schema_context=state["schema_context"],
        recalled_block=recalled_block,
    )

    # ── Langfuse trace ────────────────────────────────────────────────────────
    trace = None
    generation = None
    if _langfuse:
        trace = _langfuse.trace(
            name="data-query",
            input={"question": question},
            metadata={"model": "claude-sonnet-4-6"},
        )
        generation = trace.generation(
            name="sql-generation",
            model="claude-sonnet-4-6",
            model_parameters={"max_tokens": 1024},
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": question},
            ],
        )

    # ── Claude API call ───────────────────────────────────────────────────────
    t0 = time.time()
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": question}],
        )
        llm_latency_ms = int((time.time() - t0) * 1000)
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        plan = json.loads(raw)
    except json.JSONDecodeError as e:
        if generation:
            generation.end(level="ERROR", status_message=str(e))
        if trace:
            trace.update(output={"error": str(e)})
            _langfuse.flush()
        return {"error": f"Claude returned invalid JSON: {e}", "question": question}
    except Exception as e:
        if generation:
            generation.end(level="ERROR", status_message=str(e))
        if trace:
            trace.update(output={"error": str(e)})
            _langfuse.flush()
        return {"error": f"Claude error: {e}", "question": question}

    in_tok = msg.usage.input_tokens
    out_tok = msg.usage.output_tokens

    if generation:
        generation.end(
            output=raw,
            usage={"input": in_tok, "output": out_tok, "unit": "TOKENS"},
        )

    # Accumulate session stats
    u = st.session_state.usage_stats
    u["calls"] += 1
    u["input_tokens"] += in_tok
    u["output_tokens"] += out_tok
    u["total_latency_ms"] += llm_latency_ms

    # ── SQL execution (via WrenEngine if available, else direct DuckDB) ────────
    sql = plan.get("sql", "")
    engine = state.get("engine")
    t1 = time.time()
    try:
        if engine:
            native_sql = engine.dry_plan(sql)  # transpile through semantic layer
            conn = duckdb.connect(str(DATA_DIR / "data.duckdb"), read_only=True)
            df = conn.execute(native_sql).fetchdf()
            conn.close()
        else:
            native_sql = sql
            conn = duckdb.connect(str(DATA_DIR / "data.duckdb"), read_only=True)
            df = conn.execute(sql).fetchdf()
            conn.close()

        sql_latency_ms = int((time.time() - t1) * 1000)
        rows = [{k: safe_val(v) for k, v in record.items()} for record in df.to_dict(orient="records")]

        if trace:
            span = trace.span(name="sql-execution", input={"sql": sql, "native_sql": native_sql})
            span.end(output={"row_count": len(rows), "latency_ms": sql_latency_ms})
            trace.update(output={"answer": plan.get("answer", ""), "row_count": len(rows)})
            _langfuse.flush()

        return {
            "question": question,
            "sql": sql,
            "native_sql": native_sql if native_sql != sql else None,
            "answer": plan.get("answer", ""),
            "chart_type": plan.get("chart_type", "table"),
            "chart_title": plan.get("chart_title", ""),
            "chart_x": plan.get("chart_x"),
            "chart_y": plan.get("chart_y"),
            "chart_y_label": plan.get("chart_y_label", ""),
            "columns": list(df.columns),
            "rows": rows,
            "row_count": len(rows),
            "_usage": {
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "llm_latency_ms": llm_latency_ms,
                "sql_latency_ms": sql_latency_ms,
                "engine": "WrenEngine" if engine else "DuckDB direct",
            },
        }
    except Exception as e:
        if trace:
            span = trace.span(name="sql-execution", input={"sql": sql})
            span.end(level="ERROR", status_message=str(e))
            trace.update(output={"error": str(e)})
            _langfuse.flush()
        return {"error": f"Query error: {e}", "sql": sql, "question": question}


# ── renderer ──────────────────────────────────────────────────────────────────

def render_bot(content: dict, idx: int) -> None:
    if content.get("error"):
        st.error(content["error"])
        if content.get("sql"):
            st.code(content["sql"], language="sql")
        return

    st.write(content["answer"])

    # SQL + actions
    st.code(content["sql"], language="sql")
    c1, c2 = st.columns([1, 1])
    with c1:
        if idx in st.session_state.confirmed:
            st.success("SQL saved to memory")
        else:
            if st.button("✓ Confirm SQL", key=f"confirm_{idx}"):
                save_feedback(content["question"], content["sql"])
                st.session_state.confirmed.add(idx)
                st.rerun()
    with c2:
        with st.expander("Edit SQL"):
            new_sql = st.text_area(
                "", value=content["sql"], key=f"edit_{idx}",
                height=120, label_visibility="collapsed",
            )
            if st.button("Save corrected SQL", key=f"save_{idx}"):
                save_feedback(content["question"], new_sql)
                content["sql"] = new_sql
                st.session_state.confirmed.add(idx)
                st.rerun()

    # Transpiled SQL (shown only when WrenEngine expanded it — i.e. relationships involved)
    if content.get("native_sql"):
        with st.expander("Transpiled SQL (via WrenEngine)"):
            st.code(content["native_sql"], language="sql")

    # Per-query token stats
    if content.get("_usage"):
        u = content["_usage"]
        cost = (u["input_tokens"] * _COST_INPUT_PER_M + u["output_tokens"] * _COST_OUTPUT_PER_M) / 1_000_000
        st.caption(
            f"Tokens: {u['input_tokens']:,} in / {u['output_tokens']:,} out · "
            f"LLM: {u['llm_latency_ms']}ms · SQL: {u['sql_latency_ms']}ms · "
            f"Cost: ${cost:.5f}"
        )

    # Chart
    ctype = content.get("chart_type", "table")
    x_col, y_col = content.get("chart_x"), content.get("chart_y")
    rows = content.get("rows", [])
    if ctype != "table" and x_col and y_col and rows:
        df = pd.DataFrame(rows)
        if x_col in df.columns and y_col in df.columns:
            title = content.get("chart_title", "")
            y_label = content.get("chart_y_label", y_col)
            if ctype == "bar":
                fig = px.bar(df, x=x_col, y=y_col, title=title, labels={y_col: y_label})
            elif ctype == "line":
                fig = px.line(df, x=x_col, y=y_col, title=title, labels={y_col: y_label})
            elif ctype == "pie":
                fig = px.pie(df, names=x_col, values=y_col, title=title)
            else:
                fig = None
            if fig:
                fig.update_layout(template="plotly_dark")
                st.plotly_chart(fig, use_container_width=True)

    # Data table
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True)
        st.caption(f"{content.get('row_count', len(rows)):,} rows")


# ── sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("📡 Wren Data Intelligence")
    if state["ready"]:
        models = state["mdl"].get("models", [])
        st.success(f"{len(models)} table(s) loaded")
        for m in models:
            st.caption(f"• {m['name']}")
    else:
        st.warning("No data loaded")
        st.page_link("pages/1_Setup.py", label="Upload CSV files →", icon="📂")

    if not api_key:
        st.error("ANTHROPIC_API_KEY not set")

    # ── session usage stats ───────────────────────────────────────────────────
    u = st.session_state.usage_stats
    if u["calls"] > 0:
        st.divider()
        st.caption("**Session usage**")
        cost = (u["input_tokens"] * _COST_INPUT_PER_M + u["output_tokens"] * _COST_OUTPUT_PER_M) / 1_000_000
        avg_ms = u["total_latency_ms"] / u["calls"]
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Queries", u["calls"])
            st.metric("Est. cost", f"${cost:.4f}")
        with col2:
            st.metric("In tokens", f"{u['input_tokens']:,}")
            st.metric("Out tokens", f"{u['output_tokens']:,}")
        st.caption(f"Avg LLM latency: {avg_ms:.0f} ms")
        if _langfuse:
            st.caption("📊 Langfuse tracing active")

    if state["ready"]:
        engine_label = "⚡ WrenEngine (semantic layer)" if state.get("engine") else "🔷 DuckDB direct"
        st.caption(engine_label)
        rels = state["mdl"].get("relationships", [])
        if rels:
            st.caption(f"  {len(rels)} relationship(s) defined")

    st.divider()
    st.subheader("Try asking")
    suggestions = [
        "Show me 5G speeds of all 3 operators in Kingdom",
        "Where should STC invest in 5G?",
        "Show STC's 5G speed for past 4 weeks",
    ]
    for s in suggestions:
        if st.button(s, use_container_width=True, key=f"sug_{s[:20]}"):
            st.session_state.pending_question = s

    if st.session_state.messages:
        st.divider()
        if st.button("Clear chat", use_container_width=True):
            st.session_state.messages = []
            st.session_state.confirmed = set()
            st.rerun()

# ── main ──────────────────────────────────────────────────────────────────────
st.title("Data Intelligence")

for i, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        if msg["role"] == "user":
            st.write(msg["content"])
        else:
            render_bot(msg["content"], i)

pending = st.session_state.get("pending_question")
if pending:
    del st.session_state["pending_question"]

disabled = not state["ready"] or not api_key
question = st.chat_input("Ask a question about your data...", disabled=disabled)
active_q = question or pending

if active_q:
    idx = len(st.session_state.messages)

    st.session_state.messages.append({"role": "user", "content": active_q})
    with st.chat_message("user"):
        st.write(active_q)

    with st.chat_message("assistant"):
        with st.spinner("Generating SQL..."):
            result = handle_query(active_q)
        render_bot(result, idx + 1)

    st.session_state.messages.append({"role": "assistant", "content": result})
