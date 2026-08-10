import streamlit as st

from idms_db2_phase2.ui.main_page import render_main_page


def main() -> None:
    st.set_page_config(
        page_title="IDMS DB2 Phase 2 Converter",
        layout="wide",
    )

    st.title("IDMS > DB2 Phase 2 Converter")

    st.caption(
        "Upload Sheet Mapping, DCLGEN, Copybook, optional Copybook PDF, "
        "and IDMS COBOL code to generate DB2 embedded SQL COBOL."
    )

    render_main_page()


if __name__ == "__main__":
    main()