import re


class CobolFormatter:
    """
    Final COBOL formatting pass.

    This class intentionally does not change business logic.
    It only normalizes:
    - indentation
    - generated statement periods
    - 88-level indentation
    - EVALUATE/WHEN/END-EVALUATE indentation
    - END PROGRAM indentation
    - multiline MOVE formatting

    It preserves:
    - comments
    - author details
    - remarks
    - business-rule comments
    - paragraph names
    - section names
    """

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

        lines = text.splitlines()
        output: list[str] = []
        in_exec_sql = False
        in_evaluate = False
        pending_multiline_move = False

        for raw_line in lines:
            stripped = raw_line.strip()

            if not stripped:
                output.append("")
                continue

            upper = stripped.upper()

            if upper.startswith("EXEC SQL"):
                in_exec_sql = True
                output.append("           EXEC SQL")
                continue

            if upper.startswith("END-EXEC"):
                in_exec_sql = False
                output.append("           END-EXEC.")
                continue

            if in_exec_sql:
                output.append(
                    self._format_sql_line(
                        stripped,
                    )
                )
                continue

            if self._is_division_or_section_header(
                upper,
            ):
                output.append(
                    f"       {stripped}"
                )
                pending_multiline_move = False
                continue

            if self._is_paragraph_header(
                stripped,
            ):
                output.append(
                    f"       {stripped}"
                )
                pending_multiline_move = False
                continue

            if upper.startswith("*"):
                output.append(
                    f"      {stripped}"
                )
                continue

            if re.match(
                r"^88\s+",
                upper,
            ):
                output.append(
                    f"          {stripped}"
                )
                pending_multiline_move = False
                continue

            if upper.startswith("EVALUATE "):
                in_evaluate = True
                output.append(
                    f"           {stripped.rstrip('.')}"
                )
                pending_multiline_move = False
                continue

            if upper.startswith("WHEN "):
                output.append(
                    f"               {stripped}"
                )
                pending_multiline_move = False
                continue

            if upper.startswith("END-EVALUATE"):
                in_evaluate = False
                output.append(
                    "           END-EVALUATE."
                )
                pending_multiline_move = False
                continue

            if in_evaluate:
                output.append(
                    self._format_evaluate_body_line(
                        stripped,
                    )
                )
                pending_multiline_move = False
                continue

            if pending_multiline_move:
                if upper.startswith("TO "):
                    output.append(
                        f"                {self._ensure_period(stripped)}"
                    )
                    pending_multiline_move = False
                    continue

                output.append(
                    f"                {stripped}"
                )
                continue

            if upper.startswith("MOVE "):
                if " TO " in upper:
                    output.append(
                        f"           {self._ensure_period(stripped)}"
                    )
                    pending_multiline_move = False
                    continue

                output.append(
                    f"           {stripped}"
                )
                pending_multiline_move = True
                continue

            if upper.startswith("END PROGRAM "):
                output.append(
                    f"       {self._ensure_period(stripped)}"
                )
                pending_multiline_move = False
                continue

            if self._needs_period(
                upper,
            ):
                output.append(
                    f"           {self._ensure_period(stripped)}"
                )
                pending_multiline_move = False
                continue

            output.append(
                f"           {stripped}"
            )
            pending_multiline_move = False

        formatted = "\n".join(output)
        formatted = self._normalize_period_spacing(formatted)
        formatted = re.sub(r"\n{3,}", "\n\n", formatted)

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
        Repairs cases created by a previous formatter pass:

            MOVE DCLX.FIELD.
            TO OUT-FIELD.

        into:

            MOVE DCLX.FIELD
            TO OUT-FIELD.

        It only removes a period from a MOVE source line when the next
        non-empty line starts with TO.
        """
        lines = text.splitlines()
        output: list[str] = []
        index = 0

        while index < len(lines):
            line = lines[index]
            stripped = line.strip()
            upper = stripped.upper()

            if upper.startswith("MOVE ") and stripped.endswith("."):
                next_non_empty_index = self._next_non_empty_index(
                    lines=lines,
                    start=index + 1,
                )

                if next_non_empty_index is not None:
                    next_line = lines[next_non_empty_index].strip()

                    if next_line.upper().startswith("TO "):
                        line = line.rstrip()
                        line = line[:-1]

            output.append(line)
            index += 1

        return "\n".join(output)

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
            return f"                   {stripped}"

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

        return f"                    {stripped}"

    def _format_evaluate_body_line(
        self,
        stripped: str,
    ) -> str:
        upper = stripped.upper()

        if self._needs_period(
            upper,
        ):
            return f"                   {self._ensure_period(stripped)}"

        return f"                   {stripped}"

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
            )
        ):
            return False

        return bool(
            re.fullmatch(
                r"[A-Z0-9-]+\.",
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

    def _normalize_period_spacing(
        self,
        text: str,
    ) -> str:
        return re.sub(
            r"\s+\.",
            ".",
            text,
        )