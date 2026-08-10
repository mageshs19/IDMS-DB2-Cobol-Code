import re
from pathlib import Path

import streamlit as st

from idms_db2_phase2.domain.models import ConversionInput
from idms_db2_phase2.parsers.copybook_parser import CopybookParser
from idms_db2_phase2.parsers.dclgen_parser import DclgenParser
from idms_db2_phase2.parsers.sheet_mapping_parser import SheetMappingParser
from idms_db2_phase2.parsers.text_loader import TextLoader
from idms_db2_phase2.services.conversion_service import ConversionService
from idms_db2_phase2.services.metadata_service import MetadataService


def initialize_session_state() -> None:
    defaults = {
        "sheet_mapping_rows": [],
        "dclgen_columns": [],
        "copybook_fields": [],
        "idms_cobol_text": "",
        "idms_cobol_pdf_name": "",
        "converted_cobol": "",
        "converted_cobol_file_name": "converted_db2_cobol.cbl",
        "validation_messages": [],
        "operations": [],
        "generated": False,
        "loaded": False,
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def render_main_page() -> None:
    initialize_session_state()

    tabs = st.tabs(
        [
            "Main",
            "Metadata Overview",
            "Column Names",
            "Sets",
            "Sheet Mapping Rows",
            "Generated DB2 COBOL",
            "Validation",
        ]
    )

    with tabs[0]:
        render_main_tab()

    with tabs[1]:
        render_metadata_overview_tab()

    with tabs[2]:
        render_column_names_tab()

    with tabs[3]:
        render_sets_tab()

    with tabs[4]:
        render_sheet_mapping_rows_tab()

    with tabs[5]:
        render_generated_cobol_tab()

    with tabs[6]:
        render_validation_tab()


def render_main_tab() -> None:
    st.markdown("## Upload Inputs")

    st.info(
        "Upload Sheet Mapping, one or more DCLGEN files, optional Copybook files, "
        "and the IDMS COBOL program PDF to generate DB2 embedded SQL COBOL."
    )

    col1, col2 = st.columns(2)

    with col1:
        sheet_mapping_file = st.file_uploader(
            "Sheet Mapping Excel or CSV",
            type=["xlsx", "csv"],
            key="sheet_mapping_file",
            help="Upload the Sheet Mapping file generated or maintained for IDMS to DB2 mapping.",
        )

        dclgen_files = st.file_uploader(
            "DCLGEN Files",
            type=["txt", "cpy", "cbl", "cob"],
            accept_multiple_files=True,
            key="dclgen_files",
            help="Upload one or more DCLGEN files.",
        )

    with col2:
        copybook_files = st.file_uploader(
            "Optional Copybook Files",
            type=["txt", "cpy", "cbl", "cob"],
            accept_multiple_files=True,
            key="copybook_files",
            help="Optional. Upload one or more copybook text files if available.",
        )

        idms_cobol_pdf_file = st.file_uploader(
            "IDMS COBOL Program PDF",
            type=["pdf"],
            key="idms_cobol_pdf_file",
            help="Upload the source IDMS COBOL program as PDF.",
        )

    target_program_id = st.text_input(
        "Target PROGRAM-ID",
        value="",
        placeholder="Example: VMDZ1567",
        help=(
            "Optional. If provided, this renames the COBOL PROGRAM-ID "
            "and becomes the default downloaded .cbl file name."
        ),
    )

    load_clicked = st.button(
        "Load and Analyze Inputs",
        type="secondary",
        use_container_width=True,
    )

    if load_clicked:
        load_inputs(
            sheet_mapping_file=sheet_mapping_file,
            dclgen_files=dclgen_files,
            copybook_files=copybook_files,
            idms_cobol_pdf_file=idms_cobol_pdf_file,
        )

    generate_clicked = st.button(
        "Generate DB2 COBOL Code",
        type="primary",
        use_container_width=True,
    )

    if generate_clicked:
        if not st.session_state.loaded:
            load_inputs(
                sheet_mapping_file=sheet_mapping_file,
                dclgen_files=dclgen_files,
                copybook_files=copybook_files,
                idms_cobol_pdf_file=idms_cobol_pdf_file,
            )

        generate_cobol(
            target_program_id=target_program_id,
        )

    if st.session_state.generated:
        st.success("DB2 COBOL generation completed.")

        if st.session_state.converted_cobol:
            render_download_generated_cobol_button()

    render_current_status()


def load_inputs(
    sheet_mapping_file,
    dclgen_files,
    copybook_files,
    idms_cobol_pdf_file,
) -> None:
    sheet_parser = SheetMappingParser()
    dclgen_parser = DclgenParser()
    copybook_parser = CopybookParser()
    text_loader = TextLoader()

    sheet_rows = sheet_parser.parse_uploaded_file(
        sheet_mapping_file,
    )

    dclgen_texts: list[str] = []

    for file in dclgen_files or []:
        dclgen_texts.append(
            text_loader.read_uploaded_text(
                file,
            )
        )

    dclgen_columns = dclgen_parser.parse_many_texts(
        dclgen_texts,
    )

    copybook_text_parts: list[str] = []

    for file in copybook_files or []:
        copybook_text_parts.append(
            text_loader.read_uploaded_text(
                file,
            )
        )

    copybook_text = "\n".join(
        copybook_text_parts,
    )

    copybook_fields = copybook_parser.parse(
        copybook_text,
    )

    idms_cobol_text = ""
    idms_cobol_pdf_name = ""

    if idms_cobol_pdf_file is not None:
        idms_cobol_pdf_name = str(
            idms_cobol_pdf_file.name or "",
        )

        idms_cobol_text = text_loader.read_uploaded_text(
            idms_cobol_pdf_file,
        )

    st.session_state.sheet_mapping_rows = sheet_rows
    st.session_state.dclgen_columns = dclgen_columns
    st.session_state.copybook_fields = copybook_fields
    st.session_state.idms_cobol_text = idms_cobol_text
    st.session_state.idms_cobol_pdf_name = idms_cobol_pdf_name
    st.session_state.converted_cobol = ""
    st.session_state.converted_cobol_file_name = "converted_db2_cobol.cbl"
    st.session_state.validation_messages = []
    st.session_state.operations = []
    st.session_state.generated = False
    st.session_state.loaded = True

    st.success("Inputs loaded and analyzed.")


def generate_cobol(
    target_program_id: str,
) -> None:
    service = ConversionService()

    result = service.convert(
        ConversionInput(
            sheet_mapping_rows=st.session_state.sheet_mapping_rows,
            dclgen_columns=st.session_state.dclgen_columns,
            copybook_fields=st.session_state.copybook_fields,
            idms_cobol_text=st.session_state.idms_cobol_text,
            target_program_id=target_program_id,
        )
    )

    st.session_state.converted_cobol = result.converted_cobol
    st.session_state.validation_messages = result.validation_messages
    st.session_state.operations = result.operations
    st.session_state.converted_cobol_file_name = build_converted_cobol_file_name(
        target_program_id=target_program_id,
        source_pdf_name=st.session_state.idms_cobol_pdf_name,
    )
    st.session_state.generated = True


def build_converted_cobol_file_name(
    target_program_id: str,
    source_pdf_name: str,
) -> str:
    target = sanitize_file_stem(
        target_program_id,
    )

    if target:
        return f"{target}.cbl"

    source_stem = ""

    if source_pdf_name:
        source_stem = Path(
            source_pdf_name,
        ).stem

    source_stem = sanitize_file_stem(
        source_stem,
    )

    if source_stem:
        return f"{source_stem}_db2.cbl"

    return "converted_db2_cobol.cbl"


def sanitize_file_stem(
    value: str,
) -> str:
    text = str(
        value or "",
    ).strip()

    if not text:
        return ""

    text = Path(
        text,
    ).stem

    text = text.upper()
    text = re.sub(
        r"[^A-Z0-9_-]+",
        "_",
        text,
    )
    text = re.sub(
        r"_+",
        "_",
        text,
    )

    return text.strip(
        "_-",
    )


def render_download_generated_cobol_button() -> None:
    st.download_button(
        label="Download Generated DB2 COBOL",
        data=st.session_state.converted_cobol.encode(
            "utf-8",
        ),
        file_name=st.session_state.converted_cobol_file_name,
        mime="text/plain",
        use_container_width=True,
    )


def render_current_status() -> None:
    st.markdown("## Current Input Status")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            "Sheet Mapping Rows",
            len(st.session_state.sheet_mapping_rows),
        )

    with col2:
        st.metric(
            "DCLGEN Columns",
            len(st.session_state.dclgen_columns),
        )

    with col3:
        st.metric(
            "Copybook Fields",
            len(st.session_state.copybook_fields),
        )

    with col4:
        cobol_loaded = "Yes" if st.session_state.idms_cobol_text.strip() else "No"

        st.metric(
            "COBOL PDF Loaded",
            cobol_loaded,
        )


