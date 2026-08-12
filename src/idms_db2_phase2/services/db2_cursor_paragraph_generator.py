import re

from idms_db2_phase2.domain.models import DclgenColumn, IdmsOperation, SheetMappingRow
from idms_db2_phase2.services.db2_infrastructure_generator import Db2MappingContext
from idms_db2_phase2.services.name_normalizer import NameNormalizer


class Db2CursorParagraphGenerator:
    """
    Generates DB2 cursor OPEN / FETCH / CLOSE paragraphs.

    Core rule:
    - Sheet Mapping is the authority for DB2 record/table names.
    - Sheet Mapping is the authority for DB2 column names.
    - DCLGEN is the authority for host variable names and PIC clauses.
    - Cursor names are derived from Sheet Mapping DB2 record/table names,
      not from IDMS set names.

    Example:
        New DB2 Record = DZBEFFTV
        Cursor Name    = DZBEFFC1

        Paragraphs:
            710-OPEN-DZBEFFC1
            720-FETCH-DZBEFFC1
            730-CLOSE-DZBEFFC1

        New DB2 Record = DZEVEFTV
        Cursor Name    = DZEVEFC1

        Paragraphs:
            810-OPEN-DZEVEFC1
            820-FETCH-DZEVEFC1
            830-CLOSE-DZEVEFC1
    """

    GENERATED_MARKER = "* DB2 GENERATED CURSOR OPEN FETCH CLOSE PARAGRAPHS"

    SQL_ERROR_PARAGRAPH_PATTERN = re.compile(
        r"^\s*(SQL-ERROR|SQLERROR)\.\s*$",
        flags=re.IGNORECASE | re.MULTILINE,
    )

    END_PROGRAM_PATTERN = re.compile(
        r"^\s*(?:\d{6}\s+)?END\s+PROGRAM\b.*$",
        flags=re.IGNORECASE | re.MULTILINE,
    )

    PROCEDURE_DIVISION_PATTERN = re.compile(
        r"^\s*PROCEDURE\s+DIVISION\b.*$",
        flags=re.IGNORECASE | re.MULTILINE,
    )

    def __init__(
        self,
        mapping_rows: list[SheetMappingRow],
        dclgen_columns: list[DclgenColumn],
        operations: list[IdmsOperation],
    ) -> None:
        self.mapping_rows = mapping_rows or []
        self.dclgen_columns = dclgen_columns or []
        self.operations = operations or []
        self.messages: list[str] = []

        self.context = Db2MappingContext(
            mapping_rows=self.mapping_rows,
            dclgen_columns=self.dclgen_columns,
        )

    def apply(
        self,
        cobol_text: str,
    ) -> tuple[str, list[str]]:
        self.messages = []

        if not cobol_text:
            return cobol_text, self.messages

        if self.GENERATED_MARKER in cobol_text:
            self.messages.append(
                "DB2 cursor paragraphs: generated cursor paragraph block already exists; not inserted again."
            )
            return cobol_text, self.messages

        cursor_specs = self._cursor_specs()

        if not cursor_specs:
            self.messages.append(
                "DB2 cursor paragraphs: no cursor operations found."
            )
            return cobol_text, self.messages

        sql_error_paragraph = self._detect_sql_error_paragraph(
            cobol_text,
        )

        updated_text = cobol_text

        for spec in cursor_specs:
            updated_text = self._replace_generated_cursor_calls(
                text=updated_text,
                spec=spec,
            )

        block = self._paragraph_block(
            cursor_specs=cursor_specs,
            sql_error_paragraph=sql_error_paragraph,
        )

        updated_text = self._insert_paragraph_block(
            text=updated_text,
            block=block,
        )

        self.messages.append(
            f"DB2 cursor paragraphs: generated {len(cursor_specs)} Sheet Mapping driven cursor paragraph set(s)."
        )

        for spec in cursor_specs:
            host_variables = list(
                spec.get(
                    "host_variables",
                    [],
                )
            )

            if not host_variables:
                self.messages.append(
                    "DB2 cursor paragraphs: no FETCH host variables resolved for "
                    f"cursor {spec['cursor_name']} record {spec['record_name']} table {spec['table_name']}."
                )

        return updated_text, self.messages

    def _cursor_specs(
        self,
    ) -> list[dict[str, object]]:
        specs: list[dict[str, object]] = []
        seen_keys: set[tuple[str, str, str]] = set()

        cursor_index = 0

        for operation in self.operations:
            operation_name = str(
                operation.operation or "",
            ).upper()

            if operation_name not in {
                "OBTAIN_FIRST",
                "OBTAIN_NEXT",
                "FIND_FIRST",
            }:
                continue

            set_name = NameNormalizer.normalize(
                operation.set_name,
            )
            record_name = NameNormalizer.normalize(
                operation.record_name,
            )

            if not record_name:
                continue

            table_name = self.context.best_table_for_record(
                record_name,
            )

            if not table_name:
                continue

            cursor_name = self._cursor_name_from_db2_record(
                table_name,
            )

            spec_key = (
                set_name,
                record_name,
                table_name,
            )

            if spec_key in seen_keys:
                continue

            seen_keys.add(
                spec_key,
            )

            paragraph_numbers = self._paragraph_numbers(
                cursor_index,
            )

            host_variables = self.context.host_variables_for_record(
                record_name=record_name,
                table_name=table_name,
            )

            host_variables = [
                self._normalize_host_reference(host)
                for host in host_variables
                if self._normalize_host_reference(host)
            ]

            old_cursor_name = self._legacy_cursor_name_from_set(
                set_name,
            )

            spec = {
                "set_name": set_name,
                "record_name": record_name,
                "table_name": table_name,
                "cursor_name": cursor_name,
                "old_cursor_name": old_cursor_name,
                "open_paragraph": f"{paragraph_numbers['open']}-OPEN-{cursor_name}",
                "fetch_paragraph": f"{paragraph_numbers['fetch']}-FETCH-{cursor_name}",
                "close_paragraph": f"{paragraph_numbers['close']}-CLOSE-{cursor_name}",
                "old_open_paragraph": f"OPEN-{old_cursor_name}",
                "old_fetch_paragraph": f"FETCH-{old_cursor_name}",
                "old_close_paragraph": f"CLOSE-{old_cursor_name}",
                "host_variables": host_variables,
            }

            specs.append(
                spec,
            )

            cursor_index += 1

        return specs

    def _paragraph_numbers(
        self,
        cursor_index: int,
    ) -> dict[str, int]:
        """
        Manual style:
        - First cursor:  710 / 720 / 730
        - Second cursor: 810 / 820 / 830
        - Third cursor:  910 / 920 / 930
        """

        base = 710 + (cursor_index * 100)

        return {
            "open": base,
            "fetch": base + 10,
            "close": base + 20,
        }

    def _cursor_name_from_db2_record(
        self,
        table_name: str,
    ) -> str:
        """
        Derives cursor name from Sheet Mapping DB2 record/table.

        Examples:
            DZBEFFTV -> DZBEFFC1
            DZEVEFTV -> DZEVEFC1
            DZBEFFTB -> DZBEFFC1
        """

        table = NameNormalizer.normalize(
            table_name,
        )

        if not table:
            return "DB2CURC1"

        if table.endswith("TV") or table.endswith("TB"):
            return NameNormalizer.to_cobol(
                table[:-2] + "C1",
            )

        if table.endswith("_TV") or table.endswith("_TB"):
            return NameNormalizer.to_cobol(
                table[:-3] + "_C1",
            )

        return NameNormalizer.to_cobol(
            table + "_C1",
        )

    def _legacy_cursor_name_from_set(
        self,
        set_name: str,
    ) -> str:
        normalized = NameNormalizer.normalize(
            set_name,
        )

        if not normalized:
            return "C-IDMS-SET"

        return "C-" + NameNormalizer.to_cobol(
            normalized,
        )

    def _replace_generated_cursor_calls(
        self,
        text: str,
        spec: dict[str, object],
    ) -> str:
        """
        Replaces previously generated cursor paragraph calls that were based on
        IDMS set names with manual-style paragraph names based on DB2 record names.

        Example:
            PERFORM OPEN-C-AR-VMBEFF1.
        becomes:
            PERFORM 710-OPEN-DZBEFFC1.
        """

        updated = text

        replacements = {
            str(spec.get("old_open_paragraph", "")): str(spec.get("open_paragraph", "")),
            str(spec.get("old_fetch_paragraph", "")): str(spec.get("fetch_paragraph", "")),
            str(spec.get("old_close_paragraph", "")): str(spec.get("close_paragraph", "")),
            str(spec.get("old_cursor_name", "")): str(spec.get("cursor_name", "")),
        }

        for old_value, new_value in replacements.items():
            if not old_value or not new_value or old_value == new_value:
                continue

            updated = re.sub(
                rf"\b{re.escape(old_value)}\b",
                new_value,
                updated,
                flags=re.IGNORECASE,
            )

        return updated

    def _paragraph_block(
        self,
        cursor_specs: list[dict[str, object]],
        sql_error_paragraph: str,
    ) -> str:
        lines: list[str] = []

        lines.extend(
            self._comment_block(
                "DB2 GENERATED CURSOR OPEN FETCH CLOSE PARAGRAPHS",
            )
        )

        lines.append("")

        for spec in cursor_specs:
            cursor_name = str(
                spec["cursor_name"],
            )
            open_paragraph = str(
                spec["open_paragraph"],
            )
            fetch_paragraph = str(
                spec["fetch_paragraph"],
            )
            close_paragraph = str(
                spec["close_paragraph"],
            )
            host_variables = list(
                spec.get(
                    "host_variables",
                    [],
                )
            )

            lines.extend(
                self._open_paragraph(
                    cursor_name=cursor_name,
                    paragraph_name=open_paragraph,
                    sql_error_paragraph=sql_error_paragraph,
                )
            )

            lines.append("")

            lines.extend(
                self._fetch_paragraph(
                    cursor_name=cursor_name,
                    paragraph_name=fetch_paragraph,
                    host_variables=host_variables,
                    sql_error_paragraph=sql_error_paragraph,
                )
            )

            lines.append("")

            lines.extend(
                self._close_paragraph(
                    cursor_name=cursor_name,
                    paragraph_name=close_paragraph,
                    sql_error_paragraph=sql_error_paragraph,
                )
            )

            lines.append("")

        return "\n".join(
            lines,
        ).rstrip() + "\n"

    def _open_paragraph(
        self,
        cursor_name: str,
        paragraph_name: str,
        sql_error_paragraph: str,
    ) -> list[str]:
        return [
            f"{paragraph_name}.",
            f"MOVE '{paragraph_name}' TO SQL-LOCATION.",
            "EXEC SQL",
            f"    OPEN {cursor_name}",
            "END-EXEC.",
            "EVALUATE SQLCODE",
            "WHEN ZERO",
            f"    SET {cursor_name}-NOT-EOC TO TRUE",
            "WHEN OTHER",
            f"    DISPLAY 'ERROR WHILE OPENING CURSOR {cursor_name}'.",
            f"    PERFORM {sql_error_paragraph}.",
            "END-EVALUATE.",
        ]

    def _fetch_paragraph(
        self,
        cursor_name: str,
        paragraph_name: str,
        host_variables: list[str],
        sql_error_paragraph: str,
    ) -> list[str]:
        lines: list[str] = [
            f"{paragraph_name}.",
            f"MOVE '{paragraph_name}' TO SQL-LOCATION.",
        ]

        if not host_variables:
            lines.extend(
                [
                    f"* DB2 WARNING: No FETCH host variables mapped for {cursor_name}.",
                    "EXEC SQL",
                    f"    FETCH {cursor_name}",
                    "END-EXEC.",
                    "EVALUATE SQLCODE",
                    "WHEN ZERO",
                    "    CONTINUE",
                    "WHEN 100",
                    f"    SET {cursor_name}-EOC TO TRUE",
                    "WHEN OTHER",
                    f"    DISPLAY 'ERROR WHILE FETCHING CURSOR {cursor_name}'.",
                    f"    PERFORM {sql_error_paragraph}.",
                    "END-EVALUATE.",
                ]
            )
            return lines

        lines.extend(
            [
                "EXEC SQL",
                f"    FETCH {cursor_name}",
                "    INTO",
            ]
        )

        lines.extend(
            self._fetch_host_lines(
                host_variables,
            )
        )

        lines.extend(
            [
                "END-EXEC.",
                "EVALUATE SQLCODE",
                "WHEN ZERO",
                "    CONTINUE",
                "WHEN 100",
                f"    SET {cursor_name}-EOC TO TRUE",
                "WHEN OTHER",
                f"    DISPLAY 'ERROR WHILE FETCHING CURSOR {cursor_name}'.",
                f"    PERFORM {sql_error_paragraph}.",
                "END-EVALUATE.",
            ]
        )

        return lines

    def _fetch_host_lines(
        self,
        host_variables: list[str],
    ) -> list[str]:
        lines: list[str] = []

        clean_hosts = [
            self._normalize_host_reference(host)
            for host in host_variables
            if self._normalize_host_reference(host)
        ]

        for index, host in enumerate(clean_hosts):
            suffix = "," if index < len(clean_hosts) - 1 else ""
            lines.append(
                f"        {host}{suffix}"
            )

        return lines

    def _close_paragraph(
        self,
        cursor_name: str,
        paragraph_name: str,
        sql_error_paragraph: str,
    ) -> list[str]:
        return [
            f"{paragraph_name}.",
            f"MOVE '{paragraph_name}' TO SQL-LOCATION.",
            "EXEC SQL",
            f"    CLOSE {cursor_name}",
            "END-EXEC.",
            "EVALUATE SQLCODE",
            "WHEN ZERO",
            "    CONTINUE",
            "WHEN OTHER",
            f"    DISPLAY 'ERROR WHILE CLOSING CURSOR {cursor_name}'.",
            f"    PERFORM {sql_error_paragraph}.",
            "END-EVALUATE.",
        ]

    def _insert_paragraph_block(
        self,
        text: str,
        block: str,
    ) -> str:
        end_program_match = self.END_PROGRAM_PATTERN.search(
            text,
        )

        if end_program_match:
            return (
                text[: end_program_match.start()]
                + "\n"
                + block
                + "\n"
                + text[end_program_match.start() :]
            )

        return text.rstrip() + "\n\n" + block

    def _detect_sql_error_paragraph(
        self,
        text: str,
    ) -> str:
        match = self.SQL_ERROR_PARAGRAPH_PATTERN.search(
            text or "",
        )

        if not match:
            return "SQL-ERROR"

        return match.group(
            1,
        ).upper()

    def _comment_block(
        self,
        title: str,
    ) -> list[str]:
        return [
            "******************************************************************",
            f"* {title:<62}*",
            "******************************************************************",
        ]

    def _normalize_host_reference(
        self,
        value: str,
    ) -> str:
        text = str(
            value or "",
        ).strip()

        while text.startswith("::"):
            text = text[1:]

        while text.startswith(": :"):
            text = text[1:].strip()

        if not text:
            return ""

        if text.startswith(":"):
            return text

        return ":" + text