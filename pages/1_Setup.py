"""CSV upload page — builds DuckDB and MDL."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from app_utils import (
    UPLOADS_DIR,
    build_mdl_from_duckdb,
    load_app_state,
    process_csvs,
    write_mdl,
)

st.set_page_config(page_title="Upload Data — Wren", page_icon="📂", layout="centered")

st.title("📂 Upload Data")
st.write("Drop one or more CSV files. The app will auto-detect schema and make them queryable in the chat.")

uploaded = st.file_uploader(
    "Choose CSV files",
    type=["csv"],
    accept_multiple_files=True,
    label_visibility="collapsed",
)

if uploaded:
    st.write(f"**{len(uploaded)} file(s) selected:**")
    for f in uploaded:
        st.caption(f"• {f.name} — {f.size / 1024:.1f} KB")

    if st.button("Generate MDL & Load Data", type="primary", use_container_width=True):
        progress_box = st.empty()
        log_lines: list[str] = []

        def log(msg: str) -> None:
            log_lines.append(msg)
            progress_box.code("\n".join(log_lines), language=None)

        try:
            # Save uploaded files
            csv_paths: list[Path] = []
            for f in uploaded:
                dest = UPLOADS_DIR / f.name
                dest.write_bytes(f.read())
                csv_paths.append(dest)
                log(f"✓ Saved {f.name}")

            # Load into DuckDB
            log("Loading CSVs into DuckDB (auto-detecting types)...")
            schemas = process_csvs(csv_paths)
            for tname, info in schemas.items():
                log(f"✓ Table '{tname}' — {info['row_count']:,} rows, {len(info['columns'])} columns")

            # Build MDL JSON directly
            log("Building MDL schema...")
            mdl = build_mdl_from_duckdb()
            write_mdl(mdl)
            log(f"✓ MDL written — {len(mdl.get('models', []))} model(s)")

            # Refresh session state for all pages
            st.session_state.app_state = load_app_state()
            log("✓ Engine ready")

            st.success("Setup complete — data is ready to query!")
            st.balloons()

            # Show model cards
            cols = st.columns(min(len(schemas), 3))
            for i, (tname, info) in enumerate(schemas.items()):
                with cols[i % len(cols)]:
                    st.metric(tname, f"{info['row_count']:,} rows", f"{len(info['columns'])} columns")

            st.page_link("streamlit_app.py", label="Start querying →", icon="💬")

        except Exception as e:
            import traceback
            st.error(f"Error: {e}")
            st.code(traceback.format_exc())