def render_metadata_overview_tab() -> None:
    st.markdown("## Metadata Overview")

    metadata_service = MetadataService()

    record_summaries = metadata_service.record_summaries(
        st.session_state.sheet_mapping_rows,
    )

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            "Total Records",
            len(record_summaries),
        )

    with col2:
        st.metric(
            "Total Sheet Mapping Rows",
            len(st.session_state.sheet_mapping_rows),
        )

    with col3:
        st.metric(
            "Total DCLGEN Columns",
            len(st.session_state.dclgen_columns),
        )

    with col4:
        st.metric(
            "Copybook Fields",
            len(st.session_state.copybook_fields),
        )

    st.markdown("### Records")

    if record_summaries:
        st.dataframe(
            [
                {
                    "IDMS Record": item.record_name,
                    "DB2 Table": item.db2_table,
                    "Column Count": item.column_count,
                    "Key Columns": ", ".join(item.key_columns),
                }
                for item in record_summaries
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No record summary available. Load Sheet Mapping first.")


def render_column_names_tab() -> None:
    st.markdown("## Column Names")

    metadata_service = MetadataService()

    rows = metadata_service.column_rows(
        st.session_state.sheet_mapping_rows,
    )

    if rows:
        st.dataframe(
            rows,
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No column names available. Load Sheet Mapping first.")

    st.markdown("### DCLGEN Columns")

    dclgen_rows = [
        {
            "Table": item.table_name,
            "Column": item.column_name,
            "DB2 Type": item.db2_type,
            "COBOL Host": item.cobol_host_name,
            "COBOL PIC": item.cobol_picture,
        }
        for item in st.session_state.dclgen_columns
    ]

    if dclgen_rows:
        st.dataframe(
            dclgen_rows,
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No DCLGEN columns available.")

    st.markdown("### Optional Copybook Fields")

    copybook_rows = [
        {
            "Level": item.level,
            "Name": item.name,
            "PIC": item.picture,
            "Usage": item.usage,
            "Occurs": item.occurs,
        }
        for item in st.session_state.copybook_fields
    ]

    if copybook_rows:
        st.dataframe(
            copybook_rows,
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No copybook fields loaded. Copybook upload is optional.")


def render_sets_tab() -> None:
    st.markdown("## Sets and Relationships")

    metadata_service = MetadataService()

    relationships = metadata_service.relationship_summaries(
        st.session_state.sheet_mapping_rows,
    )

    if relationships:
        st.dataframe(
            [
                {
                    "Relation / Set": item.relation,
                    "Parent Record": item.parent_record,
                    "Child Record": item.child_record,
                    "Parent Key": item.parent_key,
                    "Child Key": item.child_key,
                }
                for item in relationships
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No set or relationship rows found in Sheet Mapping.")

    st.markdown("### IDMS Operations Found During Generation")

    if st.session_state.operations:
        st.dataframe(
            [
                {
                    "Line": item.line_number,
                    "Operation": item.operation,
                    "Record": item.record_name,
                    "Set": item.set_name,
                    "Source": item.raw_line,
                }
                for item in st.session_state.operations
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("Generate DB2 COBOL to see parsed IDMS operations.")


def render_sheet_mapping_rows_tab() -> None:
    st.markdown("## Sheet Mapping Rows")

    metadata_service = MetadataService()

    rows = metadata_service.mapping_preview_rows(
        st.session_state.sheet_mapping_rows,
    )

    if rows:
        st.dataframe(
            rows,
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No Sheet Mapping rows available. Upload Excel or CSV first.")


def render_generated_cobol_tab() -> None:
    st.markdown("## Generated DB2 COBOL")

    if not st.session_state.converted_cobol:
        st.info("Generate DB2 COBOL from the Main tab.")
        return

    st.caption(
        f"Download file name: `{st.session_state.converted_cobol_file_name}`"
    )

    render_download_generated_cobol_button()

    st.text_area(
        "Final DB2 COBOL Code",
        value=st.session_state.converted_cobol,
        height=760,
    )


def render_validation_tab() -> None:
    st.markdown("## Validation")

    if not st.session_state.validation_messages:
        st.success("No validation messages.")
        return

    for message in st.session_state.validation_messages:
        st.warning(
            message,
        )