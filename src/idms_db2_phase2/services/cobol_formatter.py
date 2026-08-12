from __future__ import annotations

import re


class CobolFormatter:
    """
    Final COBOL formatting pass.

    Scope:
    - Formatting only.
    - No IDMS-to-DB2 logic.
    - No field-name rewrite.
    - No table-name rewrite.
    - No DCLGEN name rewrite.
    - No business-rule changes.

    Responsibilities:
    - Preserve COBOL comments.
    - Format DATA DIVISION level indentation.
    - Format EXEC SQL indentation.
    - Format PROCEDURE DIVISION IF / ELSE / END-IF indentation.
    - Format EVALUATE / WHEN / END-EVALUATE indentation.
    - Preserve generated names exactly.

    Important fix:
    - END-IF. and END-EVALUATE. must not be treated as paragraph names.
      Control-flow keywords are handled before paragraph detection.
    """

    LEFT_SEQUENCE_PATTERN = re.compile(
        r"^\s*(?P<left>\d{6})\s+"
        r"(?P<body>.*?)"
        r"(?:\s+(?P<right>\d{8}))?\s*$",
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

    DIVISION_PATTERN = re.compile(
        r"^\s*(IDENTIFICATION|ENVIRONMENT|DATA|PROCEDURE)\s+DIVISION\b.*$",
        flags=re.IGNORECASE,
    )

    SECTION_PATTERN = re.compile(
        r"^\s*[A-Z0-9-]+\s+SECTION\.\s*$",
        flags=re.IGNORECASE,
    )

    EXEC_SQL_PATTERN = re.compile(
        r"^\s*EXEC\s+SQL\b",
        flags=re.IGNORECASE,
    )

    END_EXEC_PATTERN = re.compile(
        r"^\s*END-EXEC\.?\s*$",
        flags=re.IGNORECASE,
    )

    DATA_LEVEL_PATTERN = re.compile(
        r"^\s*(?P<level>0[1-9]|[1-4][0-9]|66|77|88)\s+"
        r"(?P<rest>.*)$",
        flags=re.IGNORECASE,
    )

    FILE_DESCRIPTOR_PATTERN = re.compile(
        r"^\s*(FD|SD)\s+",
        flags=re.IGNORECASE,
    )

    SELECT_FILE_PATTERN = re.compile(
        r"^\s*SELECT\s+",
        flags=re.IGNORECASE,
    )

    PARAGRAPH_PATTERN = re.compile(
        r"^\s*(?P<name>[A-Z0-9][A-Z0-9-]*)\.\s*$",
        flags=re.IGNORECASE,
    )

    COMMENT_PATTERN = re.compile(
        r"^\s*\*",
        flags=re.IGNORECASE,
    )

    PAGE_EJECT_PATTERN = re.compile(
        r"^\s*/\s*$",
        flags=re.IGNORECASE,
    )

    SQL_KEYWORDS_ZERO_INDENT = (
        "EXEC SQL",
        "END-EXEC",
    )

    SQL_KEYWORDS_LEVEL_1 = (
        "INCLUDE ",
        "DECLARE ",
        "SELECT",
        "INTO",
        "FROM ",
        "WHERE",
        "ORDER BY",
        "GROUP BY",
        "HAVING",
        "FETCH ",
        "OPEN ",
        "CLOSE ",
        "COMMIT",
        "ROLLBACK",
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "SET ",
        "VALUES",
        "FOR READ ONLY",
    )

    SQL_KEYWORDS_LEVEL_2 = (
        "AND ",
        "OR ",
    )

    NON_PARAGRAPH_SINGLE_WORDS = {
        "ACCEPT",
        "ADD",
        "ALTER",
        "CALL",
        "CANCEL",
        "CLOSE",
        "COMMIT",
        "COMPUTE",
        "CONTINUE",
        "DELETE",
        "DISPLAY",
        "DIVIDE",
        "ELSE",
        "END",
        "END-ADD",
        "END-CALL",
        "END-COMPUTE",
        "END-DELETE",
        "END-DIVIDE",
        "END-EVALUATE",
        "END-EXEC",
        "END-IF",
        "END-MULTIPLY",
        "END-PERFORM",
        "END-READ",
        "END-RETURN",
        "END-REWRITE",
        "END-SEARCH",
        "END-START",
        "END-STRING",
        "END-SUBTRACT",
        "END-UNSTRING",
        "END-WRITE",
        "ENTRY",
        "EVALUATE",
        "EXEC",
        "EXIT",
        "FETCH",
        "GOBACK",
        "GO",
        "IF",
        "INITIALIZE",
        "INSPECT",
        "MOVE",
        "MULTIPLY",
        "NEXT",
        "OPEN",
        "PERFORM",
        "READ",
        "RETURN",
        "REWRITE",
        "ROLLBACK",
        "SEARCH",
        "SET",
        "SORT",
        "START",
        "STOP",
        "STRING",
        "SUBTRACT",
        "UNSTRING",
        "WHEN",
        "WRITE",
    }

    def format(
        self,
        text: str,
    ) -> str:
        if not text:
            return ""

        normalized_text = self._normalize_line_endings(
            text,
        )

        output_lines: list[str] = []
        current_division = ""
        in_exec_sql = False
        data_level_stack: list[int] = []
        procedure_indent = 0
        evaluate_depth = 0

        for raw_line in normalized_text.splitlines():
            left_sequence, body, right_sequence = self._split_sequence(
                raw_line,
            )

            if not body.strip():
                output_lines.append("")
                continue

            stripped = body.strip()
            upper = stripped.upper()

            detected_division = self._detect_division(
                stripped,
            )

            if detected_division:
                current_division = detected_division
                in_exec_sql = False
                data_level_stack = []
                procedure_indent = 0
                evaluate_depth = 0

                output_lines.append(
                    self._rebuild_line(
                        left_sequence=left_sequence,
                        body=stripped,
                        right_sequence=right_sequence,
                    )
                )
                continue

            if self.EXEC_SQL_PATTERN.match(stripped):
                in_exec_sql = True
                output_lines.append(
                    self._rebuild_line(
                        left_sequence=left_sequence,
                        body=self._format_exec_sql_line(stripped),
                        right_sequence=right_sequence,
                    )
                )
                continue

            if in_exec_sql:
                formatted_sql_line = self._format_exec_sql_line(
                    stripped,
                )

                output_lines.append(
                    self._rebuild_line(
                        left_sequence=left_sequence,
                        body=formatted_sql_line,
                        right_sequence=right_sequence,
                    )
                )

                if self.END_EXEC_PATTERN.match(stripped):
                    in_exec_sql = False

                continue

            if self._is_comment_or_page_eject(stripped):
                output_lines.append(
                    self._rebuild_line(
                        left_sequence=left_sequence,
                        body=self._format_comment_or_page_eject(stripped),
                        right_sequence=right_sequence,
                    )
                )
                continue

            if self.SECTION_PATTERN.match(stripped):
                data_level_stack = []

                if upper == "PROCEDURE DIVISION.":
                    current_division = "PROCEDURE"

                output_lines.append(
                    self._rebuild_line(
                        left_sequence=left_sequence,
                        body=stripped,
                        right_sequence=right_sequence,
                    )
                )
                continue

            if current_division == "DATA":
                formatted_body = self._format_data_division_line(
                    stripped=stripped,
                    data_level_stack=data_level_stack,
                )

                output_lines.append(
                    self._rebuild_line(
                        left_sequence=left_sequence,
                        body=formatted_body,
                        right_sequence=right_sequence,
                    )
                )
                continue

            if current_division == "ENVIRONMENT":
                formatted_body = self._format_environment_line(
                    stripped,
                )

                output_lines.append(
                    self._rebuild_line(
                        left_sequence=left_sequence,
                        body=formatted_body,
                        right_sequence=right_sequence,
                    )
                )
                continue

            if current_division == "PROCEDURE":
                formatted_body, procedure_indent, evaluate_depth = (
                    self._format_procedure_line(
                        stripped=stripped,
                        procedure_indent=procedure_indent,
                        evaluate_depth=evaluate_depth,
                    )
                )

                output_lines.append(
                    self._rebuild_line(
                        left_sequence=left_sequence,
                        body=formatted_body,
                        right_sequence=right_sequence,
                    )
                )
                continue

            output_lines.append(
                self._rebuild_line(
                    left_sequence=left_sequence,
                    body=stripped,
                    right_sequence=right_sequence,
                )
            )

        formatted = "\n".join(output_lines)
        formatted = self._normalize_blank_lines(
            formatted,
        )

        return formatted.rstrip() + "\n"

    #
    # Split / rebuild
    #
    def _split_sequence(
        self,
        line: str,
    ) -> tuple[str, str, str]:
        text = str(line or "").rstrip()

        if self.SEQUENCE_ONLY_PATTERN.fullmatch(
            text,
        ):
            return "", "", ""

        left_match = self.LEFT_SEQUENCE_PATTERN.match(
            text,
        )

        if left_match:
            left_sequence = left_match.group("left") or ""
            body = left_match.group("body") or ""
            right_sequence = left_match.group("right") or ""

            return left_sequence, body.rstrip(), right_sequence

        right_match = self.RIGHT_SEQUENCE_PATTERN.match(
            text,
        )

        if right_match:
            body = right_match.group("body") or ""
            right_sequence = right_match.group("right") or ""

            if right_sequence and not body.strip().isdigit():
                return "", body.rstrip(), right_sequence

        return "", text, ""

    def _rebuild_line(
        self,
        left_sequence: str,
        body: str,
        right_sequence: str,
    ) -> str:
        clean_body = str(body or "").rstrip()

        if left_sequence and right_sequence:
            return f"{left_sequence} {clean_body} {right_sequence}".rstrip()

        if left_sequence:
            return f"{left_sequence} {clean_body}".rstrip()

        if right_sequence:
            return f"{clean_body} {right_sequence}".rstrip()

        return clean_body

    #
    # Division detection
    #
    def _detect_division(
        self,
        body: str,
    ) -> str:
        match = self.DIVISION_PATTERN.match(
            body,
        )

        if not match:
            return ""

        return match.group(1).upper()

    #
    # Comments
    #
    def _is_comment_or_page_eject(
        self,
        stripped: str,
    ) -> bool:
        return bool(
            self.COMMENT_PATTERN.match(stripped)
            or self.PAGE_EJECT_PATTERN.match(stripped)
        )

    def _format_comment_or_page_eject(
        self,
        stripped: str,
    ) -> str:
        if self.PAGE_EJECT_PATTERN.match(
            stripped,
        ):
            return "/"

        return stripped

    #
    # EXEC SQL formatting
    #
    def _format_exec_sql_line(
        self,
        stripped: str,
    ) -> str:
        upper = stripped.upper()

        if upper.startswith(
            self.SQL_KEYWORDS_ZERO_INDENT,
        ):
            return stripped

        if upper.startswith(
            self.SQL_KEYWORDS_LEVEL_2,
        ):
            return "        " + stripped

        if upper.startswith(
            self.SQL_KEYWORDS_LEVEL_1,
        ):
            return "    " + stripped

        if stripped.startswith(":"):
            return "        " + stripped

        if stripped.startswith(","):
            return "        " + stripped

        if stripped.endswith(","):
            return "        " + stripped

        return "        " + stripped

    #
    # ENVIRONMENT formatting
    #
    def _format_environment_line(
        self,
        stripped: str,
    ) -> str:
        upper = stripped.upper()

        if self.SELECT_FILE_PATTERN.match(
            stripped,
        ):
            return "    " + stripped

        if upper.startswith("ASSIGN "):
            return "        " + stripped

        return stripped

    #
    # DATA formatting
    #
    def _format_data_division_line(
        self,
        stripped: str,
        data_level_stack: list[int],
    ) -> str:
        upper = stripped.upper()

        if self.FILE_DESCRIPTOR_PATTERN.match(
            stripped,
        ):
            return stripped

        if upper.startswith(
            (
                "BLOCK ",
                "RECORD ",
                "RECORDING ",
                "LABEL ",
                "DATA ",
                "VALUE ",
            )
        ):
            return "    " + stripped

        level_match = self.DATA_LEVEL_PATTERN.match(
            stripped,
        )

        if not level_match:
            return stripped

        level_text = level_match.group("level")
        rest = level_match.group("rest").strip()

        try:
            level = int(level_text)
        except ValueError:
            level = 1

        if level in {1, 77}:
            data_level_stack.clear()
            data_level_stack.append(
                level,
            )
            indent = 0

        elif level == 88:
            indent = max(
                4,
                len(data_level_stack) * 4,
            )

        else:
            while data_level_stack and data_level_stack[-1] >= level:
                data_level_stack.pop()

            indent = len(data_level_stack) * 4
            data_level_stack.append(
                level,
            )

        return " " * indent + f"{level_text}  {rest}"

    #
    # PROCEDURE formatting
    #
    def _format_procedure_line(
        self,
        stripped: str,
        procedure_indent: int,
        evaluate_depth: int,
    ) -> tuple[str, int, int]:
        upper = stripped.upper()

        #
        # Critical ordering:
        # END-IF. and END-EVALUATE. must be processed before paragraph
        # detection. Otherwise they look like single-word paragraph names
        # and reset indentation incorrectly.
        #
        if upper.startswith("END-EVALUATE"):
            procedure_indent = max(
                0,
                procedure_indent - 4,
            )
            evaluate_depth = max(
                0,
                evaluate_depth - 1,
            )

            return (
                " " * procedure_indent + stripped,
                procedure_indent,
                evaluate_depth,
            )

        if upper.startswith("END-IF"):
            procedure_indent = max(
                0,
                procedure_indent - 4,
            )

            return (
                " " * procedure_indent + stripped,
                procedure_indent,
                evaluate_depth,
            )

        if upper.startswith("ELSE"):
            procedure_indent = max(
                0,
                procedure_indent - 4,
            )
            formatted = " " * procedure_indent + stripped
            procedure_indent += 4

            return formatted, procedure_indent, evaluate_depth

        if upper.startswith("WHEN "):
            when_indent = max(
                0,
                procedure_indent - 4,
            )
            formatted = " " * when_indent + stripped

            return formatted, procedure_indent, evaluate_depth

        if self._is_procedure_paragraph(
            stripped,
        ):
            return stripped, 0, 0

        formatted = " " * procedure_indent + stripped

        if upper.startswith("IF "):
            procedure_indent += 4

        elif upper.startswith("EVALUATE "):
            procedure_indent += 4
            evaluate_depth += 1

        return formatted, procedure_indent, evaluate_depth

    def _is_procedure_paragraph(
        self,
        stripped: str,
    ) -> bool:
        match = self.PARAGRAPH_PATTERN.match(
            stripped,
        )

        if not match:
            return False

        name = match.group("name").upper()

        if name in self.NON_PARAGRAPH_SINGLE_WORDS:
            return False

        if name.startswith("END-"):
            return False

        return True

    #
    # General cleanup
    #
    def _normalize_line_endings(
        self,
        text: str,
    ) -> str:
        return str(text or "").replace("\r\n", "\n").replace("\r", "\n")

    def _normalize_blank_lines(
        self,
        text: str,
    ) -> str:
        text = re.sub(
            r"\n{4,}",
            "\n\n\n",
            text,
        )

        text = re.sub(
            r"\n[ \t]+\n",
            "\n\n",
            text,
        )

        return text