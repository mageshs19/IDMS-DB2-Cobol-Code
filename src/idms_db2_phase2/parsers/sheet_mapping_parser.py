import csv
from io import BytesIO
from io import StringIO

from openpyxl import load_workbook

from idms_db2_phase2.domain.models import SheetMappingRow


class SheetMappingParser:
    COLUMNS = [
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
        "Relation",
        "Reference Field Name (CopyBook) ",
        "Reference Field PIC Clause",
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
        ],
        "Cobol Zone": [
            "Cobol Zone",
            "COBOL Zone",
        ],
        "IDMS Key": [
            "IDMS Key",
            "IDMS KEY",
        ],
        "IDMS PIC Clause": [
            "IDMS PIC Clause",
            "PIC Clause",
            "IDMS PIC",
        ],
        "Length of Field Bytes": [
            "Length of Field Bytes",
            "Length",
            "Field Length",
        ],
        "Field end position": [
            "Field end position",
            "End Position",
        ],
        "DB2 Key": [
            "DB2 Key",
            "DB2 KEY",
        ],
        "New DB2 Record": [
            "New DB2 Record",
            "DB2 Record",
            "DB2 Table",
            "New DB2 Table",
        ],
        "New DB2 Field name": [
            "New DB2 Field name",
            "DB2 Field",
            "DB2 Column",
            "New DB2 Column",
        ],
        "New DB2 Data Type": [
            "New DB2 Data Type",
            "DB2 Data Type",
            "DB2 Type",
        ],
        "Hopex Expression TypeRemark": [
            "Hopex Expression TypeRemark",
            "Expression Type",
            "Remark",
        ],
        "Relation": [
            "Relation",
            "Set",
            "Relationship",
        ],
        "Reference Field Name (CopyBook) ": [
            "Reference Field Name (CopyBook) ",
            "Reference Field Name (CopyBook)",
            "CopyBook Field",
            "Copybook Field",
            "Reference Field",
        ],
        "Reference Field PIC Clause": [
            "Reference Field PIC Clause",
            "Reference PIC",
        ],
        "Cross Application DB2 Field Name": [
            "Cross Application DB2 Field Name",
            "Cross App DB2 Field",
        ],
        "Cross Appln DB2 Data Type": [
            "Cross Appln DB2 Data Type",
            "Cross App DB2 Type",
        ],
        "Basetype": [
            "Basetype",
            "Base Type",
        ],
    }

    def parse_uploaded_file(
        self,
        uploaded_file,
    ) -> list[SheetMappingRow]:
        if uploaded_file is None:
            return []

        file_name = str(uploaded_file.name or "").lower()
        raw_bytes = uploaded_file.getvalue()

        if file_name.endswith(".xlsx"):
            return self.parse_xlsx_bytes(
                raw_bytes,
            )

        text = raw_bytes.decode(
            "utf-8",
            errors="ignore",
        )

        return self.parse_csv_text(
            text,
        )

    def parse_csv_text(
        self,
        text: str,
    ) -> list[SheetMappingRow]:
        if not text or not text.strip():
            return []

        reader = csv.DictReader(
            StringIO(text),
        )

        rows: list[SheetMappingRow] = []

        for raw_row in reader:
            mapping_row = self._to_mapping_row(
                raw_row,
            )

            if self._has_useful_content(
                mapping_row,
            ):
                rows.append(
                    mapping_row,
                )

        return rows

    def parse_xlsx_bytes(
        self,
        raw_bytes: bytes,
    ) -> list[SheetMappingRow]:
        if not raw_bytes:
            return []

        workbook = load_workbook(
            filename=BytesIO(raw_bytes),
            data_only=True,
        )

        sheet = workbook.active

        raw_rows = list(
            sheet.iter_rows(
                values_only=True,
            )
        )

        if not raw_rows:
            return []

        header_index = self._find_header_row(
            raw_rows,
        )

        if header_index < 0:
            return []

        headers = [
            str(value).strip() if value is not None else ""
            for value in raw_rows[header_index]
        ]

        output: list[SheetMappingRow] = []

        for row in raw_rows[header_index + 1:]:
            raw_row: dict[str, str] = {}

            for index, header in enumerate(headers):
                if not header:
                    continue

                value = row[index] if index < len(row) else ""

                raw_row[header] = "" if value is None else str(value).strip()

            mapping_row = self._to_mapping_row(
                raw_row,
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
    ) -> int:
        for index, row in enumerate(rows[:30]):
            normalized_cells = {
                str(value).strip().upper()
                for value in row
                if value is not None
            }

            if "COBOL RECORD IDMS" in normalized_cells:
                return index

            if (
                "NEW DB2 RECORD" in normalized_cells
                and "NEW DB2 FIELD NAME" in normalized_cells
            ):
                return index

            if (
                "DB2 RECORD" in normalized_cells
                and "DB2 COLUMN" in normalized_cells
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

        for alias in aliases:
            if alias in row:
                return str(
                    row.get(alias) or "",
                ).strip()

        upper_map = {
            str(key).strip().upper(): value
            for key, value in row.items()
        }

        for alias in aliases:
            value = upper_map.get(
                alias.strip().upper(),
            )

            if value is not None:
                return str(
                    value,
                ).strip()

        return ""

    def _has_useful_content(
        self,
        row: SheetMappingRow,
    ) -> bool:
        return bool(
            row.cobol_record_idms
            or row.new_db2_record
            or row.new_db2_field_name
            or row.relation
        )