from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------
# Project path setup
# ---------------------------------------------------------------------
# This file is located at:
# C:\VSCode\IDMS-DB2-Code-Conversion\src\idms_db2_phase2\testing\run_retrieval.py
#
# Required import root:
# C:\VSCode\IDMS-DB2-Code-Conversion\src
# ---------------------------------------------------------------------

CURRENT_FILE = Path(__file__).resolve()
SRC_DIR = CURRENT_FILE.parents[2]
PROJECT_ROOT = CURRENT_FILE.parents[3]

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


from idms_db2_phase2.domain.models import ConversionInput
from idms_db2_phase2.parsers.copybook_parser import CopybookParser
from idms_db2_phase2.parsers.dclgen_parser import DclgenParser
from idms_db2_phase2.parsers.sheet_mapping_parser import SheetMappingParser
from idms_db2_phase2.parsers.text_loader import TextLoader
from idms_db2_phase2.services.conversion_service import ConversionService


# ---------------------------------------------------------------------
# Hardcoded input/output paths
# ---------------------------------------------------------------------

INPUT_DIR = Path(r"C:\S\S-Input")
OUTPUT_DIR = Path(r"C:\S\S-Input\Output")

SHEET_MAPPING_PATH = INPUT_DIR / "Excel_Sheet_mapping.csv"

DCLGEN_PATHS = [
    INPUT_DIR / "DCLGENs_BEFF.txt",
    INPUT_DIR / "DCLGENs_BFAR.txt",
    INPUT_DIR / "DCLGENs_EVEF.txt",
]

# Copybook is optional.
# No copybook is provided for this run, so keep this empty.
COPYBOOK_PATHS = []

IDMS_COBOL_SOURCE_PATH = INPUT_DIR / "Retrieval.txt"

TARGET_PROGRAM_ID = "VMDZ4420"

AUTO_FIX_PIC_LENGTH_MISMATCHES = False


# ---------------------------------------------------------------------
# Output file name: code_name_date_time.cbl
# Example: Retrieval_12-08-2026_153045.cbl
# ---------------------------------------------------------------------

CODE_NAME = IDMS_COBOL_SOURCE_PATH.stem
DATE_TIME = datetime.now().strftime("%d-%m-%Y_%H%M%S")
OUTPUT_COBOL_PATH = OUTPUT_DIR / f"{CODE_NAME}_{DATE_TIME}.cbl"


# ---------------------------------------------------------------------
# Local uploaded file wrapper
# Existing parser flow expects uploaded-file-like objects with:
# - .name
# - .getvalue()
# ---------------------------------------------------------------------

class LocalUploadedFile:
    def __init__(self, file_path: Path) -> None:
        self.file_path = Path(file_path)
        self.name = self.file_path.name

    def getvalue(self) -> bytes:
        return self.file_path.read_bytes()


# ---------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------

def validate_file_exists(file_path: Path, label: str) -> None:
    if not file_path.exists():
        raise FileNotFoundError(f"{label} file not found: {file_path}")

    if not file_path.is_file():
        raise FileNotFoundError(f"{label} path is not a file: {file_path}")


