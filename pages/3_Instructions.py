"""Business rules / instructions editor."""

from __future__ import annotations

import streamlit as st

from app_utils import INSTRUCTIONS_PATH, QUERIES_PATH
import yaml

st.set_page_config(page_title="Context — Wren", page_icon="📝", layout="centered")

st.title("📝 Context & Instructions")

# ── Business rules ────────────────────────────────────────────────────────────
st.subheader("Business rules")
st.write("Custom rules injected into every SQL generation prompt.")

_default = "# User Instructions\n\nAdd custom rules or guidelines for LLM-based query generation here."
current = INSTRUCTIONS_PATH.read_text() if INSTRUCTIONS_PATH.exists() else _default

new_instructions = st.text_area(
    "Instructions",
    value=current,
    height=300,
    label_visibility="collapsed",
)

if st.button("Save instructions", type="primary"):
    INSTRUCTIONS_PATH.write_text(new_instructions)
    st.success("Saved!")

# ── Query memory ──────────────────────────────────────────────────────────────
st.divider()
st.subheader("Query memory")
st.write("Confirmed SQL pairs used as few-shot examples for similar future questions.")

if not QUERIES_PATH.exists():
    st.info("No confirmed queries yet. Confirm a SQL result on the main page to add one.")
else:
    data = yaml.safe_load(QUERIES_PATH.read_text()) or {}
    pairs = data.get("pairs") or []
    if not pairs:
        st.info("No confirmed queries yet.")
    else:
        st.caption(f"{len(pairs)} confirmed pair(s)")
        to_delete = None
        for i, pair in enumerate(pairs):
            nl = pair.get("nl") or pair.get("question", "")
            sql = pair.get("sql", "")
            with st.expander(f"{nl[:80]}..."):
                st.code(sql, language="sql")
                if st.button("Delete", key=f"del_{i}", type="secondary"):
                    to_delete = i

        if to_delete is not None:
            pairs.pop(to_delete)
            data["pairs"] = pairs
            QUERIES_PATH.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True))
            st.rerun()
