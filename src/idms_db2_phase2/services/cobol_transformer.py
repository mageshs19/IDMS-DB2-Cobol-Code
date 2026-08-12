from __future__ import annotations

import re

from idms_db2_phase2.domain.models import IdmsOperation
from idms_db2_phase2.parsers.cobol_parser import CobolParser
from idms_db2_phase2.services.name_normalizer import NameNormalizer
from idms_db2_phase2.services.sql_generator import SqlGenerator


class CobolTransformer:
    """
    Converts executable IDMS COBOL statements to DB2 embedded SQL COBOL.

    Scope:
    - Preserve original COBOL business logic.
    - Replace IDMS database operations with DB2-compatible SQL calls.
    - Do not perform final formatting or sequence numbering here.

    Important fixes:
    - Converts CBL NOSQL to CBL ARITH(EXTEND), because generated output uses EXEC SQL.
    - Prevents double period in PROGRAM-ID.
    - Supports both OBTAIN CALC syntaxes:
        OBTAIN <record> CALC
        OBTAIN CALC <record>
    """

    LEFT_SEQUENCE_PATTERN = re.compile(
        r"^\s*\d{6}\s+(?P<body>.*)$",
        flags=re.IGNORECASE,
    )

    RIGHT_SEQUENCE_PATTERN = re.compile(
        r"(?P<body>.*?)(?:\s+(?P<right>\d{8}))\s*$",
        flags=re.IGNORECASE,
    )

    SEQUENCE_ONLY_PATTERN = re.compile(
        r"^\s*(\d{6}|\d{8})\s*$",
        flags=re.IGNORECASE,
    )

    PROGRAM_ID_PATTERN = re.compile(
        r"PROGRAM-ID\.\s*[A-Z0-9-]+\.?",
        flags=re.IGNORECASE,
    )

    END_PROGRAM_PATTERN = re.compile(
        r"^\s*END\s+PROGRAM\b.*$",
        flags=re.IGNORECASE | re.MULTILINE,
    )

    SQL_ERROR_PARAGRAPH_PATTERN = re.compile(
        r"^\s*(SQL-ERROR|SQLERROR)\.\s*$",
        flags=re.IGNORECASE | re.MULTILINE,
    )

    SQL_ERROR_DOT_PATTERN = re.compile(
        r"\bPERFORM\s+(SQL-ERROR|SQLERROR)\s*\.?",
        flags=re.IGNORECASE,
    )

    DIVISION_PATTERN = re.compile(
        r"^\s*(IDENTIFICATION|ENVIRONMENT|DATA|PROCEDURE)\s+DIVISION\b",
        flags=re.IGNORECASE,
    )

    PARAGRAPH_PATTERN = re.compile(
        r"^\s*(?P<name>[A-Z0-9][A-Z0-9-]*)\.\s*$",
        flags=re.IGNORECASE,
    )

    NON_PARAGRAPH_SINGLE_WORDS = {
        "CONTINUE",
        "ELSE",
        "END-IF",
        "END-EVALUATE",
        "END-EXEC",
        "EXIT",
        "GOBACK",
        "STOP",
        "EJECT",
        "SKIP1",
        "SKIP2",
        "SKIP3",
        "SPACE",
        "SPACES",
        "RETURN",
    }

    def __init__(
        self,
        sql_generator: SqlGenerator,
    ) -> None:
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
        current_division = ""

        for raw_line in cobol_text.splitlines():
            logical_line = self._logical_line(
                raw_line,
            )

            detected_division = self._detect_division(
                logical_line,
            )

            if detected_division:
                current_division = detected_division

            converted_lines, _opened_set = self._convert_line(
                line=raw_line,
                target_program_id=target_program_id,
                validation_messages=validation_messages,
                current_division=current_division,
            )

            output_lines.extend(
                converted_lines,
            )

        converted = "\n".join(
            output_lines,
        )

        converted = self._rewrite_db_end_of_set_references(
            text=converted,
            operations=operations,
        )

        converted = self._ensure_cursor_close_calls(
            text=converted,
            operations=operations,
        )

        converted = self._remove_existing_standalone_sqlca(
            converted,
        )

        converted = self._replace_program_id(
            text=converted,
            target_program_id=target_program_id,
        )

        converted = self._cleanup(
            converted,
        )

        return converted, validation_messages, operations

    #
    # Line conversion
    #
    def _convert_line(
        self,
        line: str,
        target_program_id: str,
        validation_messages: list[str],
        current_division: str,
    ) -> tuple[list[str], str]:
        logical_line = self._logical_line(
            line,
        )
        stripped_line = logical_line.strip()
        upper = stripped_line.upper()

        if not stripped_line:
            return [""], ""

        if stripped_line.startswith("*"):
            return [stripped_line], ""

        if upper.startswith("CBL "):
            if "NOSQL" in upper:
                return ["CBL ARITH(EXTEND)"], ""
            return [stripped_line], ""

        if target_program_id and self.PROGRAM_ID_PATTERN.search(stripped_line):
            return [
                f"PROGRAM-ID. {target_program_id.strip().upper()}."
            ], ""

        if self._is_idms_declarative_or_control_statement(
            upper,
        ):
            return self._removed_idms_declarative_lines(
                message=f"* DB2: Removed residual IDMS control statement: {stripped_line}",
            ), ""

        if self._is_idms_status_perform(
            upper,
        ) or self._is_idms_abort_perform(
            upper,
        ):
            return self._removed_idms_executable_lines(
                message=f"* DB2: Removed IDMS status/abort paragraph call: {stripped_line}",
                current_division=current_division,
            ), ""

        if self._is_idms_bind_statement(
            upper,
        ):
            return self._removed_idms_executable_lines(
                message=f"* DB2: Removed IDMS BIND statement: {stripped_line}",
                current_division=current_division,
            ), ""

        if self._is_usage_mode_statement(
            upper,
        ):
            return self._removed_idms_executable_lines(
                message=f"* DB2: Removed IDMS usage mode statement: {stripped_line}",
                current_division=current_division,
            ), ""

        if self._is_find_current_statement(
            upper,
        ):
            return self._removed_idms_executable_lines(
                message=f"* DB2: Removed IDMS FIND CURRENT statement: {stripped_line}",
                current_division=current_division,
            ), ""

        if self._is_idms_connect_or_disconnect(
            upper,
        ):
            return self._removed_idms_executable_lines(
                message=f"* DB2: Removed IDMS relationship statement: {stripped_line}",
                current_division=current_division,
            ), ""

        if self._is_finish_statement(
            upper,
        ):
            if current_division != "PROCEDURE":
                return [
                    f"* DB2: FINISH ignored outside PROCEDURE DIVISION: {stripped_line}",
                ], ""

            return [
                "* DB2: IDMS FINISH converted to COMMIT.",
                "MOVE 'COMMIT' TO SQL-LOCATION.",
                "EXEC SQL",
                "    COMMIT",
                "END-EXEC.",
            ], ""

        if self._is_commit_statement(
            upper,
        ):
            if current_division != "PROCEDURE":
                return [
                    f"* DB2: COMMIT ignored outside PROCEDURE DIVISION: {stripped_line}",
                ], ""

            return [
                "MOVE 'COMMIT' TO SQL-LOCATION.",
                "EXEC SQL",
                "    COMMIT",
                "END-EXEC.",
            ], ""

        db_not_found_match = re.match(
            r"^\s*ON\s+DB-REC-NOT-FOUND\s+(?P<statement>.+?)\.?\s*$",
            stripped_line,
            flags=re.IGNORECASE,
        )

        if db_not_found_match:
            statement = db_not_found_match.group("statement").strip()

            return [
                "IF SQLCODE = 100",
                f"    {statement}",
                "END-IF.",
            ], ""

        obtain_calc_match = re.search(
            r"\bOBTAIN\s+(?:KEEP\s+)?(?P<record1>[A-Z0-9-]+)\s+CALC\b",
            upper,
            flags=re.IGNORECASE,
        )

        if not obtain_calc_match:
            obtain_calc_match = re.search(
                r"\bOBTAIN\s+(?:KEEP\s+)?CALC\s+(?P<record1>[A-Z0-9-]+)\b",
                upper,
                flags=re.IGNORECASE,
            )

        if obtain_calc_match:
            record = obtain_calc_match.group("record1").upper()

            if current_division != "PROCEDURE":
                return [
                    f"* DB2: OBTAIN CALC ignored outside PROCEDURE DIVISION: {stripped_line}",
                ], ""

            return [
                f"* DB2: Converted OBTAIN CALC for {record}.",
                *self.sql_generator.select_by_key(
                    record,
                ),
                "IF SQLCODE NOT = 0 AND SQLCODE NOT = 100",
                f"    PERFORM {self.sql_error_paragraph}.",
                "END-IF.",
            ], ""

        obtain_set_match = re.search(
            r"\bOBTAIN\s+(?:KEEP\s+)?(?P<first_next>FIRST|NEXT)\s+"
            r"(?P<record>[A-Z0-9-]+)\s+WITHIN\s+(?P<set>[A-Z0-9-]+)\b",
            upper,
            flags=re.IGNORECASE,
        )

        if obtain_set_match:
            first_or_next = obtain_set_match.group("first_next").upper()
            record = obtain_set_match.group("record").upper()
            set_name = obtain_set_match.group("set").upper()

            if current_division != "PROCEDURE":
                return [
                    f"* DB2: OBTAIN {first_or_next} ignored outside PROCEDURE DIVISION: {stripped_line}",
                ], ""

            lines: list[str] = [
                f"* DB2: Converted OBTAIN {first_or_next} {record} WITHIN {set_name}.",
            ]

            relationship_condition_found = (
                self.sql_generator.has_cursor_relationship_condition(
                    record_name=record,
                    set_name=set_name,
                )
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
                    "IF SQLCODE NOT = 0 AND SQLCODE NOT = 100",
                    f"    PERFORM {self.sql_error_paragraph}.",
                    "END-IF.",
                ]
            )

            if (
                not relationship_condition_found
                and self._looks_like_child_set(set_name)
            ):
                validation_messages.append(
                    f"Cursor WHERE clause for child set {set_name} could not be generated from Sheet Mapping relation/FK rows."
                )

            return lines, opened_set

        find_first_match = re.search(
            r"\bFIND\s+FIRST\s+(?P<record>[A-Z0-9-]+)?\s*WITHIN\s+(?P<set>[A-Z0-9-]+)\b",
            upper,
            flags=re.IGNORECASE,
        )

        if find_first_match:
            record = (find_first_match.group("record") or "").upper()
            set_name = find_first_match.group("set").upper()

            if current_division != "PROCEDURE":
                return [
                    f"* DB2: FIND FIRST ignored outside PROCEDURE DIVISION: {stripped_line}",
                ], ""

            if record:
                return [
                    f"* DB2: Converted FIND FIRST {record} WITHIN {set_name}.",
                    *self.sql_generator.open_cursor(
                        set_name,
                    ),
                    *self.sql_generator.fetch_cursor(
                        record_name=record,
                        set_name=set_name,
                    ),
                    "IF SQLCODE NOT = 0 AND SQLCODE NOT = 100",
                    f"    PERFORM {self.sql_error_paragraph}.",
                    "END-IF.",
                ], set_name

            validation_messages.append(
                f"FIND FIRST WITHIN {set_name} needs record inference from source code or mapping."
            )

            return [
                f"* DB2: FIND FIRST WITHIN {set_name} could not be converted because record name was not found.",
                "CONTINUE.",
            ], ""

        store_match = re.search(
            r"\bSTORE\s+(?P<record>[A-Z0-9-]+)\b",
            upper,
            flags=re.IGNORECASE,
        )

        if store_match:
            record = store_match.group("record").upper()

            if current_division != "PROCEDURE":
                return [
                    f"* DB2: STORE ignored outside PROCEDURE DIVISION: {stripped_line}",
                ], ""

            return [
                f"* DB2: Converted STORE for {record}.",
                *self.sql_generator.insert(
                    record,
                ),
                "IF SQLCODE NOT = 0",
                f"    PERFORM {self.sql_error_paragraph}.",
                "END-IF.",
            ], ""

        modify_match = re.search(
            r"\bMODIFY\s+(?P<record>[A-Z0-9-]+)\b",
            upper,
            flags=re.IGNORECASE,
        )

        if modify_match:
            record = modify_match.group("record").upper()

            if current_division != "PROCEDURE":
                return [
                    f"* DB2: MODIFY ignored outside PROCEDURE DIVISION: {stripped_line}",
                ], ""

            return [
                f"* DB2: Converted MODIFY for {record}.",
                *self.sql_generator.update(
                    record,
                ),
                "IF SQLCODE NOT = 0",
                f"    PERFORM {self.sql_error_paragraph}.",
                "END-IF.",
            ], ""

        erase_match = re.search(
            r"\bERASE\s+(?P<record>[A-Z0-9-]+)\b",
            upper,
            flags=re.IGNORECASE,
        )

        if erase_match:
            record = erase_match.group("record").upper()

            if current_division != "PROCEDURE":
                return [
                    f"* DB2: ERASE ignored outside PROCEDURE DIVISION: {stripped_line}",
                ], ""

            return [
                f"* DB2: Converted ERASE for {record}.",
                *self.sql_generator.delete(
                    record,
                ),
                "IF SQLCODE NOT = 0",
                f"    PERFORM {self.sql_error_paragraph}.",
                "END-IF.",
            ], ""

        return [
            stripped_line,
        ], ""

    #
    # DB-END-OF-SET and cursor close post-processing
    #
    def _rewrite_db_end_of_set_references(
        self,
        text: str,
        operations: list[IdmsOperation],
    ) -> str:
        if not text:
            return ""

        paragraph_cursor_map = self._paragraph_cursor_map(
            operations,
        )
        root_cursor = self._root_cursor_name(
            operations,
        )

        lines = text.splitlines()
        output: list[str] = []
        current_paragraph = ""
        previous_perform_paragraph = ""

        for raw_line in lines:
            logical = self._logical_line_for_cursor_close(
                raw_line,
            )

            paragraph_name = self._paragraph_name_from_logical_line(
                logical,
            )

            if paragraph_name:
                current_paragraph = paragraph_name

            perform_match = re.match(
                r"^\s*PERFORM\s+(?P<paragraph>[A-Z0-9][A-Z0-9-]*)\s*\.?\s*$",
                logical,
                flags=re.IGNORECASE,
            )

            if perform_match:
                previous_perform_paragraph = perform_match.group("paragraph").upper()

            replacement_cursor = ""

            if "DB-END-OF-SET" in logical.upper():
                if logical.upper().startswith("UNTIL "):
                    replacement_cursor = paragraph_cursor_map.get(
                        previous_perform_paragraph,
                        "",
                    )

                    if not replacement_cursor:
                        replacement_cursor = root_cursor
                else:
                    replacement_cursor = paragraph_cursor_map.get(
                        current_paragraph,
                        "",
                    )

                    if not replacement_cursor:
                        replacement_cursor = root_cursor

                if replacement_cursor:
                    raw_line = re.sub(
                        r"\bDB-END-OF-SET\b",
                        f"{replacement_cursor}-EOC",
                        raw_line,
                        flags=re.IGNORECASE,
                    )

            output.append(
                raw_line,
            )

        return "\n".join(
            output,
        )

    def _ensure_cursor_close_calls(
        self,
        text: str,
        operations: list[IdmsOperation],
    ) -> str:
        if not text:
            return ""

        paragraph_cursor_map = self._paragraph_cursor_map(
            operations,
        )
        root_cursor = self._root_cursor_name(
            operations,
        )
        cursor_close_call_map = self._cursor_close_call_map(
            operations,
        )

        lines = text.splitlines()
        output: list[str] = []
        previous_perform_paragraph = ""

        for index, line in enumerate(lines):
            output.append(
                line,
            )

            logical = self._logical_line_for_cursor_close(
                line,
            )

            perform_match = re.match(
                r"^\s*PERFORM\s+(?P<paragraph>[A-Z0-9][A-Z0-9-]*)\s*\.?\s*$",
                logical,
                flags=re.IGNORECASE,
            )

            if perform_match:
                previous_perform_paragraph = perform_match.group("paragraph").upper()

            upper = logical.upper()

            if not upper.startswith("UNTIL "):
                continue

            eoc_match = re.search(
                r"\b(?P<cursor>[A-Z0-9-]+)-EOC\b",
                upper,
                flags=re.IGNORECASE,
            )

            if not eoc_match:
                continue

            cursor_name = eoc_match.group("cursor").upper()

            expected_cursor = paragraph_cursor_map.get(
                previous_perform_paragraph,
                "",
            )

            if not expected_cursor:
                expected_cursor = root_cursor

            if expected_cursor and cursor_name != expected_cursor.upper():
                continue

            close_call = cursor_close_call_map.get(
                cursor_name,
                "",
            )

            if not close_call:
                continue

            if self._close_call_already_nearby(
                lines=lines,
                current_index=index,
                close_call=close_call,
            ):
                continue

            output.append(
                close_call,
            )

        return "\n".join(
            output,
        )

    def _paragraph_cursor_map(
        self,
        operations: list[IdmsOperation],
    ) -> dict[str, str]:
        mapping: dict[str, str] = {}

        for operation in operations or []:
            operation_name = str(operation.operation or "").upper()

            if operation_name not in {
                "OBTAIN_FIRST",
                "OBTAIN_NEXT",
                "FIND_FIRST",
            }:
                continue

            record_name = NameNormalizer.to_cobol(
                operation.record_name,
            ).upper()

            set_name = str(operation.set_name or "")

            if not record_name or not set_name:
                continue

            self.sql_generator.fetch_cursor(
                record_name=record_name,
                set_name=set_name,
            )

            cursor_name = self.sql_generator.cursor_name(
                set_name,
            ).upper()

            if not cursor_name:
                continue

            paragraph_name = f"VERWERK-{record_name}".upper()
            mapping[paragraph_name] = cursor_name

        root_cursor = self._root_cursor_name(
            operations,
        )

        if root_cursor:
            mapping.setdefault(
                "HOOFDVERWERKING",
                root_cursor,
            )

        return mapping

    def _root_cursor_name(
        self,
        operations: list[IdmsOperation],
    ) -> str:
        for operation in operations or []:
            operation_name = str(operation.operation or "").upper()

            if operation_name not in {
                "OBTAIN_FIRST",
                "FIND_FIRST",
            }:
                continue

            set_name = str(operation.set_name or "")

            if not set_name:
                continue

            if not self._looks_like_child_set(
                set_name,
            ):
                record_name = str(operation.record_name or "")

                if record_name:
                    self.sql_generator.fetch_cursor(
                        record_name=record_name,
                        set_name=set_name,
                    )

                return self.sql_generator.cursor_name(
                    set_name,
                ).upper()

        return ""

    def _cursor_close_call_map(
        self,
        operations: list[IdmsOperation],
    ) -> dict[str, str]:
        mapping: dict[str, str] = {}

        for operation in operations or []:
            operation_name = str(operation.operation or "").upper()

            if operation_name not in {
                "OBTAIN_FIRST",
                "OBTAIN_NEXT",
                "FIND_FIRST",
            }:
                continue

            set_name = str(operation.set_name or "")
            record_name = str(operation.record_name or "")

            if not set_name:
                continue

            if record_name:
                self.sql_generator.fetch_cursor(
                    record_name=record_name,
                    set_name=set_name,
                )

            cursor_name = self.sql_generator.cursor_name(
                set_name,
            ).upper()

            close_call = self.sql_generator.close_cursor(
                set_name,
            )

            if cursor_name and close_call:
                mapping[cursor_name] = close_call[0]

        return mapping

    def _paragraph_name_from_logical_line(
        self,
        logical: str,
    ) -> str:
        text = str(logical or "").strip()

        if not text:
            return ""

        match = self.PARAGRAPH_PATTERN.match(
            text,
        )

        if not match:
            return ""

        name = match.group("name").upper()

        if name in self.NON_PARAGRAPH_SINGLE_WORDS:
            return ""

        if name.startswith("END-"):
            return ""

        if name in {
            "IF",
            "ELSE",
            "WHEN",
            "MOVE",
            "DISPLAY",
            "PERFORM",
            "CALL",
            "OPEN",
            "CLOSE",
            "READ",
            "WRITE",
            "EXEC",
            "SELECT",
            "FETCH",
            "CONTINUE",
            "EVALUATE",
        }:
            return ""

        return name

    def _logical_line_for_cursor_close(
        self,
        line: str,
    ) -> str:
        return self._logical_line(
            line,
        )

    def _close_call_already_nearby(
        self,
        lines: list[str],
        current_index: int,
        close_call: str,
    ) -> bool:
        close_upper = str(close_call or "").strip().upper()

        if not close_upper:
            return True

        start = max(
            0,
            current_index - 3,
        )
        end = min(
            len(lines),
            current_index + 10,
        )

        for index in range(start, end):
            logical = self._logical_line_for_cursor_close(
                lines[index],
            ).upper()

            if logical == close_upper:
                return True

        return False

    #
    # Cleanup and utility methods
    #
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

    def _replace_program_id(
        self,
        text: str,
        target_program_id: str,
    ) -> str:
        if not target_program_id:
            return text

        return self.PROGRAM_ID_PATTERN.sub(
            f"PROGRAM-ID. {target_program_id.strip().upper()}.",
            text,
            count=1,
        )

    def _cleanup(
        self,
        text: str,
    ) -> str:
        if not text:
            return ""

        cleaned = text.replace(
            "\r\n",
            "\n",
        ).replace(
            "\r",
            "\n",
        )

        cleaned = re.sub(
            r"\n{4,}",
            "\n\n\n",
            cleaned,
        )

        cleaned = self._normalize_perform_sql_error(
            cleaned,
        )

        cleaned = re.sub(
            r"\bPROGRAM-ID\.\s+([A-Z0-9-]+)\.\.+",
            r"PROGRAM-ID. \1.",
            cleaned,
            flags=re.IGNORECASE,
        )

        return cleaned.rstrip() + "\n"

    def _normalize_perform_sql_error(
        self,
        text: str,
    ) -> str:
        return self.SQL_ERROR_DOT_PATTERN.sub(
            f"PERFORM {self.sql_error_paragraph}.",
            text,
        )

    def _logical_line(
        self,
        line: str,
    ) -> str:
        text = str(line or "").rstrip()

        if self.SEQUENCE_ONLY_PATTERN.fullmatch(
            text,
        ):
            return ""

        right_match = self.RIGHT_SEQUENCE_PATTERN.match(
            text,
        )

        if right_match:
            right = right_match.group("right")

            if right:
                text = right_match.group("body").rstrip()

        left_match = self.LEFT_SEQUENCE_PATTERN.match(
            text,
        )

        if left_match:
            text = left_match.group("body").rstrip()

        return text.strip()

    def _detect_division(
        self,
        logical_line: str,
    ) -> str:
        match = self.DIVISION_PATTERN.match(
            logical_line or "",
        )

        if not match:
            return ""

        return match.group(1).upper()

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

    #
    # IDMS detection
    #
    def _is_idms_declarative_or_control_statement(
        self,
        upper: str,
    ) -> bool:
        if re.search(r"^\s*IDMS-CONTROL\s+SECTION\b", upper):
            return True

        if re.search(r"^\s*PROTOCOL\b", upper):
            return True

        if re.search(r"^\s*IDMS-RECORDS\s+WITHIN\b", upper):
            return True

        if re.search(r"^\s*SCHEMA\s+SECTION\b", upper):
            return True

        if re.search(r"^\s*DB\s+[A-Z0-9-]+\s+WITHIN\s+[A-Z0-9-]+\b", upper):
            return True

        if re.search(r"^\s*COPY\s+IDMS\b", upper):
            return True

        return False

    def _is_idms_bind_statement(
        self,
        upper: str,
    ) -> bool:
        return bool(
            re.search(
                r"^\s*BIND\b",
                upper,
            )
        )

    def _is_usage_mode_statement(
        self,
        upper: str,
    ) -> bool:
        return bool(
            re.search(
                r"^\s*READY\b",
                upper,
            )
            or re.search(
                r"\bUSAGE-MODE\s+IS\b",
                upper,
            )
        )

    def _is_find_current_statement(
        self,
        upper: str,
    ) -> bool:
        return bool(
            re.search(
                r"^\s*FIND\s+CURRENT\b",
                upper,
            )
        )

    def _is_idms_connect_or_disconnect(
        self,
        upper: str,
    ) -> bool:
        return bool(
            re.search(
                r"^\s*CONNECT\b",
                upper,
            )
            or re.search(
                r"^\s*DISCONNECT\b",
                upper,
            )
        )

    def _is_finish_statement(
        self,
        upper: str,
    ) -> bool:
        return bool(
            re.search(
                r"^\s*FINISH\b",
                upper,
            )
        )

    def _is_commit_statement(
        self,
        upper: str,
    ) -> bool:
        return bool(
            re.search(
                r"^\s*COMMIT\b",
                upper,
            )
        )

    def _is_idms_status_perform(
        self,
        upper: str,
    ) -> bool:
        return bool(
            re.search(
                r"\bPERFORM\b.*\bIDMS-STATUS\b",
                upper,
            )
        )

    def _is_idms_abort_perform(
        self,
        upper: str,
    ) -> bool:
        return bool(
            re.search(
                r"\bPERFORM\b.*\bIDMS-ABORT\b",
                upper,
            )
        )

    def _looks_like_child_set(
        self,
        set_name: str,
    ) -> bool:
        normalized = NameNormalizer.normalize(
            set_name,
        )

        if not normalized:
            return False

        parts = [
            part
            for part in normalized.split("_")
            if part
        ]

        if len(parts) < 2:
            return False

        if parts[0] in {
            "AR",
            "AREA",
            "IX",
            "INDEX",
        }:
            return False

        return True

    #
    # Removed-line helpers
    #
    def _removed_idms_declarative_lines(
        self,
        message: str,
    ) -> list[str]:
        return [
            message,
        ]

    def _removed_idms_executable_lines(
        self,
        message: str,
        current_division: str,
    ) -> list[str]:
        if current_division == "PROCEDURE":
            return [
                message,
                "CONTINUE.",
            ]

        return [
            message,
        ]