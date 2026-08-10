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
        "idms_cobol_source_name": "",
        "converted_cobol": "",
        "converted_cobol_file_name": "converted_db2_cobol.cbl",
        "validation_messages": [],
        "operations": [],
        "generated": False,
        "loaded": False,
        "diagnostics": [],
        "uploaded_file_names": {},
        "auto_fix_pic_length_mismatches": False,
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
            "Diagnostics",
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

    with tabs[7]:
        render_diagnostics_tab()


def render_main_tab() -> None:
    st.markdown("## Upload Inputs")

    st.info(
        "Upload Sheet Mapping, one or more DCLGEN files, optional Copybook files, "
        "and the IDMS COBOL source text file to generate DB2 embedded SQL COBOL."
    )

    col1, col2 = st.columns(2)

    with col1:
        sheet_mapping_file = st.file_uploader(
            "Sheet Mapping Excel or CSV",
            type=["xlsx", "csv"],
            key="sheet_mapping_file",
            help=(
                "Upload Sheet Mapping as .xlsx or .csv. "
                "If your file is .xls, save it as .xlsx or .csv first."
            ),
        )

        dclgen_files = st.file_uploader(
            "DCLGEN Files",
            type=None,
            accept_multiple_files=True,
            key="dclgen_files",
            help=(
                "Upload one or more DCLGEN files. Any extension is accepted "
                "because production DCLGEN files may not use .txt/.cpy."
            ),
        )

    with col2:
        copybook_files = st.file_uploader(
            "Optional Copybook Files",
            type=None,
            accept_multiple_files=True,
            key="copybook_files",
            help="Optional. Upload one or more copybook text files if available.",
        )

        idms_cobol_source_file = st.file_uploader(
            "IDMS COBOL Source File",
            type=None,
            key="idms_cobol_source_file",
            help=(
                "Upload the source IDMS COBOL program as text/source file. "
                "Examples: .txt, .cbl, .cob, .cpy."
            ),
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

    auto_fix_pic_length_mismatches = st.checkbox(
        "Auto-fix output PIC length mismatches",
        value=st.session_state.auto_fix_pic_length_mismatches,
        key="auto_fix_pic_length_mismatches_checkbox",
        help=(
            "When enabled, the converter expands target numeric PIC lengths "
            "when a MOVE source has more digits than the output target."
        ),
    )

    st.session_state.auto_fix_pic_length_mismatches = auto_fix_pic_length_mismatches

    st.caption(
        "Example: if WS field PIC 9(8) is moved to output field PIC 9(6), "
        "the output field can be expanded to PIC 9(8)."
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
            idms_cobol_source_file=idms_cobol_source_file,
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
                idms_cobol_source_file=idms_cobol_source_file,
            )

        generate_cobol(
            target_program_id=target_program_id,
            auto_fix_pic_length_mismatches=auto_fix_pic_length_mismatches,
        )

    if st.session_state.generated:
        st.success("DB2 COBOL generation completed.")

        if st.session_state.converted_cobol:
            render_download_generated_cobol_button(
                button_key="download_generated_cobol_main",
            )

    render_current_status()


def load_inputs(
    sheet_mapping_file,
    dclgen_files,
    copybook_files,
    idms_cobol_source_file,
) -> None:
    diagnostics: list[str] = []
    uploaded_file_names: dict[str, list[str] | str] = {}

    sheet_parser = SheetMappingParser()
    dclgen_parser = DclgenParser()
    copybook_parser = CopybookParser()
    text_loader = TextLoader()

    diagnostics.append("START LOAD INPUTS")

    sheet_rows = []

    if sheet_mapping_file is None:
        diagnostics.append("Sheet Mapping file not uploaded.")
    else:
        uploaded_file_names["sheet_mapping"] = str(sheet_mapping_file.name or "")
        diagnostics.append(f"Sheet Mapping uploaded: {sheet_mapping_file.name}")
        diagnostics.append(f"Sheet Mapping size bytes: {len(sheet_mapping_file.getvalue())}")

        try:
            sheet_rows = sheet_parser.parse_uploaded_file(
                sheet_mapping_file,
            )
            diagnostics.append(f"Sheet Mapping parsed rows: {len(sheet_rows)}")
            diagnostics.extend(sheet_parser.diagnostics)
        except Exception as exc:
            diagnostics.append(f"Sheet Mapping parse failed: {exc}")
            sheet_rows = []

    dclgen_texts: list[str] = []
    dclgen_file_names: list[str] = []

    for file in dclgen_files or []:
        file_name = str(file.name or "")
        dclgen_file_names.append(file_name)

        try:
            text = text_loader.read_uploaded_text(
                file,
            )
            diagnostics.append(f"DCLGEN uploaded: {file_name}")
            diagnostics.append(f"DCLGEN text length for {file_name}: {len(text)}")
            dclgen_texts.append(text)
        except Exception as exc:
            diagnostics.append(f"DCLGEN read failed for {file_name}: {exc}")

    uploaded_file_names["dclgen_files"] = dclgen_file_names

    dclgen_columns = []

    try:
        dclgen_columns = dclgen_parser.parse_many_texts(
            dclgen_texts,
        )
        diagnostics.append(f"DCLGEN parsed columns: {len(dclgen_columns)}")
        diagnostics.extend(dclgen_parser.diagnostics)
    except Exception as exc:
        diagnostics.append(f"DCLGEN parse failed: {exc}")
        dclgen_columns = []

    copybook_text_parts: list[str] = []
    copybook_file_names: list[str] = []

    for file in copybook_files or []:
        file_name = str(file.name or "")
        copybook_file_names.append(file_name)

        try:
            text = text_loader.read_uploaded_text(
                file,
            )
            diagnostics.append(f"Copybook uploaded: {file_name}")
            diagnostics.append(f"Copybook text length for {file_name}: {len(text)}")
            copybook_text_parts.append(text)
        except Exception as exc:
            diagnostics.append(f"Copybook read failed for {file_name}: {exc}")

    uploaded_file_names["copybook_files"] = copybook_file_names

    copybook_text = "\n".join(
        copybook_text_parts,
    )

    copybook_fields = []

    try:
        copybook_fields = copybook_parser.parse(
            copybook_text,
        )
        diagnostics.append(f"Copybook parsed fields: {len(copybook_fields)}")
    except Exception as exc:
        diagnostics.append(f"Copybook parse failed: {exc}")
        copybook_fields = []

    idms_cobol_text = ""
    idms_cobol_source_name = ""

    if idms_cobol_source_file is None:
        diagnostics.append("IDMS COBOL source file not uploaded.")
    else:
        idms_cobol_source_name = str(
            idms_cobol_source_file.name or "",
        )
        uploaded_file_names["idms_cobol_source_file"] = idms_cobol_source_name

        try:
            idms_cobol_text = text_loader.read_uploaded_text(
                idms_cobol_source_file,
            )
            diagnostics.append(f"IDMS COBOL source uploaded: {idms_cobol_source_name}")
            diagnostics.append(f"IDMS COBOL source text length: {len(idms_cobol_text)}")
        except Exception as exc:
            diagnostics.append(f"IDMS COBOL source read failed: {exc}")
            idms_cobol_text = ""

    st.session_state.sheet_mapping_rows = sheet_rows
    st.session_state.dclgen_columns = dclgen_columns
    st.session_state.copybook_fields = copybook_fields
    st.session_state.idms_cobol_text = idms_cobol_text
    st.session_state.idms_cobol_source_name = idms_cobol_source_name
    st.session_state.converted_cobol = ""
    st.session_state.converted_cobol_file_name = "converted_db2_cobol.cbl"
    st.session_state.validation_messages = []
    st.session_state.operations = []
    st.session_state.generated = False
    st.session_state.loaded = True
    st.session_state.diagnostics = diagnostics
    st.session_state.uploaded_file_names = uploaded_file_names

    st.success("Inputs loaded and analyzed.")

    if not sheet_rows:
        st.warning(
            "Sheet Mapping parsed as 0 rows. Open the Diagnostics tab to see file, sheet, and header detection details."
        )

    if dclgen_files and not dclgen_columns:
        st.warning(
            "DCLGEN files were uploaded but parsed as 0 columns. Open the Diagnostics tab to review detected table and field parsing."
        )

    if idms_cobol_source_file is not None and not idms_cobol_text.strip():
        st.warning(
            "IDMS COBOL source file was uploaded but no text was read. Open the Diagnostics tab for details."
        )


def generate_cobol(
    target_program_id: str,
    auto_fix_pic_length_mismatches: bool,
) -> None:
    service = ConversionService()

    result = service.convert(
        ConversionInput(
            sheet_mapping_rows=st.session_state.sheet_mapping_rows,
            dclgen_columns=st.session_state.dclgen_columns,
            copybook_fields=st.session_state.copybook_fields,
            idms_cobol_text=st.session_state.idms_cobol_text,
            target_program_id=target_program_id,
            auto_fix_pic_length_mismatches=auto_fix_pic_length_mismatches,
        )
    )

    st.session_state.converted_cobol = result.converted_cobol
    st.session_state.validation_messages = result.validation_messages
    st.session_state.operations = result.operations
    st.session_state.converted_cobol_file_name = build_converted_cobol_file_name(
        target_program_id=target_program_id,
        source_file_name=st.session_state.idms_cobol_source_name,
    )
    st.session_state.generated = True


def build_converted_cobol_file_name(
    target_program_id: str,
    source_file_name: str,
) -> str:
    target = sanitize_file_stem(
        target_program_id,
    )

    if target:
        return f"{target}.cbl"

    source_stem = ""

    if source_file_name:
        source_stem = Path(
            source_file_name,
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


def render_download_generated_cobol_button(
    button_key: str,
) -> None:
    st.download_button(
        label="Download Generated DB2 COBOL",
        data=st.session_state.converted_cobol.encode(
            "utf-8",
        ),
        file_name=st.session_state.converted_cobol_file_name,
        mime="text/plain",
        key=button_key,
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
            "COBOL Source Loaded",
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
            "COBOL Usage": getattr(item, "cobol_usage", ""),
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

    render_download_generated_cobol_button(
        button_key="download_generated_cobol_generated_tab",
    )

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


def render_diagnostics_tab() -> None:
    st.markdown("## Diagnostics")

    st.markdown("### Uploaded Files")

    st.json(
        st.session_state.uploaded_file_names,
    )

    st.markdown("### Parser Diagnostics")

    diagnostics = st.session_state.diagnostics or []

    if not diagnostics:
        st.info("No diagnostics available. Click Load and Analyze Inputs first.")
        return

    st.code(
        "\n".join(
            diagnostics,
        ),
        language="text",
    )