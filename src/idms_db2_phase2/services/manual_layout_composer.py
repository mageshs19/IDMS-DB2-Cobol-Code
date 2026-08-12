import re


class ManualLayoutComposer:
    """
    Reorders generated COBOL into manual-style production layout.

    Required output order:
    1. Original COBOL header starts first.
    2. Original ENVIRONMENT / DATA / FILE / WORKING-STORAGE structure remains.
    3. Generated DB2 infrastructure is placed as one complete block in
       DATA DIVISION area before LINKAGE SECTION or PROCEDURE DIVISION.
    4. PROCEDURE DIVISION and original business logic remain after data sections.
    5. Generated cursor OPEN / FETCH / CLOSE paragraphs are placed near the end.
    6. SQL error paragraph is placed near the end.
    7. END PROGRAM remains last.

    Important fix:
    - The DB2 infrastructure block must remain one single complete block.
    - It must not be split into SQL-LOCATION / cursor flags / declarations /
      SQLCA include sections.
    """

    DB2_INFRA_MARKER = (
        "DB2 SQLCA, SQL ERROR WORKING STORAGE, DCLGEN INCLUDES, AND CURSOR FLAGS"
    )

    DB2_SQL_ERROR_LOCATION_MARKER = "DB2 SQL ERROR LOCATION"
    DB2_CURSOR_FLAGS_MARKER = "DB2 CURSOR END-OF-CURSOR FLAGS"
    DB2_CURSOR_DECLARATIONS_MARKER = "DB2 CURSOR DECLARATIONS"

    CURSOR_PARAGRAPH_MARKER = "DB2 GENERATED CURSOR OPEN FETCH CLOSE PARAGRAPHS"

    LEFT_SEQUENCE_PATTERN = re.compile(
        r"^\s*\d{6}\s+(?P<body>.*)$",
        flags=re.IGNORECASE,
    )

    RIGHT_SEQUENCE_PATTERN = re.compile(
        r"(?P<body>.*?)(?:\s+\d{8})\s*$",
        flags=re.IGNORECASE,
    )

    CBL_PATTERN = re.compile(
        r"^\s*CBL\b",
        flags=re.IGNORECASE,
    )

    IDENTIFICATION_DIVISION_PATTERN = re.compile(
        r"^\s*IDENTIFICATION\s+DIVISION\.\s*$",
        flags=re.IGNORECASE,
    )

    ENVIRONMENT_DIVISION_PATTERN = re.compile(
        r"^\s*ENVIRONMENT\s+DIVISION\.\s*$",
        flags=re.IGNORECASE,
    )

    DATA_DIVISION_PATTERN = re.compile(
        r"^\s*DATA\s+DIVISION\.\s*$",
        flags=re.IGNORECASE,
    )

    FILE_SECTION_PATTERN = re.compile(
        r"^\s*FILE\s+SECTION\.\s*$",
        flags=re.IGNORECASE,
    )

    WORKING_STORAGE_PATTERN = re.compile(
        r"^\s*WORKING-STORAGE\s+SECTION\.\s*$",
        flags=re.IGNORECASE,
    )

    LINKAGE_SECTION_PATTERN = re.compile(
        r"^\s*LINKAGE\s+SECTION\.\s*$",
        flags=re.IGNORECASE,
    )

    PROCEDURE_DIVISION_PATTERN = re.compile(
        r"^\s*PROCEDURE\s+DIVISION\b.*$",
        flags=re.IGNORECASE,
    )

    END_PROGRAM_PATTERN = re.compile(
        r"^\s*END\s+PROGRAM\b.*\.\s*$",
        flags=re.IGNORECASE,
    )

    SQL_ERROR_PARAGRAPH_PATTERN = re.compile(
        r"^\s*(SQL-ERROR|SQLERROR)\.\s*$",
        flags=re.IGNORECASE,
    )

    COBOL_PARAGRAPH_PATTERN = re.compile(
        r"^\s*[A-Z0-9][A-Z0-9-]*\.\s*$",
        flags=re.IGNORECASE,
    )

    COBOL_DIVISION_OR_SECTION_PATTERN = re.compile(
        r"^\s*[A-Z0-9 -]+\s+(DIVISION|SECTION)\.\s*$",
        flags=re.IGNORECASE,
    )

    DATA_LEVEL_PATTERN = re.compile(
        r"^\s*(0[1-9]|[1-4][0-9]|66|77|88)\s+",
        flags=re.IGNORECASE,
    )

    EXEC_SQL_PATTERN = re.compile(
        r"^\s*EXEC\s+SQL\b",
        flags=re.IGNORECASE,
    )

    END_EXEC_PATTERN = re.compile(
        r"^\s*END-EXEC\.\s*$",
        flags=re.IGNORECASE,
    )

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

        infrastructure_block, lines = self._extract_complete_db2_infrastructure_block(
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

        lines = self._trim_blank_edges(
            lines,
        )

        if infrastructure_block:
            lines = self._insert_infrastructure_in_data_area(
                lines=lines,
                infrastructure_block=infrastructure_block,
            )

        if cursor_block:
            lines = self._append_block_before_sql_error_or_end(
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

    def _logical_line(
        self,
        line: str,
    ) -> str:
        text = str(line or "").rstrip()

        right_match = self.RIGHT_SEQUENCE_PATTERN.match(
            text,
        )

        if right_match:
            text = right_match.group("body").rstrip()

        left_match = self.LEFT_SEQUENCE_PATTERN.match(
            text,
        )

        if left_match:
            text = left_match.group("body").strip()

        return text.strip()

    def _extract_complete_db2_infrastructure_block(
        self,
        lines: list[str],
    ) -> tuple[list[str], list[str]]:
        """
        Extracts the complete DB2 infrastructure block as one unit.

        This includes:
        - DB2 SQLCA / SQLERRWS / DCLGEN include section
        - SQL-LOCATION
        - cursor EOC flags
        - cursor declarations

        The extraction starts at the main SQLCA marker if present.
        If a partial block exists before/after it, the extraction expands to
        include all adjacent DB2 infrastructure sections.
        """

        marker_indexes = self._db2_infrastructure_marker_indexes(
            lines,
        )

        if not marker_indexes:
            return [], lines

        start_index = min(
            self._find_block_comment_start(
                lines=lines,
                marker_index=index,
            )
            for index in marker_indexes
        )

        end_index = self._find_complete_db2_infrastructure_end(
            lines=lines,
            start_index=start_index,
        )

        block = lines[start_index:end_index]
        remaining = lines[:start_index] + lines[end_index:]

        return self._trim_blank_edges(block), remaining

    def _db2_infrastructure_marker_indexes(
        self,
        lines: list[str],
    ) -> list[int]:
        markers = {
            self.DB2_INFRA_MARKER,
            self.DB2_SQL_ERROR_LOCATION_MARKER,
            self.DB2_CURSOR_FLAGS_MARKER,
            self.DB2_CURSOR_DECLARATIONS_MARKER,
        }

        indexes: list[int] = []

        for index, line in enumerate(lines):
            logical = self._logical_line(
                line,
            )
            upper = logical.upper()

            for marker in markers:
                if marker in upper:
                    indexes.append(
                        index,
                    )
                    break

        return indexes

    def _find_complete_db2_infrastructure_end(
        self,
        lines: list[str],
        start_index: int,
    ) -> int:
        inside_exec_sql = False
        index = start_index

        while index < len(lines):
            logical = self._logical_line(
                lines[index],
            )

            upper = logical.upper()

            if not logical:
                index += 1
                continue

            if inside_exec_sql:
                if self.END_EXEC_PATTERN.match(
                    logical,
                ):
                    inside_exec_sql = False

                index += 1
                continue

            if self.EXEC_SQL_PATTERN.match(
                logical,
            ):
                inside_exec_sql = True
                index += 1
                continue

            if self._is_db2_infrastructure_line(
                logical,
            ):
                index += 1
                continue

            if self._is_db2_infrastructure_decorative_comment(
                logical,
            ):
                index += 1
                continue

            if self.LINKAGE_SECTION_PATTERN.match(
                logical,
            ):
                return self._trim_end_index_before_blank_run(
                    lines=lines,
                    end_index=index,
                )

            if self.PROCEDURE_DIVISION_PATTERN.match(
                logical,
            ):
                return self._trim_end_index_before_blank_run(
                    lines=lines,
                    end_index=index,
                )

            if self._is_original_program_start_line(
                logical,
            ):
                return self._trim_end_index_before_blank_run(
                    lines=lines,
                    end_index=index,
                )

            if self.FILE_SECTION_PATTERN.match(
                logical,
            ):
                return self._trim_end_index_before_blank_run(
                    lines=lines,
                    end_index=index,
                )

            if self.COBOL_DIVISION_OR_SECTION_PATTERN.match(
                logical,
            ):
                return self._trim_end_index_before_blank_run(
                    lines=lines,
                    end_index=index,
                )

            if self.DATA_LEVEL_PATTERN.match(
                logical,
            ):
                # Allow generated DB2 data-levels only.
                if self._is_db2_data_level_line(
                    logical,
                ):
                    index += 1
                    continue

                return self._trim_end_index_before_blank_run(
                    lines=lines,
                    end_index=index,
                )

            if upper.startswith("COPY "):
                return self._trim_end_index_before_blank_run(
                    lines=lines,
                    end_index=index,
                )

            index += 1

        return len(lines)

    def _is_db2_infrastructure_line(
        self,
        logical: str,
    ) -> bool:
        upper = str(logical or "").strip().upper()

        if not upper:
            return True

        if upper.startswith("INCLUDE "):
            return True

        if upper.startswith("DECLARE ") and " CURSOR FOR" in upper:
            return True

        if upper.startswith("SELECT"):
            return True

        if upper.startswith("FROM "):
            return True

        if upper.startswith("WHERE"):
            return True

        if upper.startswith("AND "):
            return True

        if upper.startswith("OR "):
            return True

        if upper.startswith("ORDER BY"):
            return True

        if upper.startswith("GROUP BY"):
            return True

        if upper.startswith("HAVING"):
            return True

        if upper.startswith("FOR READ ONLY"):
            return True

        if upper.startswith(":"):
            return True

        if "," in upper and not self.COBOL_PARAGRAPH_PATTERN.match(upper):
            return True

        if upper.startswith("01 SQL-LOCATION"):
            return True

        if upper.startswith("PIC X(40)"):
            return True

        if upper.startswith("01 WS-") and "-FLAG" in upper:
            return True

        if upper.startswith("88 ") and (
            "-EOC" in upper or "-NOT-EOC" in upper
        ):
            return True

        if "DB2 SQL ERROR LOCATION" in upper:
            return True

        if "DB2 CURSOR END-OF-CURSOR FLAGS" in upper:
            return True

        if "DB2 CURSOR DECLARATIONS" in upper:
            return True

        if self.DB2_INFRA_MARKER in upper:
            return True

        return False

    def _is_db2_data_level_line(
        self,
        logical: str,
    ) -> bool:
        upper = str(logical or "").strip().upper()

        if upper.startswith("01 SQL-LOCATION"):
            return True

        if upper.startswith("01 WS-") and "-FLAG" in upper:
            return True

        if upper.startswith("88 ") and (
            "-EOC" in upper or "-NOT-EOC" in upper
        ):
            return True

        return False

    def _is_db2_infrastructure_decorative_comment(
        self,
        logical: str,
    ) -> bool:
        stripped = str(logical or "").strip()

        if not stripped.startswith("*"):
            return False

        without_symbols = (
            stripped.replace("*", "")
            .replace("-", "")
            .replace("=" , "")
            .strip()
        )

        if not without_symbols:
            return True

        upper = stripped.upper()

        return (
            "DB2" in upper
            or "SQLCA" in upper
            or "CURSOR" in upper
            or "SQL ERROR" in upper
        )

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
        seen_marker = False
        inside_exec_sql = False
        index = start_index

        while index < len(lines):
            logical = self._logical_line(
                lines[index],
            )

            upper = logical.upper()

            if not logical:
                index += 1
                continue

            if self.CURSOR_PARAGRAPH_MARKER in upper:
                seen_marker = True
                index += 1
                continue

            if not seen_marker:
                index += 1
                continue

            if inside_exec_sql:
                if self.END_EXEC_PATTERN.match(
                    logical,
                ):
                    inside_exec_sql = False

                index += 1
                continue

            if self.EXEC_SQL_PATTERN.match(
                logical,
            ):
                inside_exec_sql = True
                index += 1
                continue

            if self.SQL_ERROR_PARAGRAPH_PATTERN.match(
                logical,
            ):
                return self._trim_end_index_before_blank_run(
                    lines=lines,
                    end_index=index,
                )

            if self.END_PROGRAM_PATTERN.match(
                logical,
            ):
                return self._trim_end_index_before_blank_run(
                    lines=lines,
                    end_index=index,
                )

            if self.COBOL_DIVISION_OR_SECTION_PATTERN.match(
                logical,
            ):
                return self._trim_end_index_before_blank_run(
                    lines=lines,
                    end_index=index,
                )

            index += 1

        return len(lines)

    def _extract_sql_error_block(
        self,
        lines: list[str],
    ) -> tuple[list[str], list[str]]:
        start_index = self._find_sql_error_paragraph_index(
            lines,
        )

        if start_index < 0:
            return [], lines

        end_index = self._find_sql_error_paragraph_end(
            lines=lines,
            start_index=start_index,
        )

        block = lines[start_index:end_index]
        remaining = lines[:start_index] + lines[end_index:]

        return self._trim_blank_edges(block), remaining

    def _find_sql_error_paragraph_index(
        self,
        lines: list[str],
    ) -> int:
        for index, line in enumerate(lines):
            logical = self._logical_line(
                line,
            )

            if self.SQL_ERROR_PARAGRAPH_PATTERN.match(
                logical,
            ):
                return index

        return -1

    def _find_sql_error_paragraph_end(
        self,
        lines: list[str],
        start_index: int,
    ) -> int:
        index = start_index + 1
        inside_exec_sql = False

        while index < len(lines):
            logical = self._logical_line(
                lines[index],
            )

            if not logical:
                index += 1
                continue

            if inside_exec_sql:
                if self.END_EXEC_PATTERN.match(
                    logical,
                ):
                    inside_exec_sql = False

                index += 1
                continue

            if self.EXEC_SQL_PATTERN.match(
                logical,
            ):
                inside_exec_sql = True
                index += 1
                continue

            if self.END_PROGRAM_PATTERN.match(
                logical,
            ):
                return self._trim_end_index_before_blank_run(
                    lines=lines,
                    end_index=index,
                )

            if self.COBOL_DIVISION_OR_SECTION_PATTERN.match(
                logical,
            ):
                return self._trim_end_index_before_blank_run(
                    lines=lines,
                    end_index=index,
                )

            if self.COBOL_PARAGRAPH_PATTERN.match(
                logical,
            ):
                return self._trim_end_index_before_blank_run(
                    lines=lines,
                    end_index=index,
                )

            index += 1

        return len(lines)

    def _extract_end_program_line(
        self,
        lines: list[str],
    ) -> tuple[str, list[str]]:
        end_index = -1

        for index, line in enumerate(lines):
            logical = self._logical_line(
                line,
            )

            if self.END_PROGRAM_PATTERN.match(
                logical,
            ):
                end_index = index

        if end_index < 0:
            return "", lines

        end_program_line = lines[end_index]
        remaining = lines[:end_index] + lines[end_index + 1:]

        return end_program_line, remaining

    def _insert_infrastructure_in_data_area(
        self,
        lines: list[str],
        infrastructure_block: list[str],
    ) -> list[str]:
        block = self._surround_block_with_single_blank_lines(
            infrastructure_block,
        )

        if not block:
            return lines

        linkage_index = self._find_pattern_index(
            lines=lines,
            pattern=self.LINKAGE_SECTION_PATTERN,
        )

        if linkage_index >= 0:
            return (
                lines[:linkage_index]
                + block
                + self._lstrip_blank_lines(lines[linkage_index:])
            )

        procedure_index = self._find_pattern_index(
            lines=lines,
            pattern=self.PROCEDURE_DIVISION_PATTERN,
        )

        if procedure_index >= 0:
            return (
                lines[:procedure_index]
                + block
                + self._lstrip_blank_lines(lines[procedure_index:])
            )

        working_storage_index = self._find_pattern_index(
            lines=lines,
            pattern=self.WORKING_STORAGE_PATTERN,
        )

        if working_storage_index >= 0:
            insert_index = working_storage_index + 1

            return (
                lines[:insert_index]
                + block
                + self._lstrip_blank_lines(lines[insert_index:])
            )

        data_division_index = self._find_pattern_index(
            lines=lines,
            pattern=self.DATA_DIVISION_PATTERN,
        )

        if data_division_index >= 0:
            insert_index = data_division_index + 1

            return (
                lines[:insert_index]
                + block
                + self._lstrip_blank_lines(lines[insert_index:])
            )

        return self._rstrip_blank_lines(lines) + block

    def _append_block_before_sql_error_or_end(
        self,
        lines: list[str],
        block: list[str],
    ) -> list[str]:
        prepared_block = self._surround_block_with_single_blank_lines(
            block,
        )

        if not prepared_block:
            return lines

        sql_error_index = self._find_sql_error_paragraph_index(
            lines,
        )

        if sql_error_index >= 0:
            return (
                self._rstrip_blank_lines(lines[:sql_error_index])
                + prepared_block
                + self._lstrip_blank_lines(lines[sql_error_index:])
            )

        end_program_index = self._find_end_program_index(
            lines,
        )

        if end_program_index >= 0:
            return (
                self._rstrip_blank_lines(lines[:end_program_index])
                + prepared_block
                + self._lstrip_blank_lines(lines[end_program_index:])
            )

        return self._rstrip_blank_lines(lines) + prepared_block

    def _append_block_before_end(
        self,
        lines: list[str],
        block: list[str],
    ) -> list[str]:
        prepared_block = self._surround_block_with_single_blank_lines(
            block,
        )

        if not prepared_block:
            return lines

        end_program_index = self._find_end_program_index(
            lines,
        )

        if end_program_index >= 0:
            return (
                self._rstrip_blank_lines(lines[:end_program_index])
                + prepared_block
                + self._lstrip_blank_lines(lines[end_program_index:])
            )

        return self._rstrip_blank_lines(lines) + prepared_block

    def _append_end_program(
        self,
        lines: list[str],
        end_program_line: str,
    ) -> list[str]:
        if not end_program_line:
            return lines

        trimmed = self._rstrip_blank_lines(
            lines,
        )

        if not trimmed:
            return [end_program_line]

        return trimmed + ["", end_program_line]

    def _find_line_containing(
        self,
        lines: list[str],
        text: str,
    ) -> int:
        needle = str(text or "").upper()

        for index, line in enumerate(lines):
            logical = self._logical_line(
                line,
            )

            if needle in logical.upper():
                return index

        return -1

    def _find_pattern_index(
        self,
        lines: list[str],
        pattern: re.Pattern,
    ) -> int:
        for index, line in enumerate(lines):
            logical = self._logical_line(
                line,
            )

            if pattern.match(
                logical,
            ):
                return index

        return -1

    def _find_end_program_index(
        self,
        lines: list[str],
    ) -> int:
        for index, line in enumerate(lines):
            logical = self._logical_line(
                line,
            )

            if self.END_PROGRAM_PATTERN.match(
                logical,
            ):
                return index

        return -1

    def _find_block_comment_start(
        self,
        lines: list[str],
        marker_index: int,
    ) -> int:
        index = marker_index

        while index > 0:
            previous = self._logical_line(
                lines[index - 1],
            )

            if not previous:
                index -= 1
                continue

            if self._is_decorative_comment_line(
                previous,
            ):
                index -= 1
                continue

            break

        return index

    def _is_decorative_comment_line(
        self,
        logical_line: str,
    ) -> bool:
        stripped = logical_line.strip()

        if not stripped.startswith("*"):
            return False

        without_symbols = (
            stripped.replace("*", "")
            .replace("-", "")
            .replace("=" , "")
            .strip()
        )

        if not without_symbols:
            return True

        return False

    def _is_original_program_start_line(
        self,
        logical_line: str,
    ) -> bool:
        return bool(
            self.CBL_PATTERN.match(logical_line)
            or self.IDENTIFICATION_DIVISION_PATTERN.match(logical_line)
            or self.ENVIRONMENT_DIVISION_PATTERN.match(logical_line)
            or self.DATA_DIVISION_PATTERN.match(logical_line)
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

    def _surround_block_with_single_blank_lines(
        self,
        block: list[str],
    ) -> list[str]:
        trimmed = self._trim_blank_edges(
            block,
        )

        if not trimmed:
            return []

        return ["", *trimmed, ""]

    def _trim_end_index_before_blank_run(
        self,
        lines: list[str],
        end_index: int,
    ) -> int:
        while end_index > 0 and not lines[end_index - 1].strip():
            end_index -= 1

        return end_index

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