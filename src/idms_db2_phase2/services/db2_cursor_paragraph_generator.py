import re

from idms_db2_phase2.domain.models import DclgenColumn, IdmsOperation, SheetMappingRow
from idms_db2_phase2.services.db2_infrastructure_generator import Db2MappingContext
from idms_db2_phase2.services.name_normalizer import NameNormalizer


class Db2CursorParagraphGenerator:
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
        self.context = Db2MappingContext(
            mapping_rows=mapping_rows,
            dclgen_columns=dclgen_columns,
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

        for spec in cursor_specs:
            if not spec["host_variables"]:
                self.messages.append(
                    "DB2 cursor paragraphs: no FETCH host variables resolved for "
                    f"cursor {spec['cursor_name']} record {spec['record_name']} table {spec['table_name']}."
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

            table_name = self.context.best_table_for_record(
                record_name,
            )

            host_variables = self.context.host_variables_for_record(
                record_name=record_name,
                table_name=table_name,
            )

            cursor_name = self._cursor_name(
                set_name,
            )

            specs.append(
                {
                    "set_name": set_name,
                    "record_name": record_name,
                    "table_name": table_name,
                    "cursor_name": cursor_name,
                    "open_paragraph": f"OPEN-{cursor_name}",
                    "fetch_paragraph": f"FETCH-{cursor_name}",
                    "close_paragraph": f"CLOSE-{cursor_name}",
                    "host_variables": host_variables,
                }
            )

        return specs

    def _paragraph_block(
        self,
        cursor_specs: list[dict[str, object]],
    ) -> str:
        lines: list[str] = [
            "",
            "******************************************************************",
            self.GENERATED_MARKER,
            "******************************************************************",
            "",
        ]

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
                spec["host_variables"],
            )

            lines.extend(
                self._open_paragraph(
                    cursor_name=cursor_name,
                    paragraph_name=open_paragraph,
                )
            )

            lines.append(
                "",
            )

            lines.extend(
                self._fetch_paragraph(
                    cursor_name=cursor_name,
                    paragraph_name=fetch_paragraph,
                    host_variables=host_variables,
                )
            )

            lines.append(
                "",
            )

            lines.extend(
                self._close_paragraph(
                    cursor_name=cursor_name,
                    paragraph_name=close_paragraph,
                )
            )

            lines.append(
                "",
            )

        return "\n".join(
            lines,
        ).rstrip() + "\n"

    def _open_paragraph(
        self,
        cursor_name: str,
        paragraph_name: str,
    ) -> list[str]:
        return [
            f"{paragraph_name}.",
            f"MOVE '{paragraph_name}' TO SQL-LOCATION.",
            "EXEC SQL",
            f"OPEN {cursor_name}",
            "END-EXEC.",
            "EVALUATE SQLCODE",
            "WHEN ZERO",
            f"SET {cursor_name}-NOT-EOC TO TRUE.",
            "WHEN OTHER",
            f"DISPLAY 'ERROR WHILE OPENING CURSOR {cursor_name}'.",
            "PERFORM SQLERROR.",
            "END-EVALUATE.",
        ]

    def _fetch_paragraph(
        self,
        cursor_name: str,
        paragraph_name: str,
        host_variables: list[str],
    ) -> list[str]:
        lines: list[str] = [
            f"{paragraph_name}.",
            f"MOVE '{paragraph_name}' TO SQL-LOCATION.",
        ]

        if not host_variables:
            lines.extend(
                [
                    f"* ERROR DB2: No FETCH host variables mapped for {cursor_name}.",
                    "CONTINUE.",
                ]
            )
            return lines

        lines.extend(
            [
                "EXEC SQL",
                f"FETCH {cursor_name}",
                "INTO",
            ]
        )

        lines.extend(
            self._comma_lines(
                host_variables,
                "    ",
            )
        )

        lines.extend(
            [
                "END-EXEC.",
                "EVALUATE SQLCODE",
                "WHEN ZERO",
                "CONTINUE.",
                "WHEN 100",
                f"SET {cursor_name}-EOC TO TRUE.",
                "WHEN OTHER",
                f"DISPLAY 'ERROR WHILE FETCHING CURSOR {cursor_name}'.",
                "PERFORM SQLERROR.",
                "END-EVALUATE.",
            ]
        )

        return lines

    def _close_paragraph(
        self,
        cursor_name: str,
        paragraph_name: str,
    ) -> list[str]:
        return [
            f"{paragraph_name}.",
            f"MOVE '{paragraph_name}' TO SQL-LOCATION.",
            "EXEC SQL",
            f"CLOSE {cursor_name}",
            "END-EXEC.",
            "EVALUATE SQLCODE",
            "WHEN ZERO",
            "CONTINUE.",
            "WHEN OTHER",
            f"DISPLAY 'ERROR WHILE CLOSING CURSOR {cursor_name}'.",
            "PERFORM SQLERROR.",
            "END-EVALUATE.",
        ]

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
            f"PERFORM {open_paragraph}.",
            updated,
        )

        close_pattern = re.compile(
            rf"^\s*EXEC\s+SQL\s*\n\s*CLOSE\s+{re.escape(cursor_name)}\s*\n\s*END-EXEC\.?\s*$",
            flags=re.IGNORECASE | re.MULTILINE,
        )
        updated = close_pattern.sub(
            f"PERFORM {close_paragraph}.",
            updated,
        )

        return updated

    def _insert_paragraph_block(
        self,
        text: str,
        block: str,
    ) -> str:
        end_program_pattern = re.compile(
            r"^\s*(?:\d{6}\s+)?END\s+PROGRAM\b.*$",
            flags=re.IGNORECASE | re.MULTILINE,
        )

        match = end_program_pattern.search(
            text,
        )

        if match:
            return text[: match.start()] + block + "\n" + text[match.start() :]

        return text.rstrip() + "\n\n" + block

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

    def _comma_lines(
        self,
        items: list[str],
        indent: str,
    ) -> list[str]:
        output: list[str] = []

        for index, item in enumerate(items):
            suffix = "," if index < len(items) - 1 else ""

            output.append(
                f"{indent}{item}{suffix}",
            )

        return output