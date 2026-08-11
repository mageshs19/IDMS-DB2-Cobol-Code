import re


class ManualLayoutComposer:
    """
    Reorders generated COBOL into a manual-style production layout.

    This class is intentionally generic.

    It does not:
    - hardcode program names
    - hardcode DB2 table names
    - hardcode cursor names
    - hardcode business paragraph names
    - create business rules

    It only repositions generated blocks into a cleaner manual-style order:

    1. Original identification/environment/data/file/working-storage structure
    2. Existing working-storage items
    3. Generated DB2 infrastructure block
    4. Procedure Division and original business paragraphs
    5. Generated cursor OPEN/FETCH/CLOSE paragraphs
    6. SQL error paragraph
    7. END PROGRAM
    """

    DB2_INFRA_MARKER = "DB2 SQLCA, SQL ERROR WORKING STORAGE, DCLGEN INCLUDES, AND CURSOR FLAGS"
    CURSOR_PARAGRAPH_MARKER = "DB2 GENERATED CURSOR OPEN FETCH CLOSE PARAGRAPHS"

    def compose(
        self,
        cobol_text: str,
    ) -> str:
        if not cobol_text:
            return ""

        text = self._normalize_line_endings(
            cobol_text,
        )

        lines = text.splitlines()

        infrastructure_block, lines = self._extract_db2_infrastructure_block(
            lines,
        )

        cursor_block, lines = self._extract_generated_cursor_paragraph_block(
            lines,
        )

        sql_error_block, lines = self._extract_sql_error_block(
            lines,
        )

        end_program_line, lines = self._extract_end_program_line(
            lines,
        )

        if infrastructure_block:
            lines = self._insert_infrastructure_before_procedure(
                lines=lines,
                infrastructure_block=infrastructure_block,
            )

        if cursor_block:
            lines = self._append_block_before_end(
                lines=lines,
                block=cursor_block,
            )

        if sql_error_block:
            lines = self._append_block_before_end(
                lines=lines,
                block=sql_error_block,
            )

        if end_program_line:
            lines = self._append_end_program(
                lines=lines,
                end_program_line=end_program_line,
            )

        result = "\n".join(
            lines,
        )

        result = self._normalize_blank_lines(
            result,
        )

        return result.strip() + "\n"

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

    def _extract_db2_infrastructure_block(
        self,
        lines: list[str],
    ) -> tuple[list[str], list[str]]:
        marker_index = self._find_line_containing(
            lines=lines,
            text=self.DB2_INFRA_MARKER,
        )

        if marker_index < 0:
            return [], lines

        start_index = self._find_block_comment_start(
            lines=lines,
            marker_index=marker_index,
        )

        end_index = self._find_db2_infrastructure_end(
            lines=lines,
            start_index=start_index,
        )

        block = lines[start_index:end_index]
        remaining = lines[:start_index] + lines[end_index:]

        return self._trim_blank_edges(block), remaining

    def _find_db2_infrastructure_end(
        self,
        lines: list[str],
        start_index: int,
    ) -> int:
        """
        Finds the end of the generated DB2 infrastructure block.

        The block can contain:
        - SQLERRWS / SQLCA / DCLGEN includes
        - SQL-LOCATION
        - cursor flags
        - cursor declarations

        It should stop before normal user working-storage entries resume
        or before PROCEDURE DIVISION.
        """
        seen_marker = False
        seen_cursor_declarations = False
        seen_cursor_end_exec = False
        index = start_index

        while index < len(lines):
            line = lines[index]
            upper = line.strip().upper()

            if self.DB2_INFRA_MARKER in upper:
                seen_marker = True

            if seen_marker and "DB2 CURSOR DECLARATIONS" in upper:
                seen_cursor_declarations = True

            if seen_cursor_declarations and upper.startswith("END-EXEC"):
                seen_cursor_end_exec = True

            if seen_marker and upper.startswith("PROCEDURE DIVISION."):
                return index

            if seen_marker and seen_cursor_end_exec:
                next_non_empty = self._next_non_empty_line(
                    lines=lines,
                    start=index + 1,
                )

                if next_non_empty is None:
                    return index + 1

                next_upper = next_non_empty.strip().upper()

                if re.match(
                    r"^(01|77)\s+",
                    next_upper,
                ):
                    return index + 1

                if next_upper.startswith("PROCEDURE DIVISION."):
                    return index + 1

            if seen_marker and not seen_cursor_declarations:
                next_non_empty = self._next_non_empty_line(
                    lines=lines,
                    start=index + 1,
                )

                if next_non_empty is not None:
                    next_upper = next_non_empty.strip().upper()

                    if next_upper.startswith("PROCEDURE DIVISION."):
                        return index + 1

            index += 1

        return len(lines)

    def _extract_generated_cursor_paragraph_block(
        self,
        lines: list[str],
    ) -> tuple[list[str], list[str]]:
        marker_index = self._find_line_containing(
            lines=lines,
            text=self.CURSOR_PARAGRAPH_MARKER,
        )

        if marker_index < 0:
            return [], lines

        start_index = self._find_block_comment_start(
            lines=lines,
            marker_index=marker_index,
        )

        end_index = self._find_generated_cursor_paragraph_end(
            lines=lines,
            start_index=start_index,
        )

        block = lines[start_index:end_index]
        remaining = lines[:start_index] + lines[end_index:]

        return self._trim_blank_edges(block), remaining

    def _find_generated_cursor_paragraph_end(
        self,
        lines: list[str],
        start_index: int,
    ) -> int:
        index = start_index + 1

        while index < len(lines):
            stripped = lines[index].strip()
            upper = stripped.upper()

            if self._is_sql_error_paragraph_header(
                stripped,
            ):
                return index

            if upper.startswith("END PROGRAM"):
                return index

            index += 1

        return len(lines)

    def _extract_sql_error_block(
        self,
        lines: list[str],
    ) -> tuple[list[str], list[str]]:
        start_index = -1

        for index, line in enumerate(lines):
            stripped = line.strip()

            if self._is_sql_error_paragraph_header(
                stripped,
            ):
                start_index = index
                break

        if start_index < 0:
            return [], lines

        end_index = self._find_sql_error_end(
            lines=lines,
            start_index=start_index,
        )

        block = lines[start_index:end_index]
        remaining = lines[:start_index] + lines[end_index:]

        return self._trim_blank_edges(block), remaining

    def _find_sql_error_end(
        self,
        lines: list[str],
        start_index: int,
    ) -> int:
        index = start_index + 1

        while index < len(lines):
            stripped = lines[index].strip()
            upper = stripped.upper()

            if upper.startswith("END PROGRAM"):
                return index

            if self._is_paragraph_header(
                stripped,
            ) and not self._is_sql_error_paragraph_header(
                stripped,
            ):
                return index

            index += 1

        return len(lines)

    def _extract_end_program_line(
        self,
        lines: list[str],
    ) -> tuple[str, list[str]]:
        for index, line in enumerate(lines):
            if line.strip().upper().startswith("END PROGRAM"):
                return line.strip(), lines[:index] + lines[index + 1:]

        return "", lines

    def _insert_infrastructure_before_procedure(
        self,
        lines: list[str],
        infrastructure_block: list[str],
    ) -> list[str]:
        procedure_index = self._find_procedure_division_index(
            lines,
        )

        if procedure_index < 0:
            return self._append_block_before_end(
                lines=lines,
                block=infrastructure_block,
            )

        before = lines[:procedure_index]
        after = lines[procedure_index:]

        before = self._rstrip_blank_lines(
            before,
        )

        return before + [""] + infrastructure_block + [""] + after

    def _append_block_before_end(
        self,
        lines: list[str],
        block: list[str],
    ) -> list[str]:
        if not block:
            return lines

        lines = self._rstrip_blank_lines(
            lines,
        )

        return lines + [""] + block

    def _append_end_program(
        self,
        lines: list[str],
        end_program_line: str,
    ) -> list[str]:
        lines = self._rstrip_blank_lines(
            lines,
        )

        return lines + ["", self._format_end_program_line(end_program_line)]

    def _format_end_program_line(
        self,
        line: str,
    ) -> str:
        stripped = line.strip()

        if not stripped.endswith("."):
            stripped += "."

        return f"       {stripped}"

    def _find_line_containing(
        self,
        lines: list[str],
        text: str,
    ) -> int:
        target = text.upper()

        for index, line in enumerate(lines):
            if target in line.upper():
                return index

        return -1

    def _find_block_comment_start(
        self,
        lines: list[str],
        marker_index: int,
    ) -> int:
        index = marker_index

        while index > 0:
            previous = lines[index - 1].strip()

            if previous.startswith("*") or set(previous) == {"*"}:
                index -= 1
                continue

            if not previous:
                index -= 1
                continue

            break

        return index

    def _find_procedure_division_index(
        self,
        lines: list[str],
    ) -> int:
        for index, line in enumerate(lines):
            if line.strip().upper().startswith("PROCEDURE DIVISION."):
                return index

        return -1

    def _next_non_empty_line(
        self,
        lines: list[str],
        start: int,
    ) -> str | None:
        for index in range(start, len(lines)):
            if lines[index].strip():
                return lines[index]

        return None

    def _is_sql_error_paragraph_header(
        self,
        stripped: str,
    ) -> bool:
        upper = stripped.upper()

        return upper in {
            "SQL-ERROR.",
            "SQLERROR.",
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

        return bool(
            re.fullmatch(
                r"[A-Z0-9-]+\.",
                upper,
            )
        )

    def _trim_blank_edges(
        self,
        lines: list[str],
    ) -> list[str]:
        return self._rstrip_blank_lines(
            self._lstrip_blank_lines(
                lines,
            )
        )

    def _lstrip_blank_lines(
        self,
        lines: list[str],
    ) -> list[str]:
        index = 0

        while index < len(lines) and not lines[index].strip():
            index += 1

        return lines[index:]

    def _rstrip_blank_lines(
        self,
        lines: list[str],
    ) -> list[str]:
        index = len(lines)

        while index > 0 and not lines[index - 1].strip():
            index -= 1

        return lines[:index]

    def _normalize_blank_lines(
        self,
        text: str,
    ) -> str:
        return re.sub(
            r"\n{3,}",
            "\n\n",
            text,
        )