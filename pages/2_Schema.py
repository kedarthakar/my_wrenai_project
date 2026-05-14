"""Schema metadata + relationships editor."""

from __future__ import annotations

import streamlit as st

from app_utils import (
    JOIN_TYPES,
    load_app_state,
    load_descriptions,
    load_relationships,
    save_descriptions,
    save_relationships,
    build_mdl_from_duckdb,
    write_mdl,
)

st.set_page_config(page_title="Schema — Wren", page_icon="📊", layout="wide")

st.title("📊 Schema Metadata")

if "app_state" not in st.session_state:
    st.session_state.app_state = load_app_state()

state = st.session_state.app_state

if not state["ready"]:
    st.warning("No data loaded yet.")
    st.page_link("pages/1_Setup.py", label="Upload CSV files first →", icon="📂")
    st.stop()

mdl = state["mdl"]
models = mdl.get("models", [])
model_names = [m["name"] for m in models]

# ── Table / column descriptions ───────────────────────────────────────────────
st.subheader("Table & column descriptions")
st.write("Descriptions are injected into the Claude prompt to improve SQL generation accuracy.")

desc = load_descriptions()

for model in models:
    table_name = model["name"]
    td = desc.setdefault("tables", {}).setdefault(table_name, {"description": "", "columns": {}})

    with st.expander(f"**{table_name}** — {len(model.get('columns', []))} columns", expanded=False):
        new_table_desc = st.text_input(
            "Table description",
            value=td.get("description", ""),
            placeholder="What does this table represent?",
            key=f"td_{table_name}",
        )
        td["description"] = new_table_desc

        st.divider()
        st.caption("Column descriptions")
        col_descs = td.setdefault("columns", {})
        for col in model.get("columns", []):
            col_name = col["name"]
            col_type = col.get("type", "TEXT")
            c1, c2, c3 = st.columns([2, 1, 4])
            with c1:
                st.code(col_name, language=None)
            with c2:
                st.caption(col_type)
            with c3:
                col_descs[col_name] = st.text_input(
                    "description",
                    value=col_descs.get(col_name, ""),
                    placeholder="What does this column mean?",
                    key=f"cd_{table_name}_{col_name}",
                    label_visibility="collapsed",
                )

if st.button("Save descriptions", type="primary", use_container_width=True):
    save_descriptions(desc)
    st.session_state.app_state = load_app_state()
    st.success("Saved! Schema context updated.")

# ── Relationships ─────────────────────────────────────────────────────────────
st.divider()
st.subheader("Relationships")
st.write(
    "Define JOIN relationships between tables. "
    "WrenEngine uses these to transpile multi-table queries through the semantic layer."
)

if len(models) < 2:
    st.info("Upload at least 2 tables to define relationships.")
else:
    rels = load_relationships()

    # Show existing relationships
    if rels:
        st.caption(f"{len(rels)} relationship(s) defined")
        to_delete = None
        for i, r in enumerate(rels):
            c1, c2 = st.columns([5, 1])
            with c1:
                st.markdown(
                    f"`{r['name']}` &nbsp;·&nbsp; `{r['condition']}` &nbsp;·&nbsp; _{r['joinType']}_"
                )
            with c2:
                if st.button("Delete", key=f"delrel_{i}", type="secondary"):
                    to_delete = i
        if to_delete is not None:
            rels.pop(to_delete)
            save_relationships(rels)
            mdl = build_mdl_from_duckdb()
            write_mdl(mdl)
            st.session_state.app_state = load_app_state()
            st.rerun()
    else:
        st.info("No relationships defined yet.")

    # Add new relationship
    st.divider()
    st.caption("**Add relationship**")

    # Build column map for dropdowns
    col_map = {m["name"]: [c["name"] for c in m.get("columns", [])] for m in models}

    c1, c2 = st.columns(2)
    with c1:
        model_a = st.selectbox("Table A", model_names, key="rel_model_a")
        col_a = st.selectbox("Join column (A)", col_map.get(model_a, []), key="rel_col_a")
    with c2:
        model_b = st.selectbox("Table B", [n for n in model_names if n != model_a], key="rel_model_b")
        col_b = st.selectbox("Join column (B)", col_map.get(model_b, []), key="rel_col_b")

    join_type = st.selectbox("Join type", JOIN_TYPES, index=1, key="rel_join_type")
    rel_name = st.text_input(
        "Relationship name",
        value=f"{model_a}__{model_b}",
        key="rel_name",
    )

    if st.button("Add relationship", type="primary"):
        if model_a and model_b and col_a and col_b and rel_name:
            # Check for duplicate name
            if any(r["name"] == rel_name for r in rels):
                st.error(f"Relationship '{rel_name}' already exists.")
            else:
                new_rel = {
                    "name": rel_name,
                    "models": [model_a, model_b],
                    "joinType": join_type,
                    "condition": f"{model_a}.{col_a} = {model_b}.{col_b}",
                }
                rels.append(new_rel)
                save_relationships(rels)
                # Rebuild MDL + engine with new relationship
                mdl = build_mdl_from_duckdb()
                write_mdl(mdl)
                st.session_state.app_state = load_app_state()
                st.success(f"Relationship '{rel_name}' added. WrenEngine reloaded.")
                st.rerun()
        else:
            st.error("Fill all fields before adding.")
