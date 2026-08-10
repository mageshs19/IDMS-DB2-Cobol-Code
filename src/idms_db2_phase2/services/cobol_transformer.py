import re

from idms_db2_phase2.domain.models import IdmsOperation
from idms_db2_phase2.parsers.cobol_parser import CobolParser
from idms_db2_phase2.services.sql_generator import SqlGenerator


class CobolTransformer:
    def __init__(
        self,
        sql_generator: SqlGenerator,
    ) -> None:
        self.sql_generator = sql_generator
        self.parser = CobolParser()

    def transform(
        self,
        cobol_text: str,
        target_program_id: str = "",
    ) -> tuple[str, list[str], list[IdmsOperation]]:
        operations = self.parser.analyze(
            cobol_text,
        )

        validation_messages: list[str] = []
        output_lines: list[str] = []

        declared_cursors: set[str] = set()

        for line in cobol_text.splitlines():
            converted_lines = self._convert_line(
                line=line,
                target_program_id=target_program_id,
                declared_cursors=declared_cursors,
                validation_messages=validation_messages,
            )

            output_lines.extend(
                converted_lines,
            )

        converted = "\n".join(
            output_lines,
        )

        converted = self._ensure_sqlca(
            converted,
        )

        converted = self._cleanup(
            converted,
        )

        return converted, validation_messages, operations

    def _convert_line(
        self,
        line: str,
        target_program_id: str,
        declared_cursors: set[str],
        validation_messages: list[str],
    ) -> list[str]:
        upper = line.upper()

        if target_program_id and "PROGRAM-ID." in upper:
            return [
                re.sub(
                    r"PROGRAM-ID\.\s+[A-Z0-9-]+",
                    f"PROGRAM-ID. {target_program_id}",
                    line,
                    flags=re.IGNORECASE,
                )
            ]

        if re.search(
            r"\bREADY\b",
            upper,
        ):
            return [
                "           * DB2: IDMS READY removed.",
                "           CONTINUE.",
            ]

        if re.search(
            r"\bFINISH\b",
            upper,
        ):
            return [
                "           * DB2: IDMS FINISH converted to COMMIT.",
                "           EXEC SQL",
                "                COMMIT",
                "           END-EXEC.",
            ]

        if re.search(
            r"\bCOMMIT\b",
            upper,
        ) and "EXEC SQL" not in upper:
            return [
                "           EXEC SQL",
                "                COMMIT",
                "           END-EXEC.",
            ]

        match = re.search(
            r"\bOBTAIN\s+(?:KEEP\s+)?([A-Z0-9-]+)\s+CALC\b",
            upper,
        )

        if match:
            record = match.group(
                1,
            ).upper()

            return [
                f"           * DB2: Converted OBTAIN CALC for {record}.",
                *self.sql_generator.select_by_key(
                    record,
                ),
                "           IF SQLCODE NOT = 0 AND SQLCODE NOT = 100",
                "                PERFORM SQL-ERROR",
                "           END-IF.",
            ]

        match = re.search(
            r"\bOBTAIN\s+(FIRST|NEXT)\s+([A-Z0-9-]+)\s+WITHIN\s+([A-Z0-9-]+)\b",
            upper,
        )

        if match:
            first_or_next = match.group(
                1,
            ).upper()

            record = match.group(
                2,
            ).upper()

            set_name = match.group(
                3,
            ).upper()

            cursor_name = self.sql_generator.cursor_name(
                set_name,
            )

            lines: list[str] = [
                f"           * DB2: Converted OBTAIN {first_or_next} {record} WITHIN {set_name}.",
            ]

            relationship_condition_found = self.sql_generator.has_cursor_relationship_condition(
                record_name=record,
                set_name=set_name,
            )

            if cursor_name not in declared_cursors:
                lines.extend(
                    self.sql_generator.cursor_declare(
                        record,
                        set_name,
                    )
                )

                declared_cursors.add(
                    cursor_name,
                )

            if first_or_next == "FIRST":
                lines.extend(
                    self.sql_generator.open_cursor(
                        set_name,
                    )
                )

            lines.extend(
                self.sql_generator.fetch_cursor(
                    record,
                    set_name,
                )
            )

            lines.extend(
                [
                    "           IF SQLCODE NOT = 0 AND SQLCODE NOT = 100",
                    "                PERFORM SQL-ERROR",
                    "           END-IF.",
                ]
            )

            if not relationship_condition_found:
                validation_messages.append(
                    f"Cursor WHERE clause for set {set_name} could not be fully generated. "
                    f"Review Sheet Mapping Relation/FK rows for record {record}."
                )

            return lines

        match = re.search(
            r"\bFIND\s+FIRST\s+([A-Z0-9-]+)?\s*WITHIN\s+([A-Z0-9-]+)\b",
            upper,
        )

        if match:
            record = (
                match.group(
                    1,
                )
                or ""
            ).upper()

            set_name = match.group(
                2,
            ).upper()

            if not record:
                validation_messages.append(
                    f"FIND FIRST WITHIN {set_name} needs record inference."
                )

                return [
                    f"           * TODO DB2: FIND FIRST WITHIN {set_name} needs record inference.",
                    "           CONTINUE.",
                ]

            relationship_condition_found = self.sql_generator.has_cursor_relationship_condition(
                record_name=record,
                set_name=set_name,
            )

            lines = [
                f"           * DB2: Converted FIND FIRST {record} WITHIN {set_name}.",
                *self.sql_generator.cursor_declare(
                    record,
                    set_name,
                ),
                *self.sql_generator.open_cursor(
                    set_name,
                ),
                *self.sql_generator.fetch_cursor(
                    record,
                    set_name,
                ),
            ]

            if not relationship_condition_found:
                validation_messages.append(
                    f"Cursor WHERE clause for set {set_name} could not be fully generated. "
                    f"Review Sheet Mapping Relation/FK rows for record {record}."
                )

            return lines

        match = re.search(
            r"\bSTORE\s+([A-Z0-9-]+)\b",
            upper,
        )

        if match:
            record = match.group(
                1,
            ).upper()

            return [
                f"           * DB2: Converted STORE for {record}.",
                *self.sql_generator.insert(
                    record,
                ),
                "           IF SQLCODE NOT = 0",
                "                PERFORM SQL-ERROR",
                "           END-IF.",
            ]

        match = re.search(
            r"\bMODIFY\s+([A-Z0-9-]+)\b",
            upper,
        )

        if match:
            record = match.group(
                1,
            ).upper()

            return [
                f"           * DB2: Converted MODIFY for {record}.",
                *self.sql_generator.update(
                    record,
                ),
                "           IF SQLCODE NOT = 0",
                "                PERFORM SQL-ERROR",
                "           END-IF.",
            ]

        match = re.search(
            r"\bERASE\s+([A-Z0-9-]+)\b",
            upper,
        )

        if match:
            record = match.group(
                1,
            ).upper()

            return [
                f"           * DB2: Converted ERASE for {record}.",
                *self.sql_generator.delete(
                    record,
                ),
                "           IF SQLCODE NOT = 0",
                "                PERFORM SQL-ERROR",
                "           END-IF.",
            ]

        if "DB-REC-NOT-FOUND" in upper:
            return [
                re.sub(
                    r"\bDB-REC-NOT-FOUND\b",
                    "SQLCODE = 100",
                    line,
                    flags=re.IGNORECASE,
                )
            ]

        if "DB-END-OF-SET" in upper:
            return [
                re.sub(
                    r"\bDB-END-OF-SET\b",
                    "SQLCODE = 100",
                    line,
                    flags=re.IGNORECASE,
                )
            ]

        return [
            line,
        ]

    def _ensure_sqlca(
        self,
        text: str,
    ) -> str:
        if "INCLUDE SQLCA" in text.upper():
            return text

        marker = "WORKING-STORAGE SECTION."

        if marker in text.upper():
            pattern = re.compile(
                r"WORKING-STORAGE SECTION\.",
                re.IGNORECASE,
            )

            return pattern.sub(
                "       WORKING-STORAGE SECTION.\n"
                "           EXEC SQL\n"
                "                INCLUDE SQLCA\n"
                "           END-EXEC.",
                text,
                count=1,
            )

        return (
            "       DATA DIVISION.\n"
            "       WORKING-STORAGE SECTION.\n"
            "           EXEC SQL\n"
            "                INCLUDE SQLCA\n"
            "           END-EXEC.\n"
            + text
        )

    def _cleanup(
        self,
        text: str,
    ) -> str:
        lines = text.splitlines()
        cleaned_lines: list[str] = []

        inside_exec_sql = False

        for raw_line in lines:
            line = raw_line.rstrip()

            if not line.strip():
                cleaned_lines.append(
                    "",
                )
                continue

            upper = line.strip().upper()

            if upper.startswith("EXEC SQL"):
                inside_exec_sql = True
                cleaned_lines.append(
                    "           EXEC SQL",
                )
                continue

            if upper.startswith("END-EXEC"):
                inside_exec_sql = False
                cleaned_lines.append(
                    "           END-EXEC.",
                )
                continue

            if inside_exec_sql:
                cleaned_lines.append(
                    self._format_sql_line(
                        line,
                    )
                )
                continue

            cleaned_lines.append(
                self._format_cobol_line(
                    line,
                )
            )

        text = "\n".join(
            cleaned_lines,
        )

        text = re.sub(
            r"\n{3,}",
            "\n\n",
            text,
        )

        return text.strip() + "\n"

    def _format_sql_line(
        self,
        line: str,
    ) -> str:
        stripped = line.strip()
        upper = stripped.upper()

        sql_clause_prefixes = (
            "DECLARE ",
            "SELECT",
            "INTO",
            "FROM ",
            "WHERE",
            "ORDER BY",
            "GROUP BY",
            "HAVING",
            "FETCH",
            "OPEN ",
            "CLOSE ",
            "COMMIT",
            "INSERT",
            "UPDATE",
            "DELETE",
            "SET",
            "VALUES",
        )

        if upper.startswith(sql_clause_prefixes):
            return f"                {stripped}"

        if upper.startswith("AND "):
            return f"                    {stripped}"

        if upper.startswith("OR "):
            return f"                    {stripped}"

        return f"                    {stripped}"

    def _format_cobol_line(
        self,
        line: str,
    ) -> str:
        stripped = line.strip()

        if not stripped:
            return ""

        upper = stripped.upper()

        if self._is_division_or_section_header(
            upper,
        ):
            return f"       {stripped}"

        if self._is_paragraph_header(
            stripped,
        ):
            return f"       {stripped}"

        if upper.startswith("*"):
            return f"      {stripped}"

        return f"           {stripped}"

    def _is_division_or_section_header(
        self,
        upper: str,
    ) -> bool:
        headers = (
            "IDENTIFICATION DIVISION.",
            "ENVIRONMENT DIVISION.",
            "DATA DIVISION.",
            "PROCEDURE DIVISION.",
            "CONFIGURATION SECTION.",
            "INPUT-OUTPUT SECTION.",
            "FILE SECTION.",
            "WORKING-STORAGE SECTION.",
            "LINKAGE SECTION.",
        )

        return upper in headers

    def _is_paragraph_header(
        self,
        line: str,
    ) -> bool:
        stripped = line.strip()

        if not stripped.endswith("."):
            return False

        upper = stripped.upper()

        if " " in upper:
            return False

        if upper.startswith(("IF", "ELSE", "END-IF", "MOVE", "PERFORM")):
            return False

        return bool(
            re.fullmatch(
                r"[A-Z0-9-]+\.",
                upper,
            )
        )