def validate_inputs() -> None:
    validate_file_exists(SHEET_MAPPING_PATH, "Sheet Mapping")
    validate_file_exists(IDMS_COBOL_SOURCE_PATH, "IDMS COBOL Source")

    if not DCLGEN_PATHS:
        raise ValueError("At least one DCLGEN file path is required.")

    for index, dclgen_path in enumerate(DCLGEN_PATHS, start=1):
        validate_file_exists(dclgen_path, f"DCLGEN {index}")

    for index, copybook_path in enumerate(COPYBOOK_PATHS, start=1):
        validate_file_exists(copybook_path, f"Copybook {index}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------
# Load inputs
# ---------------------------------------------------------------------

def load_inputs() -> tuple[list, list, list, str, list[str]]:
    diagnostics: list[str] = []

    sheet_parser = SheetMappingParser()
    dclgen_parser = DclgenParser()
    copybook_parser = CopybookParser()
    text_loader = TextLoader()

    diagnostics.append("START LOAD INPUTS")

    # -------------------------------------------------------------
    # Sheet Mapping
    # -------------------------------------------------------------

    sheet_file = LocalUploadedFile(SHEET_MAPPING_PATH)

    sheet_rows = sheet_parser.parse_uploaded_file(
        sheet_file,
    )

    diagnostics.append(f"Sheet Mapping file: {SHEET_MAPPING_PATH}")
    diagnostics.append(f"Sheet Mapping parsed rows: {len(sheet_rows)}")
    diagnostics.extend(sheet_parser.diagnostics)

    # -------------------------------------------------------------
    # DCLGEN files
    # -------------------------------------------------------------

    dclgen_texts: list[str] = []

    for dclgen_path in DCLGEN_PATHS:
        dclgen_file = LocalUploadedFile(dclgen_path)

        dclgen_text = text_loader.read_uploaded_text(
            dclgen_file,
        )

        diagnostics.append(f"DCLGEN file: {dclgen_path}")
        diagnostics.append(f"DCLGEN text length: {len(dclgen_text)}")

        dclgen_texts.append(dclgen_text)

    dclgen_columns = dclgen_parser.parse_many_texts(
        dclgen_texts,
    )

    diagnostics.append(f"DCLGEN parsed columns: {len(dclgen_columns)}")
    diagnostics.extend(dclgen_parser.diagnostics)

    # -------------------------------------------------------------
    # Copybook files - optional
    # -------------------------------------------------------------

    copybook_text_parts: list[str] = []

    for copybook_path in COPYBOOK_PATHS:
        copybook_file = LocalUploadedFile(copybook_path)

        copybook_text = text_loader.read_uploaded_text(
            copybook_file,
        )

        diagnostics.append(f"Copybook file: {copybook_path}")
        diagnostics.append(f"Copybook text length: {len(copybook_text)}")

        copybook_text_parts.append(copybook_text)

    copybook_text = "\n".join(copybook_text_parts)

    copybook_fields = copybook_parser.parse(
        copybook_text,
    )

    diagnostics.append(f"Copybook parsed fields: {len(copybook_fields)}")

    # -------------------------------------------------------------
    # IDMS COBOL source
    # -------------------------------------------------------------

    source_file = LocalUploadedFile(IDMS_COBOL_SOURCE_PATH)

    idms_cobol_text = text_loader.read_uploaded_text(
        source_file,
    )

    diagnostics.append(f"IDMS COBOL source file: {IDMS_COBOL_SOURCE_PATH}")
    diagnostics.append(f"IDMS COBOL source text length: {len(idms_cobol_text)}")

    return (
        sheet_rows,
        dclgen_columns,
        copybook_fields,
        idms_cobol_text,
        diagnostics,
    )


# ---------------------------------------------------------------------
# Run conversion
# ---------------------------------------------------------------------

def run_conversion() -> None:
    validate_inputs()

    (
        sheet_rows,
        dclgen_columns,
        copybook_fields,
        idms_cobol_text,
        diagnostics,
    ) = load_inputs()

    service = ConversionService()

    result = service.convert(
        ConversionInput(
            sheet_mapping_rows=sheet_rows,
            dclgen_columns=dclgen_columns,
            copybook_fields=copybook_fields,
            idms_cobol_text=idms_cobol_text,
            target_program_id=TARGET_PROGRAM_ID,
            auto_fix_pic_length_mismatches=AUTO_FIX_PIC_LENGTH_MISMATCHES,
        )
    )

    OUTPUT_COBOL_PATH.write_text(
        result.converted_cobol or "",
        encoding="utf-8",
    )

    print("DB2 COBOL generation completed.")
    print(f"Output file created: {OUTPUT_COBOL_PATH}")

    print("")
    print("Input Summary")
    print("-------------")
    print(f"Project Root       : {PROJECT_ROOT}")
    print(f"SRC Directory      : {SRC_DIR}")
    print(f"Sheet Mapping Rows : {len(sheet_rows)}")
    print(f"DCLGEN Columns     : {len(dclgen_columns)}")
    print(f"Copybook Fields    : {len(copybook_fields)}")
    print(f"COBOL Text Length  : {len(idms_cobol_text)}")
    print(f"Target PROGRAM-ID  : {TARGET_PROGRAM_ID}")

    print("")
    print("Validation Messages")
    print("-------------------")

    if result.validation_messages:
        for message in result.validation_messages:
            print(f"- {message}")
    else:
        print("No validation messages.")

    print("")
    print("Diagnostics")
    print("-----------")

    for diagnostic in diagnostics:
        print(f"- {diagnostic}")


if __name__ == "__main__":
    run_conversion()