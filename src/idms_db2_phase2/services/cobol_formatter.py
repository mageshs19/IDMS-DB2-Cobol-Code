import re


class CobolFormatter:
    """
    Final COBOL formatting pass.

    This class does not change business logic.

    It normalizes:
    - indentation
    - generated statement periods
    - 88-level indentation
    - IF / ELSE / END-IF indentation
    - EVALUATE / WHEN / END-EVALUATE indentation
    - END PROGRAM indentation
    - multiline MOVE formatting
    - generated SQL-LOCATION statements
    - SQL-ERROR paragraph fallback body
    - blank lines between paragraphs
    - blank lines inside EXEC SQL blocks

    It preserves:
    - comments
    - author details
    - date-written details
    - remarks
    - business-rule comments
    - paragraph names
    - section names
    """

    COMMENT_INDENT = "      "
    PARAGRAPH_INDENT = "       "
    BASE_INDENT = "           "
    INDENT_STEP = "    "
    SQL_INDENT = "                "
    SQL_CONTINUATION_INDENT = "                    "
    MOVE_TO_INDENT = "                "

    def format(
        self,
        text: str,
    ) -> str:
        if not text:
            return ""

        text = self._normalize_line_endings(
            text,
        )

        text = self._repair_broken_multiline_move(
            text,
        )

        text = self._remove_redundant_generated_continue(
            text,
        )

        text = self._pre_normalize_known_generated_lines(
            text,
        )

        lines = text.splitlines()
        output: list[str] = []

        in_exec_sql = False
        if_depth = 0
        evaluate_stack: list[int] = []
        pending_multiline_move = False

        for raw_line in lines:
            stripped = raw_line.strip()

            if not stripped:
                if in_exec_sql:
                    continue

                if evaluate_stack:
                    continue

                output.append("")
                continue

            upper = stripped.upper()

            if upper.startswith("EXEC SQL"):
                in_exec_sql = True
                output.append(
                    f"{self.BASE_INDENT}EXEC SQL",
                )
                continue

            if upper.startswith("END-EXEC"):
                in_exec_sql = False
                output.append(
                    f"{self.BASE_INDENT}END-EXEC.",
                )
                continue

            if in_exec_sql:
                output.append(
                    self._format_sql_line(
                        stripped,
                    )
                )
                continue

            (
                formatted_line,
                if_depth,
                evaluate_stack,
                pending_multiline_move,
            ) = self._format_cobol_line(
                stripped=stripped,
                if_depth=if_depth,
                evaluate_stack=evaluate_stack,
                pending_multiline_move=pending_multiline_move,
            )

            output.append(
                formatted_line,
            )

        formatted = "\n".join(
            output,
        )

        formatted = self._remove_blank_lines_inside_exec_sql(
            formatted,
        )

        formatted = self._remove_blank_lines_before_end_exec(
            formatted,
        )

        formatted = self._remove_blank_lines_before_end_evaluate(
            formatted,
        )

        formatted = self._post_fix_end_evaluate_indentation(
            formatted,
        )

        formatted = self._post_fix_multiline_move_indentation(
            formatted,
        )

        formatted = self._normalize_program_id_case(
            formatted,
        )

        formatted = self._post_fix_paragraph_spacing(
            formatted,
        )

        formatted = self._post_fix_division_spacing(
            formatted,
        )

        formatted = self._normalize_period_spacing(
            formatted,
        )

        formatted = self._ensure_sql_error_continue(
            formatted,
        )

        formatted = self._normalize_blank_lines(
            formatted,
        )

        return formatted.strip() + "\n"

    def _normalize_line_endings(
        self,
        text: str,
    ) -> str:
        return text.replace(
            "\r\n",
            "\n",
        ).replace(
            "\r",
            "\n",
        )

    def _repair_broken_multiline_move(
        self,
        text: str,
    ) -> str:
        """
        Repairs cases created by previous formatting:

        MOVE DCLX.FIELD.
        TO OUT-FIELD.

        into:

        MOVE DCLX.FIELD
        TO OUT-FIELD.
        """

        lines = text.splitlines()
        output: list[str] = []

        for index, line in enumerate(lines):
            stripped = line.strip()
            upper = stripped.upper()

            if upper.startswith("MOVE ") and stripped.endswith("."):
                next_index = self._next_non_empty_index(
                    lines=lines,
                    start=index + 1,
                )

                if next_index is not None:
                    next_line = lines[next_index].strip()

                    if next_line.upper().startswith("TO "):
                        line = line.rstrip()[:-1]

            output.append(
                line,
            )

        return "\n".join(
            output,
        )

    def _remove_redundant_generated_continue(
        self,
        text: str,
    ) -> str:
        """
        Removes harmless generated CONTINUE lines that appear immediately
        before the generated cursor paragraph marker.
        """

        return re.sub(
            r"\n\s*CONTINUE\.\s*\n\s*\*{10,}\s*\n\s*\*\s*DB2 GENERATED CURSOR OPEN FETCH CLOSE PARAGRAPHS",
            "\n\n"
            "      ******************************************************************\n"
            "      * DB2 GENERATED CURSOR OPEN FETCH CLOSE PARAGRAPHS",
            text,
            flags=re.IGNORECASE,
        )

    def _pre_normalize_known_generated_lines(
        self,
        text: str,
    ) -> str:
        text = re.sub(
            r"(?m)^\s*END-EVALUATE\.\s*$",
            "END-EVALUATE.",
            text,
        )

        text = re.sub(
            r"(?m)^\s*TO\s+",
            "TO ",
            text,
        )

        return text

    def _next_non_empty_index(
        self,
        lines: list[str],
        start: int,
    ) -> int | None:
        for index in range(start, len(lines)):
            if lines[index].strip():
                return index

        return None

    def _format_sql_line(
        self,
        stripped: str,
    ) -> str:
        upper = stripped.upper()

        if stripped.startswith(","):
            return f"{self.SQL_CONTINUATION_INDENT}{stripped}"

        if stripped.startswith(":"):
            return f"{self.SQL_CONTINUATION_INDENT}{stripped}"

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
            return f"{self.SQL_INDENT}{stripped}"

        if upper.startswith("AND "):
            return f"{self.SQL_CONTINUATION_INDENT}{stripped}"

        if upper.startswith("OR "):
            return f"{self.SQL_CONTINUATION_INDENT}{stripped}"

        return f"{self.SQL_CONTINUATION_INDENT}{stripped}"

    def _format_cobol_line(
        self,
        stripped: str,
        if_depth: int,
        evaluate_stack: list[int],
        pending_multiline_move: bool,
    ) -> tuple[str, int, list[int], bool]:
        upper = stripped.upper()

        if self._is_division_or_section_header(
            upper,
        ):
            return f"{self.PARAGRAPH_INDENT}{stripped.upper()}", 0, [], False

        if upper.startswith("PROGRAM-ID."):
            return (
                f"{self.BASE_INDENT}{self._normalize_program_id_text(stripped)}",
                if_depth,
                evaluate_stack,
                False,
            )

        if upper.startswith("END PROGRAM "):
            return (
                f"{self.PARAGRAPH_INDENT}{self._ensure_period(stripped.upper())}",
                0,
                [],
                False,
            )

        if self._is_paragraph_header(
            stripped,
        ):
            return f"{self.PARAGRAPH_INDENT}{stripped.upper()}", 0, [], False

        if upper.startswith("*"):
            return (
                f"{self.COMMENT_INDENT}{stripped}",
                if_depth,
                evaluate_stack,
                pending_multiline_move,
            )

        if self._is_data_level_line(
            upper,
        ):
            return (
                f"{self.BASE_INDENT}{stripped}",
                if_depth,
                evaluate_stack,
                False,
            )

        if upper.startswith("EVALUATE "):
            evaluate_stack.append(
                if_depth,
            )

            return (
                f"{self._statement_indent(if_depth)}{stripped.rstrip('.')}",
                if_depth,
                evaluate_stack,
                False,
            )

        if upper.startswith("WHEN "):
            evaluate_depth = evaluate_stack[-1] if evaluate_stack else if_depth

            return (
                f"{self._statement_indent(evaluate_depth + 1)}{stripped}",
                if_depth,
                evaluate_stack,
                False,
            )

        if upper.startswith("END-EVALUATE"):
            evaluate_depth = evaluate_stack.pop() if evaluate_stack else 0

            return (
                f"{self._statement_indent(evaluate_depth)}END-EVALUATE.",
                if_depth,
                evaluate_stack,
                False,
            )

        if evaluate_stack:
            evaluate_depth = evaluate_stack[-1]

            return self._format_evaluate_body_line(
                stripped=stripped,
                if_depth=if_depth,
                evaluate_depth=evaluate_depth,
                evaluate_stack=evaluate_stack,
            )

        if upper.startswith("IF "):
            return (
                f"{self._statement_indent(if_depth)}{stripped}",
                if_depth + 1,
                evaluate_stack,
                False,
            )

        if upper == "ELSE":
            adjusted_depth = max(
                if_depth - 1,
                0,
            )

            return (
                f"{self._statement_indent(adjusted_depth)}{stripped}",
                if_depth,
                evaluate_stack,
                False,
            )

        if upper.startswith("END-IF"):
            new_if_depth = max(
                if_depth - 1,
                0,
            )

            return (
                f"{self._statement_indent(new_if_depth)}END-IF.",
                new_if_depth,
                evaluate_stack,
                False,
            )

        if pending_multiline_move:
            if upper.startswith("TO "):
                return (
                    f"{self.MOVE_TO_INDENT}{self._ensure_period(stripped)}",
                    if_depth,
                    evaluate_stack,
                    False,
                )

            return (
                f"{self._statement_indent(if_depth)}{stripped}",
                if_depth,
                evaluate_stack,
                pending_multiline_move,
            )

        if upper.startswith("MOVE "):
            if " TO " in upper:
                return (
                    f"{self._statement_indent(if_depth)}{self._ensure_period(stripped)}",
                    if_depth,
                    evaluate_stack,
                    False,
                )

            return (
                f"{self._statement_indent(if_depth)}{stripped}",
                if_depth,
                evaluate_stack,
                True,
            )

        if self._needs_period(
            upper,
        ):
            return (
                f"{self._statement_indent(if_depth)}{self._ensure_period(stripped)}",
                if_depth,
                evaluate_stack,
                False,
            )

        return (
            f"{self._statement_indent(if_depth)}{stripped}",
            if_depth,
            evaluate_stack,
            pending_multiline_move,
        )

    def _format_evaluate_body_line(
        self,
        stripped: str,
        if_depth: int,
        evaluate_depth: int,
        evaluate_stack: list[int],
    ) -> tuple[str, int, list[int], bool]:
        upper = stripped.upper()
        body_depth = evaluate_depth + 2

        if upper.startswith("IF "):
            return (
                f"{self._statement_indent(body_depth + if_depth)}{stripped}",
                if_depth + 1,
                evaluate_stack,
                False,
            )

        if upper == "ELSE":
            adjusted_if_depth = max(
                if_depth - 1,
                0,
            )

            return (
                f"{self._statement_indent(body_depth + adjusted_if_depth)}{stripped}",
                if_depth,
                evaluate_stack,
                False,
            )

        if upper.startswith("END-IF"):
            new_if_depth = max(
                if_depth - 1,
                0,
            )

            return (
                f"{self._statement_indent(body_depth + new_if_depth)}END-IF.",
                new_if_depth,
                evaluate_stack,
                False,
            )

        if self._needs_period(
            upper,
        ):
            return (
                f"{self._statement_indent(body_depth + if_depth)}{self._ensure_period(stripped)}",
                if_depth,
                evaluate_stack,
                False,
            )

        return (
            f"{self._statement_indent(body_depth + if_depth)}{stripped}",
            if_depth,
            evaluate_stack,
            False,
        )

    def _remove_blank_lines_inside_exec_sql(
        self,
        text: str,
    ) -> str:
        lines = text.splitlines()
        output: list[str] = []
        in_exec_sql = False

        for line in lines:
            stripped = line.strip()
            upper = stripped.upper()

            if upper.startswith("EXEC SQL"):
                in_exec_sql = True
                output.append(line)
                continue

            if upper.startswith("END-EXEC"):
                in_exec_sql = False
                output.append(line)
                continue

            if in_exec_sql and not stripped:
                continue

            output.append(line)

        return "\n".join(
            output,
        )

    def _remove_blank_lines_before_end_exec(
        self,
        text: str,
    ) -> str:
        return re.sub(
            r"\n\s*\n(\s*END-EXEC\.)",
            r"\n\1",
            text,
            flags=re.IGNORECASE,
        )

    def _remove_blank_lines_before_end_evaluate(
        self,
        text: str,
    ) -> str:
        return re.sub(
            r"\n\s*\n(\s*END-EVALUATE\.)",
            r"\n\1",
            text,
            flags=re.IGNORECASE,
        )

    def _post_fix_end_evaluate_indentation(
        self,
        text: str,
    ) -> str:
        return re.sub(
            r"(?m)^\s*END-EVALUATE\.\s*$",
            f"{self.BASE_INDENT}END-EVALUATE.",
            text,
        )

    def _post_fix_multiline_move_indentation(
        self,
        text: str,
    ) -> str:
        return re.sub(
            r"(?m)^(\s*MOVE\s+[^\n]+)\n\s*(TO\s+[A-Z0-9-]+\.?)",
            rf"\1\n{self.MOVE_TO_INDENT}\2",
            text,
            flags=re.IGNORECASE,
        )

    def _normalize_program_id_case(
        self,
        text: str,
    ) -> str:
        return re.sub(
            r"(?im)^(\s*PROGRAM-ID\.\s*)([A-Z0-9-]+)(\.)?\s*$",
            lambda match: (
                f"{match.group(1).upper()}"
                f"{match.group(2).upper()}."
            ),
            text,
        )

    def _post_fix_paragraph_spacing(
        self,
        text: str,
    ) -> str:
        lines = text.splitlines()
        output: list[str] = []

        for line in lines:
            stripped = line.strip()
            upper = stripped.upper()

            if (
                self._is_paragraph_header(stripped)
                and output
                and output[-1].strip()
                and not output[-1].strip().upper().endswith("DIVISION.")
                and not output[-1].strip().upper().endswith("SECTION.")
                and not output[-1].strip().startswith("*")
                and upper not in {
                    "END-EXEC.",
                    "END-EVALUATE.",
                    "END-IF.",
                }
            ):
                output.append("")

            output.append(line)

        return "\n".join(output)

    def _post_fix_division_spacing(
        self,
        text: str,
    ) -> str:
        text = re.sub(
            r"(?m)^(           PROGRAM-ID\.[^\n]+)\n(       ENVIRONMENT DIVISION\.)",
            r"\1\n\n\2",
            text,
        )

        text = re.sub(
            r"(?m)^(       ENVIRONMENT DIVISION\.)\n(       DATA DIVISION\.)",
            r"\1\n\n\2",
            text,
        )

        return text

    def _statement_indent(
        self,
        depth: int,
    ) -> str:
        if depth <= 0:
            return self.BASE_INDENT

        return self.BASE_INDENT + (self.INDENT_STEP * depth)

    def _normalize_program_id_text(
        self,
        stripped: str,
    ) -> str:
        match = re.search(
            r"PROGRAM-ID\.\s*([A-Z0-9-]+)",
            stripped,
            flags=re.IGNORECASE,
        )

        if not match:
            return stripped.upper()

        return f"PROGRAM-ID. {match.group(1).upper()}."

    def _is_division_or_section_header(
        self,
        upper: str,
    ) -> bool:
        return upper in {
            "IDENTIFICATION DIVISION.",
            "ENVIRONMENT DIVISION.",
            "DATA DIVISION.",
            "PROCEDURE DIVISION.",
            "CONFIGURATION SECTION.",
            "INPUT-OUTPUT SECTION.",
            "FILE SECTION.",
            "WORKING-STORAGE SECTION.",
            "LINKAGE SECTION.",
        }

    def _is_paragraph_header(
        self,
        stripped: str,
    ) -> bool:
        if not stripped.endswith("."):
            return False

        upper = stripped.upper()

        if " " in upper:
            return False

        if upper.startswith(
            (
                "IF",
                "ELSE",
                "END-IF",
                "MOVE",
                "PERFORM",
                "DISPLAY",
                "SET",
                "CONTINUE",
                "WHEN",
                "EVALUATE",
                "GOBACK",
                "EXIT",
            )
        ):
            return False

        return bool(
            re.fullmatch(
                r"[A-Z0-9-]+\.",
                upper,
            )
        )

    def _is_data_level_line(
        self,
        upper: str,
    ) -> bool:
        return bool(
            re.match(
                r"^(0[1-9]|[1-4][0-9]|66|77|88)\s+",
                upper,
            )
        )

    def _needs_period(
        self,
        upper: str,
    ) -> bool:
        return upper.startswith(
            (
                "PERFORM ",
                "DISPLAY ",
                "CONTINUE",
                "SET ",
                "GOBACK",
                "EXIT",
            )
        )

    def _ensure_period(
        self,
        stripped: str,
    ) -> str:
        if stripped.endswith("."):
            return stripped

        return stripped + "."

    def _ensure_sql_error_continue(
        self,
        text: str,
    ) -> str:
        pattern = (
            r"(SQL-ERROR\.\s*\n"
            r"\s*DISPLAY\s+'DB2 SQL ERROR SQLCODE='\s+SQLCODE\.\s*\n"
            r"\s*DISPLAY\s+'DB2 SQL ERROR LOCATION='\s+SQL-LOCATION\.)"
            r"(?!\s*\n\s*CONTINUE\.)"
        )

        return re.sub(
            pattern,
            r"\1\n           CONTINUE.",
            text,
            flags=re.IGNORECASE,
        )

    def _normalize_period_spacing(
        self,
        text: str,
    ) -> str:
        return re.sub(
            r"\s+\.",
            ".",
            text,
        )

    def _normalize_blank_lines(
        self,
        text: str,
    ) -> str:
        return re.sub(
            r"\n{3,}",
            "\n\n",
            text,
        )