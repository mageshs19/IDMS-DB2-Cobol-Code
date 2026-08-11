import re

from idms_db2_phase2.domain.models import DclgenColumn, IdmsOperation, SheetMappingRow
from idms_db2_phase2.services.name_normalizer import NameNormalizer


class Db2CursorParagraphGenerator:
    """
    Generates generic DB2 OPEN/FETCH/CLOSE paragraphs.

    Generic rules:
    - No hardcoded program IDs.
    - No hardcoded cursor names.
    - No hardcoded DB2 table names.
    - Cursor names are derived from IDMS set names.
    - FETCH host variables are derived from Sheet Mapping + DCLGEN.
    """

    GENERATED_MARKER = "* DB2 GENERATED CURSOR OPEN FETCH CLOSE PARAGRAPHS"

    def __init__(
        self,
        mapping_rows: list[SheetMappingRow],
        dclgen_columns: list[DclgenColumn],
        operations: list[IdmsOperation],
    ) -> None:
        self.mapping_rows = mapping_rows
        self.dclgen_columns = dclgen_columns
        self.operations = operations
        self.messages: list[str] = []
        self.dclgen_lookup = self._build_dclgen_lookup()

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

        updated_text = cobol_text

        for spec in cursor_specs:
            updated_text = self._replace_inline_open_fetch_close(
                text=updated_text,
                cursor_name=str(spec["cursor_name"]),
                open_paragraph=str(spec["open_paragraph"]),
                fetch_paragraph=str(spec["fetch_paragraph"]),
                close_paragraph=str(spec["close_paragraph"]),
            )

        block = self._paragraph_block(
            cursor_specs=cursor_specs,
        )

        updated_text = self._insert_paragraph_block(
            text=updated_text,
            block=block,
        )

        self.messages.append(
            f"DB2 cursor paragraphs: generated {len(cursor_specs)} cursor paragraph set(s)."
        )

        return updated_text, self.messages

    def _cursor_specs(
        self,
    ) -> list[dict[str, object]]:
        specs: list[dict[str, object]] = []
        seen: set[str] = set()

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

            if not set_name:
                continue

            if set_name in seen:
                continue

            seen.add(
                set_name,
            )

            cursor_name = self._cursor_name(
                set_name=set_name,
            )

            specs.append(
                {
                    "set_name": set_name,
                    "record_name": record_name,
                    "cursor_name": cursor_name,
                    "open_paragraph": f"OPEN-{cursor_name}",
                    "fetch_paragraph": f"FETCH-{cursor_name}",
                    "close_paragraph": f"CLOSE-{cursor_name}",
                    "host_variables": self._fetch_host_variables(
                        record_name=record_name,
                    ),
                }
            )

        return specs

    def _paragraph_block(
        self,
        cursor_specs: list[dict[str, object]],
    ) -> str:
        lines: list[str] = [
            "",
            "      ******************************************************************",
            f"      * {self.GENERATED_MARKER}",
            "      ******************************************************************",
            "",
        ]

        for spec in cursor_specs:
            cursor_name = str(spec["cursor_name"])
            open_paragraph = str(spec["open_paragraph"])
            fetch_paragraph = str(spec["fetch_paragraph"])
            close_paragraph = str(spec["close_paragraph"])
            host_variables = list(spec["host_variables"])

            lines.extend(
                self._open_paragraph(
                    cursor_name=cursor_name,
                    paragraph_name=open_paragraph,
                )
            )
            lines.append("")

            lines.extend(
                self._fetch_paragraph(
                    cursor_name=cursor_name,
                    paragraph_name=fetch_paragraph,
                    host_variables=host_variables,
                )
            )
            lines.append("")

            lines.extend(
                self._close_paragraph(
                    cursor_name=cursor_name,
                    paragraph_name=close_paragraph,
                )
            )
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def _open_paragraph(
        self,
        cursor_name: str,
        paragraph_name: str,
    ) -> list[str]:
        return [
            f"       {paragraph_name}.",
            f"           MOVE '{paragraph_name}' TO SQL-LOCATION",
            "           EXEC SQL",
            f"                OPEN {cursor_name}",
            "           END-EXEC.",
            "           EVALUATE SQLCODE",
            "               WHEN ZERO",
            f"                   SET {cursor_name}-NOT-EOC TO TRUE",
            "               WHEN OTHER",
            f"                   DISPLAY 'ERROR WHILE OPENING CURSOR {cursor_name}'",
            "                   PERFORM SQLERROR",
            "           END-EVALUATE.",
        ]

    def _fetch_paragraph(
        self,
        cursor_name: str,
        paragraph_name: str,
        host_variables: list[str],
    ) -> list[str]:
        lines: list[str] = [
            f"       {paragraph_name}.",
            f"           MOVE '{paragraph_name}' TO SQL-LOCATION",
        ]

        if not host_variables:
            lines.extend(
                [
                    f"           * TODO DB2: No FETCH host variables mapped for {cursor_name}.",
                    "           CONTINUE.",
                ]
            )
            return lines

        lines.extend(
            [
                "           EXEC SQL",
                f"                FETCH {cursor_name}",
                "                INTO",
            ]
        )

        for index, host_variable in enumerate(host_variables):
            suffix = "," if index < len(host_variables) - 1 else ""

            lines.append(
                f"                    {host_variable}{suffix}"
            )

        lines.extend(
            [
                "           END-EXEC.",
                "           EVALUATE SQLCODE",
                "               WHEN ZERO",
                "                   CONTINUE",
                "               WHEN 100",
                f"                   SET {cursor_name}-EOC TO TRUE",
                "               WHEN OTHER",
                f"                   DISPLAY 'ERROR WHILE FETCHING CURSOR {cursor_name}'",
                "                   PERFORM SQLERROR",
                "           END-EVALUATE.",
            ]
        )

        return lines

    def _close_paragraph(
        self,
        cursor_name: str,
        paragraph_name: str,
    ) -> list[str]:
        return [
            f"       {paragraph_name}.",
            f"           MOVE '{paragraph_name}' TO SQL-LOCATION",
            "           EXEC SQL",
            f"                CLOSE {cursor_name}",
            "           END-EXEC.",
            "           EVALUATE SQLCODE",
            "               WHEN ZERO",
            "                   CONTINUE",
            "               WHEN OTHER",
            f"                   DISPLAY 'ERROR WHILE CLOSING CURSOR {cursor_name}'",
            "                   PERFORM SQLERROR",
            "           END-EVALUATE.",
        ]

    def _fetch_host_variables(
        self,
        record_name: str,
    ) -> list[str]:
        rows = self._rows_for_record(
            record_name=record_name,
        )

        host_variables: list[str] = []
        seen: set[str] = set()

        table_name = self._best_dclgen_table_for_rows(
            rows=rows,
        )

        for row in rows:
            column = NameNormalizer.normalize(
                row.new_db2_field_name,
            )

            if not column:
                continue

            host = self._host_for_column(
                table_name=table_name,
                column_name=column,
            )

            if not host:
                continue

            if host in seen:
                continue

            seen.add(
                host,
            )
            host_variables.append(
                host,
            )

        return host_variables

    def _host_for_column(
        self,
        table_name: str,
        column_name: str,
    ) -> str:
        table = NameNormalizer.normalize(
            table_name,
        )
        column = NameNormalizer.normalize(
            column_name,
        )

        host = self.dclgen_lookup.get(
            (
                table,
                column,
            )
        )

        if not host:
            host = self.dclgen_lookup.get(
                (
                    "",
                    column,
                )
            )

        if host:
            return f":{host}"

        if table and column:
            return f":DCL{table}.{NameNormalizer.to_cobol(column)}"

        if column:
            return f":{NameNormalizer.to_cobol(column)}"

        return ""

    def _build_dclgen_lookup(
        self,
    ) -> dict[tuple[str, str], str]:
        lookup: dict[tuple[str, str], str] = {}

        for column in self.dclgen_columns:
            table = NameNormalizer.normalize(
                column.table_name,
            )
            column_name = NameNormalizer.normalize(
                column.column_name,
            )
            host_name = NameNormalizer.to_cobol(
                column.cobol_host_name or column.column_name,
            )

            if not column_name or not host_name:
                continue

            host_reference = f"DCL{table}.{host_name}" if table else host_name

            lookup[
                (
                    table,
                    column_name,
                )
            ] = host_reference

            lookup[
                (
                    "",
                    column_name,
                )
            ] = host_reference

        return lookup

    def _best_dclgen_table_for_rows(
        self,
        rows: list[SheetMappingRow],
    ) -> str:
        mapping_columns = {
            NameNormalizer.normalize(
                row.new_db2_field_name,
            )
            for row in rows
            if row.new_db2_field_name
        }

        scores: dict[str, int] = {}

        for column in self.dclgen_columns:
            table = NameNormalizer.normalize(
                column.table_name,
            )
            db2_column = NameNormalizer.normalize(
                column.column_name,
            )

            if not table or not db2_column:
                continue

            if db2_column in mapping_columns:
                scores[table] = scores.get(
                    table,
                    0,
                ) + 1

        if scores:
            return max(
                scores.items(),
                key=lambda item: item[1],
            )[0]

        for row in rows:
            table = NameNormalizer.normalize(
                row.new_db2_record,
            )

            if table:
                return table

        return ""

    def _rows_for_record(
        self,
        record_name: str,
    ) -> list[SheetMappingRow]:
        normalized_record = NameNormalizer.normalize(
            record_name,
        )
        no_suffix = NameNormalizer.remove_record_suffix(
            normalized_record,
        )

        rows: list[SheetMappingRow] = []

        for row in self.mapping_rows:
            row_record = NameNormalizer.normalize(
                row.cobol_record_idms,
            )
            row_record_no_suffix = NameNormalizer.remove_record_suffix(
                row_record,
            )

            if row_record in {normalized_record, no_suffix}:
                rows.append(row)
                continue

            if row_record_no_suffix in {normalized_record, no_suffix}:
                rows.append(row)

        return rows

    def _cursor_name(
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

    def _replace_inline_open_fetch_close(
        self,
        text: str,
        cursor_name: str,
        open_paragraph: str,
        fetch_paragraph: str,
        close_paragraph: str,
    ) -> str:
        updated = text

        open_pattern = re.compile(
            rf"^\s*EXEC\s+SQL\s*\n\s*OPEN\s+{re.escape(cursor_name)}\s*\n\s*END-EXEC\.?\s*$",
            flags=re.IGNORECASE | re.MULTILINE,
        )
        updated = open_pattern.sub(
            f"           PERFORM {open_paragraph}",
            updated,
        )

        fetch_pattern = re.compile(
            rf"^\s*EXEC\s+SQL\s*\n\s*FETCH\s+{re.escape(cursor_name)}\s*.*?\n\s*END-EXEC\.?\s*$",
            flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
        )
        updated = fetch_pattern.sub(
            f"           PERFORM {fetch_paragraph}",
            updated,
        )

        close_pattern = re.compile(
            rf"^\s*EXEC\s+SQL\s*\n\s*CLOSE\s+{re.escape(cursor_name)}\s*\n\s*END-EXEC\.?\s*$",
            flags=re.IGNORECASE | re.MULTILINE,
        )
        updated = close_pattern.sub(
            f"           PERFORM {close_paragraph}",
            updated,
        )

        return updated

    def _insert_paragraph_block(
        self,
        text: str,
        block: str,
    ) -> str:
        end_program_pattern = re.compile(
            r"^\s*END\s+PROGRAM\b.*$",
            flags=re.IGNORECASE | re.MULTILINE,
        )

        match = end_program_pattern.search(
            text,
        )

        if match:
            return text[: match.start()] + block + "\n" + text[match.start() :]

        return text.rstrip() + "\n\n" + block