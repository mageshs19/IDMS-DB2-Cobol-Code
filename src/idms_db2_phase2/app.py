from pathlib import Path
import sys

import streamlit as st


CURRENT_FILE = Path(__file__).resolve()
SRC_DIR = CURRENT_FILE.parents[1]

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


from idms_db2_phase2.ui.main_page import render_main_page


def main() -> None:
    st.set_page_config(
        page_title="IDMS DB2 Phase 2 Converter",
        layout="wide",
    )

    st.title(
        "IDMS > DB2 Phase 2 Converter"
    )

    st.caption(
        "Upload Sheet Mapping, DCLGEN, optional Copybook files, "
        "and IDMS COBOL source code to generate DB2 embedded SQL COBOL."
    )

    render_main_page()


if __name__ == "__main__":
    main()