import re

from idms_db2_phase2.domain.models import IdmsOperation
from idms_db2_phase2.parsers.cobol_parser import CobolParser
from idms_db2_phase2.services.sql_generator import SqlGenerator


class CobolTransformer:
    def __init__(self, sql_generator: SqlGenerator) -> None:
        self.sql_generator = sql_generator
        self.parser = CobolParser()
        self.sql_error_paragraph = "SQL-ERROR"

    def transform(
        self,
        cobol_text: str,
        target_program_id: str = "",
    ) -> tuple[str, list[str], list[IdmsOperation]]:
        self.sql_error_paragraph = self._detect_sql_error_paragraph(
            cobol_text,
        )

        operations = self.parser.analyze(
            cobol_text,
        )

        validation_messages: list[str] = []
        output_lines: list[str] = []
        used_cursor_records: dict[str, str] = {}
        pending_close_set = ""

        for line in cobol_text.splitlines():
            converted_lines, opened_set = self._convert_line(
                line=line,
                target_program_id=target_program_id,
                used_cursor_records=used_cursor_records,
                validation_messages=validation_messages,
            )

            if pending_close_set:
                converted_lines = self._rewrite_sqlcode_100_loop_to_eoc_loop(
                    lines=converted_lines,
                    set_name=pending_close_set,
                )

            output_lines.extend(
                converted_lines,
            )

            if opened_set:
                pending_close_set = opened_set

            converted_block = "\n".join(
                converted_lines,
            )

            if pending_close_set and self._is_eoc_loop_line(
                text=converted_block,
                set_name=pending_close_set,
            ):
                output_lines.extend(
                    self.sql_generator.close_cursor(
                        pending_close_set,
                    )
                )
                pending_close_set = ""

        converted = "\n".join(
            output_lines,
        )

        converted = self._remove_existing_standalone_sqlca(
            converted,
        )

        converted = self._ensure_db2_infrastructure(
            text=converted,
            used_cursor_records=used_cursor_records,
        )

        converted = self._insert_cursor_paragraphs(
            text=converted,
            used_cursor_records=used_cursor_records,
        )

        converted = self._fix_end_program_name(
            text=converted,
            target_program_id=target_program_id,
        )

        converted = self._ensure_sql_error_paragraph(
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
        used_cursor_records: dict[str, str],
        validation_messages: list[str],
    ) -> tuple[list[str], str]:
        upper = line.upper().strip()

        if target_program_id and "PROGRAM-ID." in upper:
            return [
                re.sub(
                    r"PROGRAM-ID\.\s+[A-Z0-9-]+",
                    f"PROGRAM-ID. {target_program_id}",
                    line,
                    flags=re.IGNORECASE,
                )
            ], ""

        if self._is_idms_control_line(
            upper,
        ):
            return [
                f"           * DB2: Removed residual IDMS control statement: {line.strip()}",
                "           CONTINUE.",
            ], ""

        if re.search(
            r"\bREADY\b",
            upper,
        ):
            return [
                "           * DB2: IDMS READY removed.",
                "           CONTINUE.",
            ], ""

        if re.search(
            r"\bFINISH\b",
            upper,
        ):
            return [
                "           * DB2: IDMS FINISH converted to COMMIT.",
                "           EXEC SQL",
                "                COMMIT",
                "           END-EXEC.",
            ], ""

        if re.search(
            r"\bCOMMIT\b",
            upper,
        ) and "EXEC SQL" not in upper:
            return [
                "           EXEC SQL",
                "                COMMIT",
                "           END-EXEC.",
            ], ""

        obtain_calc_match = re.search(
            r"\bOBTAIN\s+(?:KEEP\s+)?([A-Z0-9-]+)\s+CALC\b",
            upper,
        )

        if obtain_calc_match:
            record = obtain_calc_match.group(
                1,
            ).upper()

            return [
                f"           * DB2: Converted OBTAIN CALC for {record}.",
                *self.sql_generator.select_by_key(
                    record,
                ),
                "           IF SQLCODE NOT = 0 AND SQLCODE NOT = 100",
                f"                PERFORM {self.sql_error_paragraph}",
                "           END-IF.",
            ], ""

        obtain_set_match = re.search(
            r"\bOBTAIN\s+(FIRST|NEXT)\s+([A-Z0-9-]+)\s+WITHIN\s+([A-Z0-9-]+)\b",
            upper,
        )

        if obtain_set_match:
            first_or_next = obtain_set_match.group(
                1,
            ).upper()

            record = obtain_set_match.group(
                2,
            ).upper()

            set_name = obtain_set_match.group(
                3,
            ).upper()

            used_cursor_records[set_name] = record

            lines: list[str] = [
                f"           * DB2: Converted OBTAIN {first_or_next} {record} WITHIN {set_name}.",
            ]

            relationship_condition_found = self.sql_generator.has_cursor_relationship_condition(
                record_name=record,
                set_name=set_name,
            )

            opened_set = ""

            if first_or_next == "FIRST":
                lines.extend(
                    self.sql_generator.open_cursor(
                        set_name,
                    )
                )
                opened_set = set_name

            lines.extend(
                self.sql_generator.fetch_cursor(
                    record_name=record,
                    set_name=set_name,
                )
            )

            lines.extend(
                [
                    "           IF SQLCODE NOT = 0 AND SQLCODE NOT = 100",
                    f"                PERFORM {self.sql_error_paragraph}",
                    "           END-IF.",
                ]
            )

            if not relationship_condition_found:
                validation_messages.append(
                    f"Cursor WHERE clause for set {set_name} could not be generated from Sheet Mapping relation/FK rows."
                )

            return lines, opened_set

        find_first_match = re.search(
            r"\bFIND\s+FIRST\s+([A-Z0-9-]+)?\s*WITHIN\s+([A-Z0-9-]+)\b",
            upper,
        )

        if find_first_match:
            record = (
                find_first_match.group(
                    1,
                )
                or ""
            ).upper()

            set_name = find_first_match.group(
                2,
            ).upper()

            if record:
                used_cursor_records[set_name] = record
            else:
                validation_messages.append(
                    f"FIND FIRST WITHIN {set_name} needs record inference from source code or mapping."
                )

            return [
                f"           * DB2: Converted FIND FIRST {record} WITHIN {set_name}.",
                *self.sql_generator.open_cursor(
                    set_name,
                ),
                *self.sql_generator.fetch_cursor(
                    record_name=record,
                    set_name=set_name,
                ),
            ], set_name

        store_match = re.search(
            r"\bSTORE\s+([A-Z0-9-]+)\b",
            upper,
        )

        if store_match:
            record = store_match.group(
                1,
            ).upper()

            return [
                f"           * DB2: Converted STORE for {record}.",
                *self.sql_generator.insert(
                    record,
                ),
                "           IF SQLCODE NOT = 0",
                f"                PERFORM {self.sql_error_paragraph}",
                "           END-IF.",
            ], ""

        modify_match = re.search(
            r"\bMODIFY\s+([A-Z0-9-]+)\b",
            upper,
        )

        if modify_match:
            record = modify_match.group(
                1,
            ).upper()

            return [
                f"           * DB2: Converted MODIFY for {record}.",
                *self.sql_generator.update(
                    record,
                ),
                "           IF SQLCODE NOT = 0",
                f"                PERFORM {self.sql_error_paragraph}",
                "           END-IF.",
            ], ""

        erase_match = re.search(
            r"\bERASE\s+([A-Z0-9-]+)\b",
            upper,
        )

        if erase_match:
            record = erase_match.group(
                1,
            ).upper()

            return [
                f"           * DB2: Converted ERASE for {record}.",
                *self.sql_generator.delete(
                    record,
                ),
                "           IF SQLCODE NOT = 0",
                f"                PERFORM {self.sql_error_paragraph}",
                "           END-IF.",
            ], ""

        if "DB-REC-NOT-FOUND" in upper:
            return [
                re.sub(
                    r"\bDB-REC-NOT-FOUND\b",
                    "SQLCODE = 100",
                    line,
                    flags=re.IGNORECASE,
                )
            ], ""

        if "DB-END-OF-SET" in upper:
            return [
                re.sub(
                    r"\bDB-END-OF-SET\b",
                    "SQLCODE = 100",
                    line,
                    flags=re.IGNORECASE,
                )
            ], ""

        return [line], ""

    def _rewrite_sqlcode_100_loop_to_eoc_loop(
        self,
        lines: list[str],
        set_name: str,
    ) -> list[str]:
        cursor_name = self.sql_generator.cursor_name(
            set_name,
        )

        eoc_condition = f"{cursor_name}-EOC"

        rewritten: list[str] = []

        for line in lines:
            rewritten.append(
                re.sub(
                    r"\bUNTIL\s+SQLCODE\s*=\s*100\b",
                    f"UNTIL {eoc_condition}",
                    line,
                    flags=re.IGNORECASE,
                )
            )

        return rewritten

    def _is_eoc_loop_line(
        self,
        text: str,
        set_name: str,
    ) -> bool:
        cursor_name = self.sql_generator.cursor_name(
            set_name,
        )

        eoc_condition = f"{cursor_name}-EOC"

        return bool(
            re.search(
                rf"\bPERFORM\b.+\bUNTIL\s+{re.escape(eoc_condition)}\b",
                text,
                flags=re.IGNORECASE,
            )
        )

    def _ensure_db2_infrastructure(
        self,
        text: str,
        used_cursor_records: dict[str, str],
    ) -> str:
        if "DB2 SQLCA, SQL ERROR WORKING STORAGE, DCLGEN INCLUDES, AND CURSOR FLAGS" in text:
            return text

        block = self.sql_generator.db2_infrastructure_block(
            used_cursor_records=used_cursor_records,
        )

        if not block.strip():
            return text

        working_storage_pattern = re.compile(
            r"(^\s*WORKING-STORAGE\s+SECTION\.\s*$)",
            flags=re.IGNORECASE | re.MULTILINE,
        )

        working_storage_match = working_storage_pattern.search(
            text,
        )

        if working_storage_match:
            return (
                text[: working_storage_match.end()]
                + "\n"
                + block
                + text[working_storage_match.end() :]
            )

        procedure_pattern = re.compile(
            r"(^\s*PROCEDURE\s+DIVISION\.\s*$)",
            flags=re.IGNORECASE | re.MULTILINE,
        )

        procedure_match = procedure_pattern.search(
            text,
        )

        if procedure_match:
            return (
                text[: procedure_match.start()]
                + block
                + "\n"
                + text[procedure_match.start() :]
            )

        return block + "\n\n" + text

    def _insert_cursor_paragraphs(
        self,
        text: str,
        used_cursor_records: dict[str, str],
    ) -> str:
        if not used_cursor_records:
            return text

        if "DB2 GENERATED CURSOR OPEN FETCH CLOSE PARAGRAPHS" in text:
            return text

        block = self.sql_generator.cursor_paragraph_block(
            used_cursor_records=used_cursor_records,
            sql_error_paragraph=self.sql_error_paragraph,
        )

        if not block.strip():
            return text

        end_program_pattern = re.compile(
            r"^\s*END\s+PROGRAM\b.*$",
            flags=re.IGNORECASE | re.MULTILINE,
        )

        end_program_match = end_program_pattern.search(
            text,
        )

        if end_program_match:
            return (
                text[: end_program_match.start()]
                + block
                + "\n"
                + text[end_program_match.start() :]
            )

        return text.rstrip() + "\n\n" + block

    def _remove_existing_standalone_sqlca(
        self,
        text: str,
    ) -> str:
        pattern = re.compile(
            r"^\s*EXEC\s+SQL\s*\n\s*INCLUDE\s+SQLCA\s*\n\s*END-EXEC\.?\s*$",
            flags=re.IGNORECASE | re.MULTILINE,
        )

        return pattern.sub(
            "",
            text,
        )

    def _detect_sql_error_paragraph(
        self,
        cobol_text: str,
    ) -> str:
        if re.search(
            r"^\s*SQL-ERROR\.\s*$",
            cobol_text,
            flags=re.IGNORECASE | re.MULTILINE,
        ):
            return "SQL-ERROR"

        if re.search(
            r"^\s*SQLERROR\.\s*$",
            cobol_text,
            flags=re.IGNORECASE | re.MULTILINE,
        ):
            return "SQLERROR"

        return "SQL-ERROR"

    def _fix_end_program_name(
        self,
        text: str,
        target_program_id: str,
    ) -> str:
        program_id = self._current_program_id(
            text=text,
            target_program_id=target_program_id,
        )

        if not program_id:
            return text

        return re.sub(
            r"END\s+PROGRAM\s+[A-Z0-9-]+\.",
            f"END PROGRAM {program_id}.",
            text,
            flags=re.IGNORECASE,
        )

    def _current_program_id(
        self,
        text: str,
        target_program_id: str,
    ) -> str:
        if target_program_id and target_program_id.strip():
            return target_program_id.strip().upper()

        match = re.search(
            r"PROGRAM-ID\.\s+([A-Z0-9-]+)",
            text,
            flags=re.IGNORECASE,
        )

        if match:
            return match.group(1).upper()

        return ""

    def _ensure_sql_error_paragraph(
        self,
        text: str,
    ) -> str:
        if re.search(
            rf"^\s*{re.escape(self.sql_error_paragraph)}\.\s*$",
            text,
            flags=re.IGNORECASE | re.MULTILINE,
        ):
            return text

        paragraph = "\n".join(
            [
                "",
                f"       {self.sql_error_paragraph}.",
                "           DISPLAY 'DB2 SQL ERROR SQLCODE=' SQLCODE.",
                "           DISPLAY 'DB2 SQL ERROR LOCATION=' SQL-LOCATION.",
                "           CONTINUE.",
                "",
            ]
        )

        end_program_pattern = re.compile(
            r"^\s*END\s+PROGRAM\b.*$",
            flags=re.IGNORECASE | re.MULTILINE,
        )

        end_program_match = end_program_pattern.search(
            text,
        )

        if end_program_match:
            return (
                text[: end_program_match.start()]
                + paragraph
                + "\n"
                + text[end_program_match.start() :]
            )

        return text.rstrip() + "\n" + paragraph

    def _is_idms_control_line(
        self,
        upper_line: str,
    ) -> bool:
        normalized = upper_line.strip().rstrip(".")

        patterns = [
            r"^BIND\b",
            r"^BIND\s+RUN-UNIT\b",
            r"^PERFORM\s+[A-Z0-9-]*IDMS-STATUS\b",
            r"^PERFORM\s+[A-Z0-9-]*IDMS-ABORT\b",
            r"^FIND\s+CURRENT\b",
            r"^USAGE-MODE\s+IS\s+UPDATE\b",
            r"^CONNECT\b",
            r"^DISCONNECT\b",
        ]

        return any(
            re.search(
                pattern,
                normalized,
                flags=re.IGNORECASE,
            )
            for pattern in patterns
        )

    def _cleanup(
        self,
        text: str,
    ) -> str:
        lines = text.splitlines()
        cleaned_lines: list[str] = []
        inside_exec_sql = False
        evaluate_depth = 0
        pending_multiline_move = False

        for raw_line in lines:
            stripped = raw_line.strip()

            if not stripped:
                cleaned_lines.append("")
                continue

            upper = stripped.upper()

            if upper.startswith("EXEC SQL"):
                inside_exec_sql = True
                cleaned_lines.append("           EXEC SQL")
                continue

            if upper.startswith("END-EXEC"):
                inside_exec_sql = False
                cleaned_lines.append("           END-EXEC.")
                continue

            if inside_exec_sql:
                cleaned_lines.append(
                    self._format_sql_line(
                        stripped,
                    )
                )
                continue

            formatted_line, evaluate_depth, pending_multiline_move = self._format_cobol_line(
                stripped=stripped,
                evaluate_depth=evaluate_depth,
                pending_multiline_move=pending_multiline_move,
            )

            cleaned_lines.append(
                formatted_line,
            )

        text = "\n".join(
            cleaned_lines,
        )

        text = self._normalize_period_spacing(
            text,
        )

        text = re.sub(
            r"\n{3,}",
            "\n\n",
            text,
        )

        return text.strip() + "\n"

    def _format_sql_line(
        self,
        stripped: str,
    ) -> str:
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
            "FOR READ ONLY",
            "INCLUDE",
        )

        if upper.startswith(sql_clause_prefixes):
            return f"                {stripped}"

        if upper.startswith("AND "):
            return f"                    {stripped}"

        if upper.startswith("OR "):
            return f"                    {stripped}"

        if stripped.startswith(","):
            return f"                   {stripped}"

        return f"                    {stripped}"

    def _format_cobol_line(
        self,
        stripped: str,
        evaluate_depth: int,
        pending_multiline_move: bool,
    ) -> tuple[str, int, bool]:
        upper = stripped.upper()

        if self._is_division_or_section_header(upper):
            return f"       {stripped}", evaluate_depth, False

        if self._is_paragraph_header(stripped):
            return f"       {stripped}", evaluate_depth, False

        if upper.startswith("*"):
            return f"      {stripped}", evaluate_depth, pending_multiline_move

        if re.match(r"^88\s+", upper):
            return f"           {stripped}", evaluate_depth, False

        if upper.startswith("EVALUATE "):
            return f"           {stripped.rstrip('.')}", evaluate_depth + 1, False

        if upper.startswith("WHEN "):
            return f"               {stripped}", evaluate_depth, False

        if upper.startswith("END-EVALUATE"):
            new_depth = max(evaluate_depth - 1, 0)
            return "           END-EVALUATE.", new_depth, False

        if pending_multiline_move:
            if upper.startswith("TO "):
                return f"                {self._ensure_period(stripped)}", evaluate_depth, False

            return f"                {stripped}", evaluate_depth, pending_multiline_move

        if evaluate_depth > 0:
            if upper.startswith(("SET ", "DISPLAY ", "PERFORM ", "CONTINUE")):
                return f"                   {self._ensure_period(stripped)}", evaluate_depth, False

            return f"                   {stripped}", evaluate_depth, False

        if upper.startswith("MOVE "):
            if " TO " in upper:
                return f"           {self._ensure_period(stripped)}", evaluate_depth, False

            return f"           {stripped}", evaluate_depth, True

        if self._is_generated_statement_requiring_period(upper):
            return f"           {self._ensure_period(stripped)}", evaluate_depth, False

        if upper.startswith("END PROGRAM "):
            return f"       {self._ensure_period(stripped)}", evaluate_depth, False

        return f"           {stripped}", evaluate_depth, pending_multiline_move

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

        return bool(re.fullmatch(r"[A-Z0-9-]+\.", upper))

    def _is_generated_statement_requiring_period(
        self,
        upper: str,
    ) -> bool:
        prefixes = (
            "PERFORM ",
            "DISPLAY ",
            "CONTINUE",
            "SET ",
            "GOBACK",
            "EXIT",
        )

        return upper.startswith(prefixes)

    def _ensure_period(
        self,
        text: str,
    ) -> str:
        stripped = text.rstrip()

        if stripped.endswith("."):
            return stripped

        return stripped + "."

    def _normalize_period_spacing(
        self,
        text: str,
    ) -> str:
        return re.sub(r"\s+\.", ".", text)