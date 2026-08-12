from __future__ import annotations

import re

from idms_db2_phase2.domain.models import DclgenColumn, SheetMappingRow
from idms_db2_phase2.services.name_normalizer import NameNormalizer


class TimestampGenerator:
    """
    Generates timestamp and audit support using Sheet Mapping as authority
    and DCLGEN as host-variable authority.

    Rules:
    - Sheet Mapping decides DB2 table/column names.
    - DCLGEN supplies COBOL host variable spelling and group names.
    - DCLGEN must not introduce audit fields absent from Sheet Mapping.
    - For UPDATE flows, generate TS_UPDATE and USER-ID moves only.
    - TS_CREATE is insert-only and is not generated for update-only flows.
    - Timestamp paragraph must be a safe paragraph with a terminating boundary.
    """

    WS_MARKER = "DB2 GENERATED TIMESTAMP AND AUDIT WORKING STORAGE"
    PARAGRAPH_NAME = "600-GET-TIMESTAMP"
    PARAGRAPH_EXIT_NAME = "600-GET-TIMESTAMP-EXIT"

    WORKING_STORAGE_PATTERN = re.compile(
        r"^\s*(?:\d{6}\s+)?WORKING-STORAGE\s+SECTION\.\s*(?:\d{8})?\s*$",
        flags=re.IGNORECASE,
    )

    LINKAGE_SECTION_PATTERN = re.compile(
        r"^\s*(?:\d{6}\s+)?LINKAGE\s+SECTION\.\s*(?:\d{8})?\s*$",
        flags=re.IGNORECASE,
    )

    PROCEDURE_DIVISION_PATTERN = re.compile(
        r"^\s*(?:\d{6}\s+)?PROCEDURE\s+DIVISION\b.*$",
        flags=re.IGNORECASE,
    )

    STOP_RUN_PATTERN = re.compile(
        r"^\s*(?:\d{6}\s+)?STOP\s+RUN\.\s*(?:\d{8})?\s*$",
        flags=re.IGNORECASE,
    )

    END_PROGRAM_PATTERN = re.compile(
        r"^\s*(?:\d{6}\s+)?END\s+PROGRAM\b.*\.\s*(?:\d{8})?\s*$",
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

    UPDATE_SQL_PATTERN = re.compile(
        r"\bUPDATE\s+(?P<table>[A-Z][A-Z0-9_]*)\b",
        flags=re.IGNORECASE,
    )

    INSERT_SQL_PATTERN = re.compile(
        r"\bINSERT\s+INTO\s+(?P<table>[A-Z][A-Z0-9_]*)\b",
        flags=re.IGNORECASE,
    )

    DELETE_SQL_PATTERN = re.compile(
        r"\bDELETE\s+FROM\s+(?P<table>[A-Z][A-Z0-9_]*)\b",
        flags=re.IGNORECASE,
    )

    FROM_SQL_PATTERN = re.compile(
        r"\bFROM\s+(?P<table>[A-Z][A-Z0-9_]*)\b",
        flags=re.IGNORECASE,
    )

    INCLUDE_PATTERN = re.compile(
        r"\bINCLUDE\s+(?P<table>[A-Z][A-Z0-9_]*)\b",
        flags=re.IGNORECASE,
    )

    DB2_WRITE_OPERATION_PATTERN = re.compile(
        r"^\s*(?:INSERT|UPDATE|DELETE)\b",
        flags=re.IGNORECASE,
    )

    COBOL_WRITE_OPERATION_PATTERN = re.compile(
        r"^\s*(?:STORE|MODIFY|ERASE)\b",
        flags=re.IGNORECASE,
    )

    def __init__(
        self,
        mapping_rows: list[SheetMappingRow],
        dclgen_columns: list[DclgenColumn],
    ) -> None:
        self.mapping_rows = mapping_rows or []
        self.dclgen_columns = dclgen_columns or []
        self.messages: list[str] = []

        self.dclgen_host_lookup = self._build_dclgen_host_lookup(self.dclgen_columns)
        self.dclgen_group_lookup = self._build_dclgen_group_lookup(self.dclgen_columns)
        self.dclgen_table_catalog = self._build_dclgen_table_catalog(self.dclgen_columns)

    def apply(
        self,
        cobol_text: str,
        target_program_id: str = "",
    ) -> tuple[str, list[str]]:
        self.messages = []

        if not cobol_text:
            return "", self.messages

        used_tables = self._used_db2_tables_from_text(cobol_text)
        write_tables = self._write_db2_tables_from_text(cobol_text)

        audit_specs = self._audit_specs_from_sheet_mapping(
            used_tables=used_tables,
            write_tables=write_tables,
        )

        if not audit_specs:
            self.messages.append(
                "Timestamp generator: no Sheet Mapping audit fields found for DB2 records used by this program."
            )
            return cobol_text, self.messages

        updated_text = cobol_text

        updated_text = self._ensure_timestamp_working_storage(
            text=updated_text,
            target_program_id=target_program_id,
        )

        updated_text = self._ensure_timestamp_paragraph(
            text=updated_text,
        )

        if self._has_db2_write_activity(updated_text):
            updated_text = self._ensure_timestamp_perform(
                text=updated_text,
            )

            updated_text = self._ensure_audit_moves(
                text=updated_text,
                audit_specs=audit_specs,
            )

            self.messages.append(
                f"Timestamp generator: generated audit moves for {len(audit_specs)} Sheet Mapping field(s) used by this write program."
            )
        else:
            self.messages.append(
                "Timestamp generator: timestamp fields detected, but no DB2 write activity found; audit MOVE statements were not generated."
            )

        return updated_text.rstrip() + "\n", self.messages

    def _used_db2_tables_from_text(
        self,
        text: str,
    ) -> set[str]:
        output: set[str] = set()
        in_exec_sql = False

        for line in (text or "").splitlines():
            logical = self._logical_line(line)

            if self.EXEC_SQL_PATTERN.match(logical):
                in_exec_sql = True

            include_match = self.INCLUDE_PATTERN.search(logical)

            if include_match:
                output.add(
                    self._resolve_dclgen_table(include_match.group("table"))
                )

            if in_exec_sql:
                for pattern in [
                    self.UPDATE_SQL_PATTERN,
                    self.INSERT_SQL_PATTERN,
                    self.DELETE_SQL_PATTERN,
                    self.FROM_SQL_PATTERN,
                ]:
                    match = pattern.search(logical)

                    if match:
                        output.add(
                            self._resolve_dclgen_table(match.group("table"))
                        )

            if self.END_EXEC_PATTERN.match(logical):
                in_exec_sql = False

        return {
            item
            for item in output
            if item
        }

    def _write_db2_tables_from_text(
        self,
        text: str,
    ) -> set[str]:
        output: set[str] = set()
        in_exec_sql = False

        for line in (text or "").splitlines():
            logical = self._logical_line(line)

            if self.EXEC_SQL_PATTERN.match(logical):
                in_exec_sql = True

            if in_exec_sql:
                for pattern in [
                    self.UPDATE_SQL_PATTERN,
                    self.INSERT_SQL_PATTERN,
                    self.DELETE_SQL_PATTERN,
                ]:
                    match = pattern.search(logical)

                    if match:
                        output.add(
                            self._resolve_dclgen_table(match.group("table"))
                        )

            if self.END_EXEC_PATTERN.match(logical):
                in_exec_sql = False

        return {
            item
            for item in output
            if item
        }

    def _audit_specs_from_sheet_mapping(
        self,
        used_tables: set[str],
        write_tables: set[str],
    ) -> list[dict[str, str]]:
        output: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()

        normalized_used = {
            self._resolve_dclgen_table(table)
            for table in used_tables
            if table
        }

        normalized_write = {
            self._resolve_dclgen_table(table)
            for table in write_tables
            if table
        }

        for row in self.mapping_rows:
            table = NameNormalizer.normalize(
                self._first_non_empty(
                    row.new_db2_record,
                    row.cross_application_db2_table,
                )
            )

            column = NameNormalizer.normalize(
                self._first_non_empty(
                    row.new_db2_field_name,
                    row.cross_application_db2_field_name,
                )
            )

            if not table or not column:
                continue

            resolved_table = self._resolve_dclgen_table(table)

            if resolved_table not in normalized_used:
                continue

            if resolved_table not in normalized_write:
                continue

            kind = self._audit_kind(column)

            if not kind:
                continue

            # For current update flows, TS_CREATE must not be generated.
            if kind == "TS_CREATE":
                continue

            host = self._host_for_table_column(
                table=resolved_table,
                column=column,
            )

            group = self._dclgen_group_for_table(resolved_table)

            if not host or not group:
                continue

            key = (
                resolved_table,
                column,
            )

            if key in seen:
                continue

            seen.add(key)

            output.append(
                {
                    "table": resolved_table,
                    "column": column,
                    "kind": kind,
                    "reference": f"{host} OF {group}",
                }
            )

        return output

    def _audit_kind(
        self,
        column: str,
    ) -> str:
        normalized = NameNormalizer.normalize(column)

        if normalized.startswith("TS_CREATE"):
            return "TS_CREATE"

        if normalized.startswith("TS_UPDATE"):
            return "TS_UPDATE"

        if normalized.startswith("ID_USERID"):
            return "USER_ID"

        if normalized.startswith("NR_USERID"):
            return "USER_ID"

        if normalized.startswith("ID_USER"):
            return "USER_ID"

        if normalized.startswith("NR_USER"):
            return "USER_ID"

        return ""

    def _ensure_timestamp_working_storage(
        self,
        text: str,
        target_program_id: str,
    ) -> str:
        if self.WS_MARKER in text:
            return text

        block = self._timestamp_working_storage_block(target_program_id)

        lines = text.splitlines()
        insert_index = self._working_storage_insert_index(lines)

        if insert_index < 0:
            return text.rstrip() + "\n\n" + block + "\n"

        updated_lines = (
            lines[:insert_index]
            + block.splitlines()
            + [""]
            + lines[insert_index:]
        )

        return "\n".join(updated_lines)

    def _ensure_timestamp_paragraph(
        self,
        text: str,
    ) -> str:
        if re.search(
            rf"^\s*(?:\d{{6}}\s+)?{re.escape(self.PARAGRAPH_NAME)}\.",
            text,
            flags=re.IGNORECASE | re.MULTILINE,
        ):
            return text

        block = self._timestamp_paragraph_block()

        lines = text.splitlines()
        insert_index = self._timestamp_paragraph_insert_index(lines)

        if insert_index < 0:
            return text.rstrip() + "\n\n" + block + "\n"

        updated_lines = (
            lines[:insert_index]
            + block.splitlines()
            + [""]
            + lines[insert_index:]
        )

        return "\n".join(updated_lines)

    def _ensure_timestamp_perform(
        self,
        text: str,
    ) -> str:
        if re.search(
            rf"\bPERFORM\s+{re.escape(self.PARAGRAPH_NAME)}\b",
            text,
            flags=re.IGNORECASE,
        ):
            return text

        lines = text.splitlines()
        procedure_index = self._find_pattern_index(
            lines=lines,
            pattern=self.PROCEDURE_DIVISION_PATTERN,
        )

        if procedure_index < 0:
            return text

        insert_index = procedure_index + 1

        while insert_index < len(lines):
            logical = self._logical_line(lines[insert_index])

            if not logical or logical.startswith("*"):
                insert_index += 1
                continue

            break

        updated_lines = (
            lines[:insert_index]
            + [f"PERFORM {self.PARAGRAPH_NAME}"]
            + lines[insert_index:]
        )

        return "\n".join(updated_lines)

    def _ensure_audit_moves(
        self,
        text: str,
        audit_specs: list[dict[str, str]],
    ) -> str:
        move_lines = self._audit_move_lines(audit_specs)

        if not move_lines:
            return text

        existing_upper = text.upper()
        filtered_lines: list[str] = []

        for line in move_lines:
            if line.upper() in existing_upper:
                continue

            filtered_lines.append(line)

        if not filtered_lines:
            return text

        lines = text.splitlines()
        insert_index = self._audit_moves_insert_index(lines)

        if insert_index < 0:
            return text.rstrip() + "\n" + "\n".join(filtered_lines) + "\n"

        updated_lines = (
            lines[:insert_index]
            + filtered_lines
            + lines[insert_index:]
        )

        return "\n".join(updated_lines)

    def _audit_move_lines(
        self,
        audit_specs: list[dict[str, str]],
    ) -> list[str]:
        lines: list[str] = []

        for spec in audit_specs:
            kind = spec.get("kind", "")
            reference = spec.get("reference", "")

            if not reference:
                continue

            if kind == "TS_UPDATE":
                lines.append(f"MOVE TS-TIMESTAMP TO {reference}")
                continue

            if kind == "USER_ID":
                lines.append(f"MOVE CS-PROGRAM TO {reference}")
                continue

        return lines

    def _audit_moves_insert_index(
        self,
        lines: list[str],
    ) -> int:
        for index, line in enumerate(lines):
            logical = self._logical_line(line)

            if re.search(
                r"\bMOVE\s+'UPDATE-[A-Z0-9-]+'\s+TO\s+SQL-LOCATION\b",
                logical,
                flags=re.IGNORECASE,
            ):
                return index + 1

        for index, line in enumerate(lines):
            logical = self._logical_line(line)

            if re.search(
                r"^\s*EXEC\s+SQL\b",
                logical,
                flags=re.IGNORECASE,
            ):
                previous = self._previous_non_blank_line(lines, index)

                if re.search(
                    r"\bUPDATE-[A-Z0-9-]+\b",
                    previous,
                    flags=re.IGNORECASE,
                ):
                    return index

        return -1

    def _timestamp_working_storage_block(
        self,
        target_program_id: str,
    ) -> str:
        program_id = str(target_program_id or "").strip().upper()

        if not program_id:
            program_id = "DB2PGM"

        if len(program_id) > 8:
            program_id = program_id[:8]

        lines = [
            "******************************************************************",
            "* DB2 GENERATED TIMESTAMP AND AUDIT WORKING STORAGE              *",
            "******************************************************************",
            f"01  CS-PROGRAM                  PIC X(8) VALUE '{program_id:<8}'.",
            "01  WS-TIMESTAMP-FIELDS.",
            "    05  TS-SYSTEM.",
            "        10  DA-SYS.",
            "            15  DA-SYS-CCYY.",
            "                20  CC            PIC X(2).",
            "                20  YY            PIC X(2).",
            "            15  MM                PIC X(2).",
            "            15  DD                PIC X(2).",
            "        10  HR-SYS.",
            "            15  HH                PIC X(2).",
            "            15  MI                PIC X(2).",
            "            15  SS                PIC X(2).",
            "            15  TT                PIC X(2).",
            "    05  TS-TIMESTAMP.",
            "        10  CC                    PIC X(2).",
            "        10  YY                    PIC X(2).",
            "        10  TE-MARKER1            PIC X VALUE '-'.",
            "        10  MM                    PIC X(2).",
            "        10  TE-MARKER2            PIC X VALUE '-'.",
            "        10  DD                    PIC X(2).",
            "        10  TE-MARKER3            PIC X VALUE '-'.",
            "        10  HH                    PIC X(2).",
            "        10  TE-MARKER4            PIC X VALUE '.'.",
            "        10  MI                    PIC X(2).",
            "        10  TE-MARKER5            PIC X VALUE '.'.",
            "        10  SS                    PIC X(2).",
            "        10  TE-MARKER6            PIC X VALUE '.'.",
            "        10  TT                    PIC X(2).",
            "        10  NNNN                  PIC 9(04) VALUE 0.",
        ]

        return "\n".join(lines)

    def _timestamp_paragraph_block(
        self,
    ) -> str:
        lines = [
            f"{self.PARAGRAPH_NAME}.",
            "MOVE FUNCTION CURRENT-DATE TO TS-SYSTEM.",
            "MOVE CC OF TS-SYSTEM TO CC OF TS-TIMESTAMP.",
            "MOVE YY OF TS-SYSTEM TO YY OF TS-TIMESTAMP.",
            "MOVE MM OF TS-SYSTEM TO MM OF TS-TIMESTAMP.",
            "MOVE DD OF TS-SYSTEM TO DD OF TS-TIMESTAMP.",
            "MOVE HH OF TS-SYSTEM TO HH OF TS-TIMESTAMP.",
            "MOVE MI OF TS-SYSTEM TO MI OF TS-TIMESTAMP.",
            "MOVE SS OF TS-SYSTEM TO SS OF TS-TIMESTAMP.",
            "MOVE TT OF TS-SYSTEM TO TT OF TS-TIMESTAMP.",
            "DISPLAY 'TIMESTAMP: ' TS-TIMESTAMP.",
            "",
            f"{self.PARAGRAPH_EXIT_NAME}.",
            "EXIT.",
        ]

        return "\n".join(lines)

    def _has_db2_write_activity(
        self,
        text: str,
    ) -> bool:
        in_exec_sql = False

        for line in (text or "").splitlines():
            logical = self._logical_line(line)

            if self.EXEC_SQL_PATTERN.match(logical):
                in_exec_sql = True

            if in_exec_sql and self.DB2_WRITE_OPERATION_PATTERN.search(logical):
                return True

            if self.END_EXEC_PATTERN.match(logical):
                in_exec_sql = False

            if self.COBOL_WRITE_OPERATION_PATTERN.search(logical):
                return True

        return False

    def _working_storage_insert_index(
        self,
        lines: list[str],
    ) -> int:
        linkage_index = self._find_pattern_index(
            lines=lines,
            pattern=self.LINKAGE_SECTION_PATTERN,
        )

        if linkage_index >= 0:
            return linkage_index

        procedure_index = self._find_pattern_index(
            lines=lines,
            pattern=self.PROCEDURE_DIVISION_PATTERN,
        )

        if procedure_index >= 0:
            return procedure_index

        working_storage_index = self._find_pattern_index(
            lines=lines,
            pattern=self.WORKING_STORAGE_PATTERN,
        )

        if working_storage_index >= 0:
            return working_storage_index + 1

        return -1

    def _timestamp_paragraph_insert_index(
        self,
        lines: list[str],
    ) -> int:
        # Insert before STOP RUN so it is near the end, but the generated EXIT
        # paragraph prevents PERFORM from falling into STOP RUN.
        for index, line in enumerate(lines):
            logical = self._logical_line(line)

            if self.STOP_RUN_PATTERN.match(logical):
                return index

            if self.END_PROGRAM_PATTERN.match(logical):
                return index

        return len(lines)

    def _find_pattern_index(
        self,
        lines: list[str],
        pattern: re.Pattern,
    ) -> int:
        for index, line in enumerate(lines):
            logical = self._logical_line(line)

            if pattern.match(logical):
                return index

        return -1

    def _previous_non_blank_line(
        self,
        lines: list[str],
        start_index: int,
    ) -> str:
        for index in range(start_index - 1, -1, -1):
            logical = self._logical_line(lines[index])

            if logical:
                return logical

        return ""

    def _logical_line(
        self,
        line: str,
    ) -> str:
        text = str(line or "").rstrip()

        if len(text) >= 6 and text[:6].strip().isdigit():
            text = text[6:]

        if len(text) >= 8 and text[-8:].strip().isdigit():
            text = text[:-8]

        return text.strip()

    def _build_dclgen_host_lookup(
        self,
        columns: list[DclgenColumn],
    ) -> dict[tuple[str, str], str]:
        output: dict[tuple[str, str], str] = {}

        for column in columns:
            table = NameNormalizer.normalize(column.table_name)
            column_name = NameNormalizer.normalize(column.column_name)
            host_name = NameNormalizer.to_cobol(
                column.cobol_host_name or column.column_name,
            )

            if not table or not column_name or not host_name:
                continue

            for candidate in self._table_candidates(table):
                output[(candidate, column_name)] = host_name

        return output

    def _build_dclgen_group_lookup(
        self,
        columns: list[DclgenColumn],
    ) -> dict[str, str]:
        output: dict[str, str] = {}

        for column in columns:
            table = NameNormalizer.normalize(column.table_name)

            if not table:
                continue

            group_name = "DCL" + NameNormalizer.to_cobol(table)

            for candidate in self._table_candidates(table):
                output[candidate] = group_name

        return output

    def _build_dclgen_table_catalog(
        self,
        columns: list[DclgenColumn],
    ) -> set[str]:
        output: set[str] = set()

        for column in columns:
            table = NameNormalizer.normalize(column.table_name)

            if table:
                output.add(table)

        return output

    def _host_for_table_column(
        self,
        table: str,
        column: str,
    ) -> str:
        table = NameNormalizer.normalize(table)
        column = NameNormalizer.normalize(column)

        for candidate in self._table_candidates(table):
            host = self.dclgen_host_lookup.get(
                (
                    candidate,
                    column,
                ),
                "",
            )

            if host:
                return host

        return ""

    def _dclgen_group_for_table(
        self,
        table: str,
    ) -> str:
        table = NameNormalizer.normalize(table)

        for candidate in self._table_candidates(table):
            group = self.dclgen_group_lookup.get(candidate, "")

            if group:
                return group

        return "DCL" + NameNormalizer.to_cobol(table)

    def _resolve_dclgen_table(
        self,
        table: str,
    ) -> str:
        normalized = NameNormalizer.normalize(table)

        if not normalized:
            return ""

        for candidate in self._table_candidates(normalized):
            if candidate in self.dclgen_table_catalog:
                return candidate

        return normalized

    def _table_candidates(
        self,
        table: str,
    ) -> list[str]:
        normalized = NameNormalizer.normalize(table)

        if not normalized:
            return []

        output = [normalized]

        if normalized.endswith("_TB"):
            output.append(normalized[:-3] + "_TV")

        if normalized.endswith("_TV"):
            output.append(normalized[:-3] + "_TB")

        if normalized.endswith("TB"):
            output.append(normalized[:-2] + "TV")

        if normalized.endswith("TV"):
            output.append(normalized[:-2] + "TB")

        output.append(NameNormalizer.to_cobol(normalized))
        output.append(NameNormalizer.compact(normalized))

        final: list[str] = []

        for item in output:
            if item and item not in final:
                final.append(item)

        return final

    def _first_non_empty(
        self,
        *values: str,
    ) -> str:
        for value in values:
            text = str(value or "").strip()

            if text:
                return text

        return ""