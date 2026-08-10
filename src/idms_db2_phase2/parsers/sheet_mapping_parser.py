import csv
import re
from io import BytesIO
from io import StringIO

from openpyxl import load_workbook

from idms_db2_phase2.domain.models import SheetMappingRow


class SheetMappingParser:
    COLUMNS = [
        "IDMS to DB2 Mapping",
        "Cobol Record IDMS",
        "Cobol Zone",
        "IDMS Key",
        "IDMS PIC Clause",
        "Length of Field Bytes",
        "Field end position",
        "DB2 Key",
        "New DB2 Record",
        "New DB2 Field name",
        "New DB2 Data Type",
        "Hopex Expression TypeRemark",
        "Remarks",
        "Relation",
        "Reference Field Name (CopyBook) ",
        "Reference Field PIC Clause",
        "Cross Application DB2 table",
        "Cross Application DB2 Field Name",
        "Cross Appln DB2 Data Type",
        "Basetype",
    ]

    FIELD_ALIASES = {
        "Cobol Record IDMS": [
            "Cobol Record IDMS",
            "COBOL RECORD IDMS",
            "Cobol Record",
            "IDMS Record",
            "Record IDMS",
            "Cobol Record Name",
            "IDMS to DB2 Mapping",
        ],
        "Cobol Zone": [
            "Cobol Zone",
            "COBOL Zone",
            "Cobol Field",
            "IDMS Field",
            "COBOL Field",
            "IDMS COBOL Zone",
        ],
        "IDMS Key": [
            "IDMS Key",
            "IDMS KEY",
            "Key IDMS",
        ],
        "IDMS PIC Clause": [
            "IDMS PIC Clause",
            "PIC Clause",
            "IDMS PIC",
            "Picture",
            "PIC",
        ],
        "Length of Field Bytes": [
            "Length of Field Bytes",
            "Length",
            "Field Length",
            "Length Bytes",
        ],
        "Field end position": [
            "Field end position",
            "Field End Position",
            "End Position",
            "Field End",
        ],
        "DB2 Key": [
            "DB2 Key",
            "DB2 key",
            "DB2 KEY",
            "Key DB2",
        ],
        "New DB2 Record": [
            "New DB2 Record",
            "DB2 Record",
            "DB2 Table",
            "New DB2 Table",
            "Table",
        ],
        "New DB2 Field name": [
            "New DB2 Field name",
            "New DB2 Field Name",
            "DB2 Field",
            "DB2 Column",
            "New DB2 Column",
            "Column",
        ],
        "New DB2 Data Type": [
            "New DB2 Data Type",
            "New DB2 DataType",
            "DB2 Data Type",
            "DB2 DataType",
            "DB2 Type",
            "Data Type",
            "DataType",
        ],
        "Hopex Expression TypeRemark": [
            "Hopex Expression TypeRemark",
            "Hopex Expression Type Remark",
            "Hopex Expression Type",
            "Expression Type",
            "Expression Type Remark",
        ],
        "Remarks": [
            "Remarks",
            "Remark",
            "Comments",
            "Comment",
        ],
        "Relation": [
            "Relation",
            "Set",
            "Relationship",
            "Set Name",
        ],
        "Reference Field Name (CopyBook) ": [
            "Reference Field Name (CopyBook) ",
            "Reference Field Name (CopyBook)",
            "Reference Field Name (Copybook)",
            "Reference Field Name CopyBook",
            "Reference Field Name Copybook",
            "CopyBook Field",
            "Copybook Field",
            "Reference Field",
        ],
        "Reference Field PIC Clause": [
            "Reference Field PIC Clause",
            "Reference PIC",
            "Reference Field PIC",
        ],
        "Cross Application DB2 table": [
            "Cross Application DB2 table",
            "Cross Application DB2 Table",
            "Cross App DB2 Table",
            "Cross Application Table",
            "Cross Application DB2 Record",
        ],
        "Cross Application DB2 Field Name": [
            "Cross Application DB2 Field Name",
            "Cross App DB2 Field",
            "Cross Application Field Name",
            "Cross Application DB2 Column",
        ],
        "Cross Appln DB2 Data Type": [
            "Cross Appln DB2 Data Type",
            "Cross Appln DB2 DataType",
            "Cross Application DB2 Data Type",
            "Cross Application DB2 DataType",
            "Cross App DB2 Type",
        ],
        "Basetype": [
            "Basetype",
            "Base Type",
            "BaseType",
        ],
    }

    def __init__(
        self,
    ) -> None:
        self.diagnostics: list[str] = []

    def parse_uploaded_file(
        self,
        uploaded_file,
    ) -> list[SheetMappingRow]:
        self.diagnostics = []

        if uploaded_file is None:
            self.diagnostics.append(
                "No Sheet Mapping file supplied."
            )
            return []

        file_name = str(
            uploaded_file.name or "",
        ).lower()

        raw_bytes = uploaded_file.getvalue()

        self.diagnostics.append(
            f"Sheet Mapping file name: {file_name}"
        )
        self.diagnostics.append(
            f"Sheet Mapping file size bytes: {len(raw_bytes)}"
        )

        if file_name.endswith(".xlsx"):
            return self.parse_xlsx_bytes(
                raw_bytes,
            )

        if file_name.endswith(".xls"):
            self.diagnostics.append(
                "Unsupported .xls file detected. Save the file as .xlsx or .csv."
            )
            return []

        text = raw_bytes.decode(
            "utf-8-sig",
            errors="ignore",
        )

        self.diagnostics.append(
            f"CSV/text decoded length: {len(text)}"
        )

        return self.parse_csv_text(
            text,
        )

    def parse_csv_text(
        self,
        text: str,
    ) -> list[SheetMappingRow]:
        if not text or not text.strip():
            self.diagnostics.append(
                "CSV/text content is empty."
            )
            return []

        cleaned_text = text.replace(
            "\ufeff",
            "",
        )

        sample = cleaned_text[:500].replace(
            "\n",
            "\\n",
        )

        self.diagnostics.append(
            f"CSV/text sample: {sample}"
        )

        reader = csv.DictReader(
            StringIO(cleaned_text),
        )

        if not reader.fieldnames:
            self.diagnostics.append(
                "CSV header not detected."
            )
            return []

        self.diagnostics.append(
            f"CSV detected headers: {reader.fieldnames}"
        )

        rows: list[SheetMappingRow] = []

        for raw_row in reader:
            normalized_raw_row = self._normalize_raw_dict_keys(
                raw_row,
            )

            mapping_row = self._to_mapping_row(
                normalized_raw_row,
            )

            if self._has_useful_content(
                mapping_row,
            ):
                rows.append(
                    mapping_row,
                )

        self.diagnostics.append(
            f"CSV parsed useful rows: {len(rows)}"
        )

        return rows

    def parse_xlsx_bytes(
        self,
        raw_bytes: bytes,
    ) -> list[SheetMappingRow]:
        if not raw_bytes:
            self.diagnostics.append(
                "XLSX file bytes are empty."
            )
            return []

        workbook = load_workbook(
            filename=BytesIO(raw_bytes),
            data_only=True,
            read_only=True,
        )

        sheet_names = [
            sheet.title
            for sheet in workbook.worksheets
        ]

        self.diagnostics.append(
            f"Workbook sheets: {sheet_names}"
        )

        all_rows: list[SheetMappingRow] = []

        for sheet in workbook.worksheets:
            self.diagnostics.append(
                f"Reading sheet: {sheet.title}"
            )

            sheet_rows = self._parse_worksheet(
                sheet,
            )

            self.diagnostics.append(
                f"Sheet {sheet.title} parsed useful rows: {len(sheet_rows)}"
            )

            if sheet_rows:
                all_rows.extend(
                    sheet_rows,
                )

        self.diagnostics.append(
            f"Workbook total parsed rows: {len(all_rows)}"
        )

        return all_rows

    def _parse_worksheet(
        self,
        sheet,
    ) -> list[SheetMappingRow]:
        raw_rows = list(
            sheet.iter_rows(
                values_only=True,
            )
        )

        self.diagnostics.append(
            f"Sheet {sheet.title} raw row count: {len(raw_rows)}"
        )

        if not raw_rows:
            return []

        header_index = self._find_header_row(
            rows=raw_rows,
            sheet_title=sheet.title,
        )

        if header_index < 0:
            self.diagnostics.append(
                f"Sheet {sheet.title}: header row not found."
            )
            return []

        headers = [
            self._cell_to_string(
                value,
            )
            for value in raw_rows[header_index]
        ]

        self.diagnostics.append(
            f"Sheet {sheet.title}: header row index: {header_index}"
        )
        self.diagnostics.append(
            f"Sheet {sheet.title}: detected headers: {headers}"
        )

        output: list[SheetMappingRow] = []

        for row in raw_rows[header_index + 1:]:
            raw_row: dict[str, str] = {}

            for index, header in enumerate(headers):
                if not header:
                    continue

                value = row[index] if index < len(row) else ""

                raw_row[header] = self._cell_to_string(
                    value,
                )

            normalized_raw_row = self._normalize_raw_dict_keys(
                raw_row,
            )

            mapping_row = self._to_mapping_row(
                normalized_raw_row,
            )

            if self._has_useful_content(
                mapping_row,
            ):
                output.append(
                    mapping_row,
                )

        return output

    def _find_header_row(
        self,
        rows: list[tuple],
        sheet_title: str,
    ) -> int:
        for index, row in enumerate(rows[:100]):
            normalized_cells = {
                self._normalize_header(
                    self._cell_to_string(
                        value,
                    )
                )
                for value in row
                if value is not None
            }

            if index < 10:
                self.diagnostics.append(
                    f"Sheet {sheet_title}: row {index} normalized cells: {sorted(normalized_cells)}"
                )

            if self._normalize_header("Cobol Record IDMS") in normalized_cells:
                return index

            if self._normalize_header("IDMS to DB2 Mapping") in normalized_cells:
                return index

            if (
                self._normalize_header("New DB2 Record") in normalized_cells
                and self._normalize_header("New DB2 Field name") in normalized_cells
            ):
                return index

            if (
                self._normalize_header("DB2 Record") in normalized_cells
                and self._normalize_header("DB2 Column") in normalized_cells
            ):
                return index

            if (
                self._normalize_header("IDMS Record") in normalized_cells
                and self._normalize_header("DB2 Table") in normalized_cells
            ):
                return index

        return -1

    def _to_mapping_row(
        self,
        raw_row: dict[str, str],
    ) -> SheetMappingRow:
        return SheetMappingRow(
            cobol_record_idms=self._get(
                raw_row,
                "Cobol Record IDMS",
            ),
            cobol_zone=self._get(
                raw_row,
                "Cobol Zone",
            ),
            idms_key=self._get(
                raw_row,
                "IDMS Key",
            ),
            idms_pic_clause=self._get(
                raw_row,
                "IDMS PIC Clause",
            ),
            length_of_field_bytes=self._get(
                raw_row,
                "Length of Field Bytes",
            ),
            field_end_position=self._get(
                raw_row,
                "Field end position",
            ),
            db2_key=self._get(
                raw_row,
                "DB2 Key",
            ),
            new_db2_record=self._get(
                raw_row,
                "New DB2 Record",
            ),
            new_db2_field_name=self._get(
                raw_row,
                "New DB2 Field name",
            ),
            new_db2_data_type=self._get(
                raw_row,
                "New DB2 Data Type",
            ),
            hopex_expression_type_remark=self._get(
                raw_row,
                "Hopex Expression TypeRemark",
            ),
            remarks=self._get(
                raw_row,
                "Remarks",
            ),
            relation=self._get(
                raw_row,
                "Relation",
            ),
            reference_field_name_copybook=self._get(
                raw_row,
                "Reference Field Name (CopyBook) ",
            ),
            reference_field_pic_clause=self._get(
                raw_row,
                "Reference Field PIC Clause",
            ),
            cross_application_db2_table=self._get(
                raw_row,
                "Cross Application DB2 table",
            ),
            cross_application_db2_field_name=self._get(
                raw_row,
                "Cross Application DB2 Field Name",
            ),
            cross_application_db2_data_type=self._get(
                raw_row,
                "Cross Appln DB2 Data Type",
            ),
            basetype=self._get(
                raw_row,
                "Basetype",
            ),
        )

    def _get(
        self,
        row: dict[str, str],
        canonical_name: str,
    ) -> str:
        aliases = self.FIELD_ALIASES.get(
            canonical_name,
            [
                canonical_name,
            ],
        )

        normalized_lookup = {
            self._normalize_header(key): value
            for key, value in row.items()
        }

        for alias in aliases:
            normalized_alias = self._normalize_header(
                alias,
            )

            value = normalized_lookup.get(
                normalized_alias,
            )

            if value is not None:
                return str(
                    value,
                ).strip()

        return ""

    def _normalize_raw_dict_keys(
        self,
        row: dict,
    ) -> dict[str, str]:
        normalized: dict[str, str] = {}

        for key, value in row.items():
            clean_key = self._cell_to_string(
                key,
            )

            clean_value = self._cell_to_string(
                value,
            )

            if clean_key:
                normalized[clean_key] = clean_value

        return normalized

    def _cell_to_string(
        self,
        value,
    ) -> str:
        if value is None:
            return ""

        text = str(
            value,
        )

        text = text.replace(
            "\ufeff",
            "",
        )
        text = text.replace(
            "\u00a0",
            " ",
        )
        text = text.replace(
            "\r",
            " ",
        )
        text = text.replace(
            "\n",
            " ",
        )

        text = re.sub(
            r"\s+",
            " ",
            text,
        )

        return text.strip()

    def _normalize_header(
        self,
        value: str,
    ) -> str:
        text = self._cell_to_string(
            value,
        )

        text = text.upper()
        text = text.replace(
            "&",
            "AND",
        )

        text = re.sub(
            r"[^A-Z0-9]+",
            "",
            text,
        )

        return text.strip()

    def _has_useful_content(
        self,
        row: SheetMappingRow,
    ) -> bool:
        return bool(
            row.cobol_record_idms
            or row.cobol_zone
            or row.new_db2_record
            or row.new_db2_field_name
            or row.relation
            or row.db2_key
            or row.idms_key
            or row.cross_application_db2_table
            or row.cross_application_db2_field_name
        )