from __future__ import annotations

import re
from difflib import SequenceMatcher

from idms_db2_phase2.domain.models import DclgenColumn, SheetMappingRow
from idms_db2_phase2.services.name_normalizer import NameNormalizer


class FieldReferenceRewriter:
    """
    Production-safe field reference rewriter.

    Core rules:
    - Sheet Mapping is the authority for DB2 table and DB2 column names.
    - DCLGEN is the authority for COBOL host variable spelling and group names.
    - Qualified IDMS references are rewritten:
        FIELD OF RECORD
        FIELD IN RECORD
    - Working-storage groups, linkage groups, date helper groups, timestamp groups,
      output groups, copybook input groups, and string literals are never treated
      as IDMS records.
    - Bare MOVE target rewrite is allowed only when active record context exists.
    - Active context can come from paragraph names, MOVE SPACES TO record,
      INITIALIZE DCLxxxx, or nearest DCLGEN host reference.
    - REDEFINES aliases are resolved through Sheet Mapping.
    - If a real IDMS qualified reference cannot be mapped because Sheet Mapping
      is missing, the executable line is replaced with a clear comment and CONTINUE.
    """

    MIN_DCLGEN_FALLBACK_SCORE = 85

    QUALIFIED_REFERENCE_PATTERN = re.compile(
        r"\b(?P<field>[A-Z][A-Z0-9-]*)\s+"
        r"(?P<qualifier>OF|IN)\s+"
        r"(?P<record>[A-Z][A-Z0-9-]*)\b",
        flags=re.IGNORECASE,
    )

    REDEFINES_PATTERN = re.compile(
        r"\b(?P<alias>[A-Z][A-Z0-9-]*)\s+REDEFINES\s+"
        r"(?P<base>[A-Z][A-Z0-9-]*)\b",
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
        r"^\s*(0[1-9]|[1-4][0-9]|66|77|88)\s+",
        flags=re.IGNORECASE,
    )

    COBOL_LEVEL_AND_NAME_PATTERN = re.compile(
        r"^\s*(?P<level>0[1-9]|[1-4][0-9]|66|77|88)\s+"
        r"(?P<name>[A-Z][A-Z0-9-]*)\b",
        flags=re.IGNORECASE,
    )

    PARAGRAPH_PATTERN = re.compile(
        r"^\s*(?P<name>[A-Z0-9][A-Z0-9-]*)\.\s*$",
        flags=re.IGNORECASE,
    )

    RECORD_CONTEXT_PARAGRAPH_PATTERN = re.compile(
        r"^\s*(?:VERWERK|PROCESS|READ|HANDLE|CHECK|BUILD|WRITE|UPDATE|INSERT|DELETE)-"
        r"(?P<record>[A-Z][A-Z0-9-]*)\.\s*$",
        flags=re.IGNORECASE,
    )

    MOVE_TO_PATTERN = re.compile(
        r"^(?P<left_seq>\s*\d{6}\s+)?"
        r"(?P<prefix>MOVE\s+)"
        r"(?P<source>.+?)"
        r"(?P<to>\s+TO\s+)"
        r"(?P<target>[A-Z][A-Z0-9-]*)"
        r"(?P<body_suffix>\.?\s*)"
        r"(?P<right_seq>\d{8}\s*)?$",
        flags=re.IGNORECASE,
    )

    MOVE_SPACES_TO_RECORD_PATTERN = re.compile(
        r"^(?P<left_seq>\s*\d{6}\s+)?"
        r"(?P<prefix>MOVE\s+)"
        r"(?P<literal>SPACES|SPACE|ZEROES|ZEROS|LOW-VALUES|HIGH-VALUES)"
        r"(?P<to>\s+TO\s+)"
        r"(?P<record>[A-Z][A-Z0-9-]*)"
        r"(?P<body_suffix>\.?\s*)"
        r"(?P<right_seq>\d{8}\s*)?$",
        flags=re.IGNORECASE,
    )

    INITIALIZE_DCLGEN_PATTERN = re.compile(
        r"^\s*INITIALIZE\s+(?P<group>DCL[A-Z0-9-]+)\.?\s*$",
        flags=re.IGNORECASE,
    )

    DCLGEN_HOST_REFERENCE_PATTERN = re.compile(
        r"\b(?P<field>[A-Z][A-Z0-9-]*)\s+(?:OF|IN)\s+(?P<group>DCL[A-Z0-9-]+)\b",
        flags=re.IGNORECASE,
    )

    SQL_LOCATION_PATTERN = re.compile(
        r"\bMOVE\s+'[A-Z0-9-]+'\s+TO\s+SQL-LOCATION\b",
        flags=re.IGNORECASE,
    )

    BARE_IDENTIFIER_PATTERN = re.compile(
        r"^[A-Z][A-Z0-9-]*$",
        flags=re.IGNORECASE,
    )

    CONDITION_OPERATOR_PATTERN = re.compile(
        r"(?P<left>\b[A-Z][A-Z0-9-]*\b)"
        r"(?P<space1>\s*)"
        r"(?P<op>=|>|<|>=|<=|NOT\s+=|NOT\s+>|NOT\s+<)"
        r"(?P<space2>\s*)"
        r"(?P<right>\b[A-Z][A-Z0-9-]*\b|'[^']*'|\"[^\"]*\"|[0-9]+)",
        flags=re.IGNORECASE,
    )

    HEADER_PATTERNS = [
        re.compile(r"^\s*PROGRAM-ID\.", re.IGNORECASE),
        re.compile(r"^\s*AUTHOR\.", re.IGNORECASE),
        re.compile(r"^\s*DATE-WRITTEN\.", re.IGNORECASE),
        re.compile(r"^\s*DATE-COMPILED\.", re.IGNORECASE),
        re.compile(r"^\s*IDENTIFICATION\s+DIVISION\.", re.IGNORECASE),
        re.compile(r"^\s*ENVIRONMENT\s+DIVISION\.", re.IGNORECASE),
        re.compile(r"^\s*DATA\s+DIVISION\.", re.IGNORECASE),
        re.compile(r"^\s*PROCEDURE\s+DIVISION\b", re.IGNORECASE),
        re.compile(r"^\s*WORKING-STORAGE\s+SECTION\.", re.IGNORECASE),
        re.compile(r"^\s*FILE\s+SECTION\.", re.IGNORECASE),
        re.compile(r"^\s*LINKAGE\s+SECTION\.", re.IGNORECASE),
    ]

    UNSAFE_WORDS = {
        "ACCEPT",
        "ADD",
        "AND",
        "BY",
        "CALL",
        "CLOSE",
        "COMPUTE",
        "CONTINUE",
        "DELETE",
        "DISPLAY",
        "DIVIDE",
        "ELSE",
        "END",
        "END-IF",
        "END-PERFORM",
        "END-READ",
        "END-EXEC",
        "EVALUATE",
        "EXEC",
        "EXIT",
        "FROM",
        "GO",
        "GOBACK",
        "IF",
        "INITIALIZE",
        "INTO",
        "MOVE",
        "MULTIPLY",
        "NEXT",
        "NOT",
        "OF",
        "OPEN",
        "OR",
        "PERFORM",
        "READ",
        "RETURN",
        "REWRITE",
        "SET",
        "SQL",
        "STOP",
        "STRING",
        "SUBTRACT",
        "THEN",
        "TO",
        "UNTIL",
        "VARYING",
        "WHEN",
        "WRITE",
    }

    PROTECTED_QUALIFIERS = {
        "PARMDATE",
        "DATE6",
        "DATE8",
        "DATE6R_WS",
        "DATE6R-WS",
        "DATE_YMD",
        "DATE-YMD",
        "DATE_DMY",
        "DATE-DMY",
        "DATE_YMD8",
        "DATE-YMD8",
        "DATE_DMY8",
        "DATE-DMY8",
        "ZONE_DATONLY",
        "ZONE-DATONLY",
        "STOP_DATONLY",
        "STOP-DATONLY",
        "DATUMLS",
        "PARAM_DATONLY",
        "PARAM-DATONLY",
        "TS_SYSTEM",
        "TS-SYSTEM",
        "TS_TIMESTAMP",
        "TS-TIMESTAMP",
        "TS_SYSTEM_FIELDS",
        "TS-SYSTEM-FIELDS",
        "DA_SYS",
        "DA-SYS",
        "HR_SYS",
        "HR-SYS",
        "ES_DATUM",
        "ES-DATUM",
        "ERROR_STATUS",
        "ERROR-STATUS",
        "ERROR",
        "UITRECORD",
        "OPERATIES_REC",
        "OPERATIES-REC",
        "VMBD205I",
        "VMDZ205I",
    }

    PROTECTED_QUALIFIER_PREFIXES = (
        "TS_",
        "TS-",
        "WS_",
        "WS-",
        "DATE_",
        "DATE-",
        "DA_",
        "DA-",
        "HR_",
        "HR-",
        "ES_",
        "ES-",
        "SQL",
        "DCL",
        "PARAM",
        "DATUMLS",
        "STOP_",
        "STOP-",
        "UIT_",
        "UIT-",
        "UIT",
        "OPERATIES",
        "ERROR",
    )

    def __init__(
        self,
        mapping_rows: list[SheetMappingRow],
        dclgen_columns: list[DclgenColumn],
    ) -> None:
        self.mapping_rows = mapping_rows or []
        self.dclgen_columns = dclgen_columns or []
        self.rewrite_messages: list[str] = []

        self.row_contexts = self._build_row_contexts()
        self.dclgen_host_lookup = self._build_dclgen_host_lookup()
        self.dclgen_group_lookup = self._build_dclgen_group_lookup()
        self.record_to_table_lookup = self._build_record_to_table_lookup()
        self.group_to_record_lookup = self._build_group_to_record_lookup()
        self.redefines_alias_lookup = self._build_redefines_alias_lookup()
        self.source_reference_lookup = self._build_source_reference_lookup()
        self.known_source_record_keys = self._build_known_source_record_keys()
        self.table_catalog = self._build_table_catalog()

    def rewrite(
        self,
        text: str,
    ) -> str:
        self.rewrite_messages = []
        self._add_startup_diagnostics()
        self._add_sample_mapping_diagnostics()

        if not text:
            return ""

        output_lines: list[str] = []
        in_exec_sql = False
        current_context_record = ""
        current_context_strength = "none"
        current_division = ""

        for raw_line in text.splitlines():
            line = raw_line
            logical = self._logical_line(line)

            current_division = self._division_from_line(
                logical=logical,
                current_division=current_division,
            )

            if self.EXEC_SQL_PATTERN.match(logical):
                in_exec_sql = True
                output_lines.append(line)
                continue

            if in_exec_sql:
                output_lines.append(line)

                if self.END_EXEC_PATTERN.match(logical):
                    in_exec_sql = False

                continue

            todo_replacement = self._todo_db2_replacement_comment(line)

            if todo_replacement:
                output_lines.extend(todo_replacement)
                continue

            paragraph_match = self.PARAGRAPH_PATTERN.match(logical)

            if paragraph_match:
                paragraph_name = paragraph_match.group("name")
                record_context = self._record_context_from_paragraph(paragraph_name)

                if record_context:
                    current_context_record = record_context
                    current_context_strength = "strong"
                else:
                    inferred_context = self._infer_record_context_from_paragraph(
                        paragraph_name,
                    )

                    if inferred_context:
                        current_context_record = inferred_context
                        current_context_strength = "inferred"
                    else:
                        current_context_record = ""
                        current_context_strength = "none"

                output_lines.append(line)
                continue

            line_context = self._record_context_from_executable_line(logical)

            if line_context:
                current_context_record = line_context
                current_context_strength = "strong"
                self.rewrite_messages.append(
                    f"Active record context inferred from executable line: {line_context}"
                )

            if self._must_skip_line(
                line=line,
                logical=logical,
                current_division=current_division,
            ):
                output_lines.append(line)
                continue

            unmapped_comment = self._line_with_unmapped_qualified_reference_comment(
                line=line,
            )

            if unmapped_comment:
                output_lines.extend(unmapped_comment)
                continue

            rewritten = self._rewrite_code_segments(
                line=line,
                current_context_record=current_context_record,
                context_strength=current_context_strength,
            )

            output_lines.append(rewritten)

        rewritten_text = "\n".join(output_lines)

        rewritten_text = self._rewrite_residual_bare_move_targets_by_nearest_dcl_context(
            rewritten_text,
        )

        return rewritten_text

    def _add_startup_diagnostics(self) -> None:
        self.rewrite_messages.append(
            f"Sheet Mapping rows received: {len(self.mapping_rows)}",
        )
        self.rewrite_messages.append(
            f"DCLGEN columns received: {len(self.dclgen_columns)}",
        )

    def _add_sample_mapping_diagnostics(self) -> None:
        useful_contexts = [
            context
            for context in self.row_contexts
            if context.get("source_records")
            and context.get("source_fields")
            and context.get("target_table")
            and context.get("target_column")
        ]

        self.rewrite_messages.append(
            f"Row contexts built: {len(self.row_contexts)}",
        )
        self.rewrite_messages.append(
            f"Useful row contexts with source and target: {len(useful_contexts)}",
        )
        self.rewrite_messages.append(
            f"Known source record keys built: {len(self.known_source_record_keys)}",
        )
        self.rewrite_messages.append(
            f"DCLGEN host lookup entries built: {len(self.dclgen_host_lookup)}",
        )
        self.rewrite_messages.append(
            f"DCLGEN group lookup entries built: {len(self.dclgen_group_lookup)}",
        )
        self.rewrite_messages.append(
            f"REDEFINES alias mappings built: {len(self.redefines_alias_lookup)}",
        )
        self.rewrite_messages.append(
            f"Source-reference mappings built: {len(self.source_reference_lookup)}",
        )

    def _build_row_contexts(self) -> list[dict[str, object]]:
        contexts: list[dict[str, object]] = []
        current_source_records: list[str] = []

        for row_index, row in enumerate(self.mapping_rows, start=1):
            row_source_records = self._source_record_values_from_record_column(row)

            if row_source_records:
                current_source_records = row_source_records

            source_records = row_source_records or current_source_records
            source_fields = self._source_field_values(row)

            target_table = self._first_non_empty(
                row.new_db2_record,
                row.cross_application_db2_table,
            )

            target_column = self._first_non_empty(
                row.new_db2_field_name,
                row.cross_application_db2_field_name,
            )

            contexts.append(
                {
                    "row_index": row_index,
                    "row": row,
                    "source_records": source_records,
                    "source_fields": source_fields,
                    "target_table": self._normalize_name(target_table),
                    "target_column": self._normalize_name(target_column),
                }
            )

        return contexts

    def _build_known_source_record_keys(self) -> set[str]:
        keys: set[str] = set()

        for context in self.row_contexts:
            for record in context.get("source_records") or []:
                for key in self._name_match_keys(str(record)):
                    keys.add(key)

        return keys

    def _build_dclgen_host_lookup(self) -> dict[tuple[str, str], str]:
        lookup: dict[tuple[str, str], str] = {}

        for column in self.dclgen_columns:
            table = self._normalize_name(column.table_name)
            column_name = self._normalize_name(column.column_name)

            host_name = NameNormalizer.to_cobol(
                column.cobol_host_name or column.column_name,
            )

            if not table or not column_name or not host_name:
                continue

            for table_candidate in self._table_candidates(table):
                lookup[(table_candidate, column_name)] = host_name

        return lookup

    def _build_dclgen_group_lookup(self) -> dict[str, str]:
        lookup: dict[str, str] = {}

        for column in self.dclgen_columns:
            table = self._normalize_name(column.table_name)

            if not table:
                continue

            group_name = "DCL" + NameNormalizer.to_cobol(table)

            for table_candidate in self._table_candidates(table):
                lookup[table_candidate] = group_name

        return lookup

    def _build_redefines_alias_lookup(self) -> dict[tuple[str, str], list[str]]:
        lookup: dict[tuple[str, str], list[str]] = {}

        for context in self.row_contexts:
            row = context.get("row")

            if not isinstance(row, SheetMappingRow):
                continue

            source_records = context.get("source_records") or []

            if not source_records:
                continue

            texts = [
                str(row.cobol_zone or ""),
                str(row.reference_field_name_copybook or ""),
                str(row.remarks or ""),
            ]

            for text in texts:
                for match in self.REDEFINES_PATTERN.finditer(text):
                    alias_field = self._normalize_name(match.group("alias"))
                    base_field = self._normalize_name(match.group("base"))

                    if not alias_field or not base_field:
                        continue

                    for source_record in source_records:
                        for record_key in self._name_match_keys(str(source_record)):
                            for alias_key in self._name_match_keys(alias_field):
                                base_keys = lookup.setdefault(
                                    (
                                        record_key,
                                        alias_key,
                                    ),
                                    [],
                                )

                                for base_key in self._name_match_keys(base_field):
                                    if base_key and base_key not in base_keys:
                                        base_keys.append(base_key)

        return lookup

    def _build_source_reference_lookup(self) -> dict[tuple[str, str], str]:
        lookup: dict[tuple[str, str], str] = {}

        for context in self.row_contexts:
            row = context.get("row")

            if not isinstance(row, SheetMappingRow):
                continue

            source_records = context.get("source_records") or []
            source_fields = context.get("source_fields") or []

            if not source_records or not source_fields:
                continue

            target = self._target_for_mapping_row(row)

            if not target:
                continue

            for source_record in source_records:
                for source_field in source_fields:
                    for record_key in self._name_match_keys(str(source_record)):
                        for field_key in self._name_match_keys(str(source_field)):
                            lookup[(record_key, field_key)] = target

        self._apply_redefines_aliases_to_source_lookup(lookup)

        return lookup

    def _apply_redefines_aliases_to_source_lookup(
        self,
        lookup: dict[tuple[str, str], str],
    ) -> None:
        if not self.redefines_alias_lookup:
            return

        changed = True
        pass_count = 0

        while changed and pass_count < 5:
            changed = False
            pass_count += 1

            for alias_lookup_key, base_field_keys in self.redefines_alias_lookup.items():
                record_key, alias_field_key = alias_lookup_key

                if lookup.get(
                    (
                        record_key,
                        alias_field_key,
                    )
                ):
                    continue

                for base_field_key in base_field_keys:
                    target = lookup.get(
                        (
                            record_key,
                            base_field_key,
                        ),
                        "",
                    )

                    if target:
                        lookup[(record_key, alias_field_key)] = target
                        changed = True
                        break

    def _build_record_to_table_lookup(self) -> dict[str, str]:
        lookup: dict[str, str] = {}

        for context in self.row_contexts:
            target_table = str(context.get("target_table") or "")

            if not target_table:
                continue

            for source_record in context.get("source_records") or []:
                for key in self._name_match_keys(str(source_record)):
                    if key and key not in lookup:
                        lookup[key] = target_table

        return lookup

    def _build_group_to_record_lookup(self) -> dict[str, str]:
        lookup: dict[str, str] = {}

        for record_key, table in self.record_to_table_lookup.items():
            group = self._dclgen_group_for_table(table)

            if group and record_key:
                lookup[self._normalize_name(group)] = NameNormalizer.to_cobol(record_key)

        return lookup

    def _build_table_catalog(self) -> list[str]:
        output: list[str] = []

        for row in self.mapping_rows:
            table = self._first_non_empty(
                row.new_db2_record,
                row.cross_application_db2_table,
            )

            normalized = self._normalize_name(table)

            if normalized and normalized not in output:
                output.append(normalized)

        for column in self.dclgen_columns:
            table = self._normalize_name(column.table_name)

            if table and table not in output:
                output.append(table)

        return output

    def _source_record_values_from_record_column(
        self,
        row: SheetMappingRow,
    ) -> list[str]:
        return self._record_name_candidates(
            row.cobol_record_idms,
        )

    def _source_field_values(
        self,
        row: SheetMappingRow,
    ) -> list[str]:
        return self._field_name_candidates(
            row.cobol_zone,
            row.reference_field_name_copybook,
        )

    def _target_for_mapping_row(
        self,
        row: SheetMappingRow,
    ) -> str:
        table = self._normalize_name(
            self._first_non_empty(
                row.new_db2_record,
                row.cross_application_db2_table,
            )
        )

        column = self._normalize_name(
            self._first_non_empty(
                row.new_db2_field_name,
                row.cross_application_db2_field_name,
            )
        )

        if not table or not column:
            return ""

        host_name = self._host_for_table_column(
            table=table,
            column=column,
        )

        group_name = self._dclgen_group_for_table(table)

        if not host_name or not group_name:
            return ""

        return f"{host_name} OF {group_name}"

    def _host_for_table_column(
        self,
        table: str,
        column: str,
    ) -> str:
        table = self._normalize_name(table)
        column = self._normalize_name(column)

        if not table or not column:
            return ""

        for table_candidate in self._table_candidates(table):
            host_name = self.dclgen_host_lookup.get(
                (
                    table_candidate,
                    column,
                ),
                "",
            )

            if host_name:
                return host_name

        return NameNormalizer.to_cobol(column)

    def _dclgen_group_for_table(
        self,
        table: str,
    ) -> str:
        table = self._normalize_name(table)

        if not table:
            return ""

        for table_candidate in self._table_candidates(table):
            group_name = self.dclgen_group_lookup.get(
                table_candidate,
                "",
            )

            if group_name:
                return group_name

        return "DCL" + NameNormalizer.to_cobol(table)

    def _rewrite_code_segments(
        self,
        line: str,
        current_context_record: str,
        context_strength: str,
    ) -> str:
        segments = self._split_string_segments(line)
        output_segments: list[str] = []

        for segment_text, is_string in segments:
            if is_string:
                output_segments.append(segment_text)
                continue

            rewritten = self._rewrite_record_initialization_in_line(
                segment_text,
            )

            rewritten = self._rewrite_qualified_references_in_line(
                rewritten,
            )

            rewritten = self._rewrite_contextual_bare_references_in_line(
                line=rewritten,
                current_context_record=current_context_record,
                context_strength=context_strength,
            )

            output_segments.append(rewritten)

        return "".join(output_segments)

    def _rewrite_record_initialization_in_line(
        self,
        line: str,
    ) -> str:
        match = self.MOVE_SPACES_TO_RECORD_PATTERN.match(line)

        if not match:
            return line

        source_record = match.group("record")

        if self._looks_like_dclgen_record(source_record):
            return line

        group_name = self._dclgen_group_for_source_record(source_record)

        if not group_name:
            return line

        self.rewrite_messages.append(
            f"Record initialization rewrite used: MOVE "
            f"{match.group('literal')} TO {source_record} > INITIALIZE {group_name}",
        )

        left_seq = match.group("left_seq") or ""
        right_seq = match.group("right_seq") or ""
        body_suffix = match.group("body_suffix") or ""

        return (
            f"{left_seq}"
            f"INITIALIZE {group_name}"
            f"{body_suffix}"
            f"{right_seq}"
        )

    def _rewrite_qualified_references_in_line(
        self,
        line: str,
    ) -> str:
        def repl(match: re.Match) -> str:
            source_field = match.group("field")
            source_record = match.group("record")

            if self._looks_like_dclgen_record(source_record):
                return match.group(0)

            if self._is_protected_qualifier(source_record):
                return match.group(0)

            target = self._target_for_source_reference(
                source_field=source_field,
                source_record=source_record,
                allow_fallback=True,
            )

            if not target:
                return match.group(0)

            self.rewrite_messages.append(
                f"Rewritten IDMS reference: {source_field} OF {source_record} > {target}",
            )

            return target

        return self.QUALIFIED_REFERENCE_PATTERN.sub(
            repl,
            line,
        )

    def _rewrite_contextual_bare_references_in_line(
        self,
        line: str,
        current_context_record: str,
        context_strength: str,
    ) -> str:
        if not current_context_record:
            return line

        rewritten = self._rewrite_move_target_in_context(
            line=line,
            current_context_record=current_context_record,
        )

        if context_strength == "strong":
            rewritten = self._rewrite_move_source_in_context(
                line=rewritten,
                current_context_record=current_context_record,
            )
            rewritten = self._rewrite_condition_bare_fields_in_context(
                line=rewritten,
                current_context_record=current_context_record,
            )

        return rewritten

    def _rewrite_move_target_in_context(
        self,
        line: str,
        current_context_record: str,
    ) -> str:
        match = self.MOVE_TO_PATTERN.match(line)

        if not match:
            return line

        target = match.group("target").strip()
        clean_target = target.rstrip(".").strip()

        if not self._safe_bare_identifier(clean_target):
            return line

        mapped_target = self._target_for_source_reference(
            source_field=clean_target,
            source_record=current_context_record,
            allow_fallback=False,
        )

        if not mapped_target:
            return line

        self.rewrite_messages.append(
            f"Contextual MOVE target rewrite: {clean_target} in "
            f"{current_context_record} > {mapped_target}",
        )

        left_seq = match.group("left_seq") or ""
        right_seq = match.group("right_seq") or ""
        body_suffix = match.group("body_suffix") or ""

        return (
            f"{left_seq}"
            f"{match.group('prefix')}"
            f"{match.group('source')}"
            f"{match.group('to')}"
            f"{mapped_target}"
            f"{body_suffix}"
            f"{right_seq}"
        )

    def _rewrite_move_source_in_context(
        self,
        line: str,
        current_context_record: str,
    ) -> str:
        match = self.MOVE_TO_PATTERN.match(line)

        if not match:
            return line

        source = match.group("source").strip()

        if not self._safe_bare_identifier(source):
            return line

        mapped_source = self._target_for_source_reference(
            source_field=source,
            source_record=current_context_record,
            allow_fallback=False,
        )

        if not mapped_source:
            return line

        self.rewrite_messages.append(
            f"Contextual MOVE source rewrite: {source} in "
            f"{current_context_record} > {mapped_source}",
        )

        left_seq = match.group("left_seq") or ""
        right_seq = match.group("right_seq") or ""
        body_suffix = match.group("body_suffix") or ""

        return (
            f"{left_seq}"
            f"{match.group('prefix')}"
            f"{mapped_source}"
            f"{match.group('to')}"
            f"{match.group('target')}"
            f"{body_suffix}"
            f"{right_seq}"
        )

    def _rewrite_condition_bare_fields_in_context(
        self,
        line: str,
        current_context_record: str,
    ) -> str:
        if not re.search(r"\bIF\b|\bWHEN\b|\bUNTIL\b", line, flags=re.IGNORECASE):
            return line

        def repl(match: re.Match) -> str:
            left = match.group("left")
            right = match.group("right")

            new_left = left
            new_right = right

            if self._safe_bare_identifier(left):
                mapped_left = self._target_for_source_reference(
                    source_field=left,
                    source_record=current_context_record,
                    allow_fallback=False,
                )

                if mapped_left:
                    new_left = mapped_left

            if self._safe_bare_identifier(right):
                mapped_right = self._target_for_source_reference(
                    source_field=right,
                    source_record=current_context_record,
                    allow_fallback=False,
                )

                if mapped_right:
                    new_right = mapped_right

            return (
                f"{new_left}"
                f"{match.group('space1')}"
                f"{match.group('op')}"
                f"{match.group('space2')}"
                f"{new_right}"
            )

        return self.CONDITION_OPERATOR_PATTERN.sub(
            repl,
            line,
        )

    def _rewrite_residual_bare_move_targets_by_nearest_dcl_context(
        self,
        text: str,
    ) -> str:
        if not text:
            return ""

        output_lines: list[str] = []
        active_record = ""
        in_exec_sql = False

        for line in text.splitlines():
            logical = self._logical_line(line)

            if self.EXEC_SQL_PATTERN.match(logical):
                in_exec_sql = True
                output_lines.append(line)
                continue

            if in_exec_sql:
                output_lines.append(line)

                if self.END_EXEC_PATTERN.match(logical):
                    in_exec_sql = False

                continue

            init_context = self._record_context_from_executable_line(logical)

            if init_context:
                active_record = init_context

            dcl_ref_match = self.DCLGEN_HOST_REFERENCE_PATTERN.search(logical)

            if dcl_ref_match:
                group = self._normalize_name(dcl_ref_match.group("group"))
                record = self.group_to_record_lookup.get(group, "")

                if record:
                    active_record = NameNormalizer.to_cobol(record)

            if not active_record:
                output_lines.append(line)
                continue

            if self._is_comment_line(line):
                output_lines.append(line)
                continue

            if self.SQL_LOCATION_PATTERN.search(logical):
                output_lines.append(line)
                continue

            rewritten = self._rewrite_move_target_in_context(
                line=line,
                current_context_record=active_record,
            )

            if rewritten != line:
                self.rewrite_messages.append(
                    f"Residual MOVE target rewrite used with nearest DCL context {active_record}: {logical}"
                )

            output_lines.append(rewritten)

        return "\n".join(output_lines)

    def _target_for_source_reference(
        self,
        source_field: str,
        source_record: str,
        allow_fallback: bool = True,
    ) -> str:
        source_record_normalized = self._normalize_name(source_record)
        source_field_normalized = self._normalize_name(source_field)

        if not source_record_normalized or not source_field_normalized:
            return ""

        for record_key in self._name_match_keys(source_record_normalized):
            for field_key in self._name_match_keys(source_field_normalized):
                target = self.source_reference_lookup.get(
                    (
                        record_key,
                        field_key,
                    ),
                    "",
                )

                if target:
                    return target

        redefines_target = self._target_for_redefines_alias(
            source_field=source_field,
            source_record=source_record,
        )

        if redefines_target:
            return redefines_target

        if not allow_fallback:
            return ""

        return self._fallback_target_for_source_reference(
            source_field=source_field,
            source_record=source_record,
            minimum_score=self.MIN_DCLGEN_FALLBACK_SCORE,
            contextual=False,
        )

    def _target_for_redefines_alias(
        self,
        source_field: str,
        source_record: str,
    ) -> str:
        source_record_normalized = self._normalize_name(source_record)
        source_field_normalized = self._normalize_name(source_field)

        if not source_record_normalized or not source_field_normalized:
            return ""

        for record_key in self._name_match_keys(source_record_normalized):
            for alias_field_key in self._name_match_keys(source_field_normalized):
                base_field_keys = self.redefines_alias_lookup.get(
                    (
                        record_key,
                        alias_field_key,
                    ),
                    [],
                )

                for base_field_key in base_field_keys:
                    target = self.source_reference_lookup.get(
                        (
                            record_key,
                            base_field_key,
                        ),
                        "",
                    )

                    if target:
                        self.rewrite_messages.append(
                            "REDEFINES mapping used: "
                            f"{source_field} OF {source_record} > {target}"
                        )
                        return target

        return ""

    def _fallback_target_for_source_reference(
        self,
        source_field: str,
        source_record: str,
        minimum_score: int = 40,
        contextual: bool = False,
    ) -> str:
        source_record_normalized = self._normalize_name(source_record)
        source_field_normalized = self._normalize_name(source_field)

        if not source_record_normalized or not source_field_normalized:
            return ""

        table_candidates = self._fallback_table_candidates_for_source_record(
            source_record_normalized,
        )

        if not table_candidates:
            return ""

        source_field_compact = self._compact_name(source_field_normalized)
        source_field_tokens = self._meaningful_tokens(source_field_normalized)

        best_column: DclgenColumn | None = None
        best_score = 0

        for column in self.dclgen_columns:
            table = self._normalize_name(column.table_name)

            if table not in table_candidates:
                continue

            column_name = self._normalize_name(column.column_name)
            host_name = self._normalize_name(
                column.cobol_host_name or column.column_name,
            )

            score = self._fallback_match_score(
                source_field_normalized=source_field_normalized,
                source_field_compact=source_field_compact,
                source_field_tokens=source_field_tokens,
                column_name=column_name,
                host_name=host_name,
            )

            if score > best_score:
                best_score = score
                best_column = column

        effective_minimum_score = max(
            int(minimum_score or 0),
            self.MIN_DCLGEN_FALLBACK_SCORE,
        )

        if not best_column or best_score < effective_minimum_score:
            self.rewrite_messages.append(
                "DCLGEN fallback mapping skipped due to low confidence: "
                f"{source_field} OF {source_record} score={best_score}, "
                f"minimum={effective_minimum_score}"
            )
            return ""

        table_name = self._normalize_name(best_column.table_name)

        host_name = NameNormalizer.to_cobol(
            best_column.cobol_host_name or best_column.column_name,
        )

        group_name = self._dclgen_group_for_table(table_name)

        if not host_name or not group_name:
            return ""

        target = f"{host_name} OF {group_name}"

        if contextual:
            self.rewrite_messages.append(
                "Contextual DCLGEN fallback mapping used: "
                f"{source_field} in {source_record} > {target} score={best_score}"
            )
        else:
            self.rewrite_messages.append(
                "DCLGEN fallback mapping used: "
                f"{source_field} OF {source_record} > {target} score={best_score}"
            )

        return target

    def _fallback_table_candidates_for_source_record(
        self,
        source_record_normalized: str,
    ) -> set[str]:
        output: set[str] = set()

        for record_key in self._name_match_keys(source_record_normalized):
            table = self.record_to_table_lookup.get(record_key, "")

            if table:
                output.update(self._table_candidates(table))

        if output:
            return output

        best_table = self._best_table_for_source_record(source_record_normalized)

        if best_table:
            output.update(self._table_candidates(best_table))

        return output

    def _fallback_match_score(
        self,
        source_field_normalized: str,
        source_field_compact: str,
        source_field_tokens: list[str],
        column_name: str,
        host_name: str,
    ) -> int:
        column_compact = self._compact_name(column_name)
        host_compact = self._host_core_compact(host_name)

        best_ratio = int(
            max(
                SequenceMatcher(None, source_field_compact, column_compact).ratio(),
                SequenceMatcher(None, source_field_compact, host_compact).ratio(),
            )
            * 100
        )

        token_score = 0

        for token in source_field_tokens:
            token_compact = self._compact_name(token)

            if not token_compact:
                continue

            if token_compact in column_compact or token_compact in host_compact:
                token_score += 15

        if source_field_compact and source_field_compact in column_compact:
            token_score += 35

        if source_field_compact and source_field_compact in host_compact:
            token_score += 35

        return max(best_ratio, min(token_score, 100))

    def _record_context_from_executable_line(
        self,
        logical: str,
    ) -> str:
        text = str(logical or "").strip()

        if not text:
            return ""

        move_init_match = self.MOVE_SPACES_TO_RECORD_PATTERN.match(text)

        if move_init_match:
            record = self._normalize_name(move_init_match.group("record"))

            if record and self._dclgen_group_for_source_record(record):
                return NameNormalizer.to_cobol(record)

        init_match = self.INITIALIZE_DCLGEN_PATTERN.match(text)

        if init_match:
            group = self._normalize_name(init_match.group("group"))
            record = self.group_to_record_lookup.get(group, "")

            if record:
                return NameNormalizer.to_cobol(record)

        return ""

    def _dclgen_group_for_source_record(
        self,
        record: str,
    ) -> str:
        record_key_candidates = self._name_match_keys(record)

        for key in record_key_candidates:
            table = self.record_to_table_lookup.get(
                key,
                "",
            )

            if table:
                return self._dclgen_group_for_table(table)

        table = self._best_table_for_source_record(record)

        if table:
            return self._dclgen_group_for_table(table)

        return ""

    def _best_table_for_source_record(
        self,
        record: str,
    ) -> str:
        semantic_aliases = self._semantic_record_aliases(record)
        best_table = ""
        best_score = 0

        for table in self.table_catalog:
            table_aliases = self._semantic_table_aliases(table)
            score = self._record_table_match_score(
                semantic_aliases,
                table_aliases,
            )

            if score > best_score:
                best_score = score
                best_table = table

        if best_score >= 70:
            return best_table

        return ""

    def _todo_db2_replacement_comment(
        self,
        line: str,
    ) -> list[str]:
        logical = self._logical_line(line)

        if "TODO DB2" not in logical.upper():
            return []

        record_name = ""

        match = re.search(
            r"\bFOR\s+([A-Z][A-Z0-9-]*)\b",
            logical,
            flags=re.IGNORECASE,
        )

        if match:
            record_name = match.group(1).upper()

        if not record_name:
            match = re.search(
                r"\bRECORD\s+([A-Z][A-Z0-9-]*)\b",
                logical,
                flags=re.IGNORECASE,
            )

            if match:
                record_name = match.group(1).upper()

        if record_name:
            self.rewrite_messages.append(
                f"TODO DB2 replaced with Sheet Mapping missing comment for {record_name}"
            )
            return [
                "* DB2: Conversion skipped because Sheet Mapping entry does not exist.",
                f"* DB2: Missing Sheet Mapping metadata for record {record_name}.",
                "CONTINUE.",
            ]

        self.rewrite_messages.append(
            "TODO DB2 replaced with generic Sheet Mapping missing comment"
        )

        return [
            "* DB2: Conversion skipped because required Sheet Mapping metadata does not exist.",
            "CONTINUE.",
        ]

    def _line_with_unmapped_qualified_reference_comment(
        self,
        line: str,
    ) -> list[str]:
        logical = self._logical_line(line)

        if not logical:
            return []

        segments = self._split_string_segments(logical)
        code_text = "".join(
            segment
            for segment, is_string in segments
            if not is_string
        )

        matches = list(self.QUALIFIED_REFERENCE_PATTERN.finditer(code_text))

        if not matches:
            return []

        mapped_any = False

        for match in matches:
            source_field = match.group("field")
            source_record = match.group("record")

            if self._looks_like_dclgen_record(source_record):
                mapped_any = True
                continue

            if self._is_protected_qualifier(source_record):
                mapped_any = True
                continue

            target = self._target_for_source_reference(
                source_field=source_field,
                source_record=source_record,
                allow_fallback=False,
            )

            if target:
                mapped_any = True
                break

        if mapped_any:
            return []

        for match in matches:
            source_field = match.group("field")
            source_record = match.group("record")

            if self._looks_like_dclgen_record(source_record):
                continue

            if self._is_protected_qualifier(source_record):
                continue

            self.rewrite_messages.append(
                "Unconverted IDMS qualified reference because Sheet Mapping "
                f"entry does not exist: field={source_field}, record={source_record}"
            )

            return [
                "* DB2: Unconverted IDMS reference.",
                f"* DB2: Sheet Mapping entry does not exist for record {source_record}.",
                "CONTINUE.",
            ]

        return []

    def _record_name_candidates(
        self,
        *values: str,
    ) -> list[str]:
        output: list[str] = []

        for value in values:
            text = str(value or "").strip()

            if not text:
                continue

            normalized = self._normalize_name(text)

            if normalized:
                output.append(normalized)

            cobol = NameNormalizer.to_cobol(normalized)

            if cobol and cobol not in output:
                output.append(cobol)

            compact = self._compact_name(normalized)

            if compact and compact not in output:
                output.append(compact)

        return self._unique(output)

    def _field_name_candidates(
        self,
        *values: str,
    ) -> list[str]:
        output: list[str] = []

        for value in values:
            text = str(value or "").strip()

            if not text:
                continue

            extracted = self._extract_cobol_field_name(text)

            for candidate in [text, extracted]:
                normalized = self._normalize_name(candidate)

                if normalized and normalized not in output:
                    output.append(normalized)

                cobol = NameNormalizer.to_cobol(normalized)

                if cobol and cobol not in output:
                    output.append(cobol)

                compact = self._compact_name(normalized)

                if compact and compact not in output:
                    output.append(compact)

        return self._unique(output)

    def _extract_cobol_field_name(
        self,
        value: str,
    ) -> str:
        text = str(value or "").strip()

        if not text:
            return ""

        text = text.replace(".", " ")
        text = re.sub(r"\s+", " ", text).strip()

        redefines_match = self.REDEFINES_PATTERN.search(text)

        if redefines_match:
            return redefines_match.group("alias")

        level_match = self.COBOL_LEVEL_AND_NAME_PATTERN.match(text)

        if level_match:
            return level_match.group("name")

        tokens = re.findall(
            r"[A-Z][A-Z0-9-]*",
            text,
            flags=re.IGNORECASE,
        )

        if not tokens:
            return ""

        ignore_tokens = {
            "PIC",
            "COMP",
            "COMP-3",
            "VALUE",
            "OCCURS",
            "REDEFINES",
            "REDFINES",
            "FILLER",
            "GROUP",
        }

        for token in tokens:
            if token.upper() not in ignore_tokens:
                return token

        return tokens[0]

    def _record_context_from_paragraph(
        self,
        paragraph_name: str,
    ) -> str:
        text = str(paragraph_name or "").strip().upper()

        match = self.RECORD_CONTEXT_PARAGRAPH_PATTERN.match(
            text + ".",
        )

        if match:
            record = self._normalize_name(match.group("record"))

            if self._record_is_known(record):
                return NameNormalizer.to_cobol(record)

            return NameNormalizer.to_cobol(record)

        return ""

    def _infer_record_context_from_paragraph(
        self,
        paragraph_name: str,
    ) -> str:
        paragraph = self._normalize_name(paragraph_name)

        if not paragraph:
            return ""

        best_record = ""
        best_score = 0

        for record_key in self.known_source_record_keys:
            record_aliases = self._semantic_record_aliases(record_key)
            paragraph_aliases = self._semantic_record_aliases(paragraph)

            score = self._record_table_match_score(
                record_aliases,
                paragraph_aliases,
            )

            if score > best_score:
                best_score = score
                best_record = record_key

        if best_score >= 80 and best_record:
            return NameNormalizer.to_cobol(best_record)

        return ""

    def _record_is_known(
        self,
        record: str,
    ) -> bool:
        for key in self._name_match_keys(record):
            if key in self.known_source_record_keys:
                return True

        return False

    def _looks_like_dclgen_record(
        self,
        value: str,
    ) -> bool:
        normalized = self._normalize_name(value)

        if normalized.startswith("DCL"):
            return True

        if normalized in self.dclgen_group_lookup.values():
            return True

        if normalized in self.group_to_record_lookup:
            return True

        return False

    def _is_protected_qualifier(
        self,
        value: str,
    ) -> bool:
        normalized = self._normalize_name(value)
        cobol = NameNormalizer.to_cobol(normalized)

        if normalized in self.PROTECTED_QUALIFIERS:
            return True

        if cobol in self.PROTECTED_QUALIFIERS:
            return True

        for prefix in self.PROTECTED_QUALIFIER_PREFIXES:
            normalized_prefix = self._normalize_name(prefix)

            if normalized.startswith(normalized_prefix):
                return True

            if cobol.startswith(prefix):
                return True

        return False

    def _safe_bare_identifier(
        self,
        value: str,
    ) -> bool:
        text = str(value or "").strip()

        if not text:
            return False

        if not self.BARE_IDENTIFIER_PATTERN.match(text):
            return False

        upper = text.upper()

        if upper in self.UNSAFE_WORDS:
            return False

        if upper.startswith("SQL"):
            return False

        if upper.startswith("DCL"):
            return False

        if upper.endswith("-R"):
            return False

        return True

    def _must_skip_line(
        self,
        line: str,
        logical: str,
        current_division: str,
    ) -> bool:
        stripped = str(logical or "").strip()

        if not stripped:
            return True

        if self._is_comment_line(line):
            return True

        if current_division != "PROCEDURE":
            return True

        if self.DATA_LEVEL_PATTERN.match(stripped):
            return True

        if stripped.upper().startswith("COPY "):
            return True

        for pattern in self.HEADER_PATTERNS:
            if pattern.match(stripped):
                return True

        upper = stripped.upper()

        if "SQL-LOCATION" in upper:
            return True

        return False

    def _is_comment_line(
        self,
        line: str,
    ) -> bool:
        text = str(line or "")

        if not text.strip():
            return False

        stripped = text.lstrip()

        if stripped.startswith("*") or stripped.startswith("/"):
            return True

        if len(text) > 6 and text[6:7] in ("*", "/"):
            return True

        return False

    def _division_from_line(
        self,
        logical: str,
        current_division: str,
    ) -> str:
        upper = str(logical or "").strip().upper()

        if upper.startswith("IDENTIFICATION DIVISION"):
            return "IDENTIFICATION"

        if upper.startswith("ENVIRONMENT DIVISION"):
            return "ENVIRONMENT"

        if upper.startswith("DATA DIVISION"):
            return "DATA"

        if upper.startswith("PROCEDURE DIVISION"):
            return "PROCEDURE"

        return current_division

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

    def _split_string_segments(
        self,
        line: str,
    ) -> list[tuple[str, bool]]:
        output: list[tuple[str, bool]] = []
        buffer = ""
        in_string = False
        quote_char = ""
        index = 0

        while index < len(line):
            char = line[index]

            if not in_string and char in ("'", '"'):
                if buffer:
                    output.append((buffer, False))
                    buffer = ""

                in_string = True
                quote_char = char
                buffer += char
                index += 1
                continue

            if in_string:
                buffer += char

                if char == quote_char:
                    if index + 1 < len(line) and line[index + 1] == quote_char:
                        buffer += line[index + 1]
                        index += 2
                        continue

                    output.append((buffer, True))
                    buffer = ""
                    in_string = False
                    quote_char = ""

                index += 1
                continue

            buffer += char
            index += 1

        if buffer:
            output.append((buffer, in_string))

        return output

    def _name_match_keys(
        self,
        value: str,
    ) -> list[str]:
        normalized = self._normalize_name(value)
        output: list[str] = []

        if normalized:
            output.append(normalized)

        cobol = NameNormalizer.to_cobol(normalized)

        if cobol:
            output.append(cobol)

        compact = self._compact_name(normalized)

        if compact:
            output.append(compact)

        no_suffix = self._remove_record_suffix(normalized)

        if no_suffix and no_suffix != normalized:
            output.append(no_suffix)
            output.append(NameNormalizer.to_cobol(no_suffix))
            output.append(self._compact_name(no_suffix))

        return self._unique(output)

    def _table_candidates(
        self,
        table: str,
    ) -> list[str]:
        normalized = self._normalize_name(table)
        output: list[str] = []

        if not normalized:
            return output

        output.append(normalized)

        if normalized.endswith("_TB"):
            output.append(normalized[:-3] + "_TV")

        if normalized.endswith("_TV"):
            output.append(normalized[:-3] + "_TB")

        if normalized.endswith("TB"):
            output.append(normalized[:-2] + "TV")

        if normalized.endswith("TV"):
            output.append(normalized[:-2] + "TB")

        output.append(NameNormalizer.to_cobol(normalized))
        output.append(self._compact_name(normalized))

        return self._unique(output)

    def _semantic_record_aliases(
        self,
        record: str,
    ) -> list[str]:
        normalized = self._normalize_name(record)
        compact = self._compact_name(normalized)
        no_suffix = self._remove_record_suffix(normalized)
        no_suffix_compact = self._compact_name(no_suffix)

        aliases = [
            normalized,
            compact,
            no_suffix,
            no_suffix_compact,
        ]

        if compact.startswith("VM"):
            aliases.append(compact[2:])

        if compact.startswith("VMB"):
            aliases.append(compact[3:])

        return self._unique([item for item in aliases if item])

    def _semantic_table_aliases(
        self,
        table: str,
    ) -> list[str]:
        normalized = self._normalize_name(table)
        compact = self._compact_name(normalized)

        aliases = [
            normalized,
            compact,
        ]

        table_core = compact

        for prefix in ["DCL", "DZ", "NK"]:
            if table_core.startswith(prefix):
                aliases.append(table_core[len(prefix):])

        for suffix in ["TB", "TV"]:
            if table_core.endswith(suffix):
                aliases.append(table_core[:-len(suffix)])

        if table_core.startswith("DZ") and table_core.endswith(("TB", "TV")):
            aliases.append(table_core[2:-2])

        return self._unique([item for item in aliases if item])

    def _record_table_match_score(
        self,
        record_aliases: list[str],
        table_aliases: list[str],
    ) -> int:
        record_set = {
            self._compact_name(item)
            for item in record_aliases
            if item
        }

        table_set = {
            self._compact_name(item)
            for item in table_aliases
            if item
        }

        if not record_set or not table_set:
            return 0

        best = 0

        for record in record_set:
            for table in table_set:
                if not record or not table:
                    continue

                if record == table:
                    best = max(best, 100)
                    continue

                if record in table or table in record:
                    best = max(best, 85)
                    continue

                ratio = int(
                    SequenceMatcher(None, record, table).ratio() * 100,
                )

                best = max(best, ratio)

        return best

    def _meaningful_tokens(
        self,
        value: str,
    ) -> list[str]:
        normalized = self._normalize_name(value)
        tokens = re.split(r"[_\-]+", normalized)

        ignored = {
            "",
            "OF",
            "IN",
            "THE",
            "A",
            "AN",
            "R",
            "R1",
            "R2",
            "R3",
            "V",
            "VM",
            "VMB",
            "DZ",
            "TB",
            "TV",
            "DCL",
        }

        output = [
            token
            for token in tokens
            if token and token not in ignored and not token.isdigit()
        ]

        return self._unique(output)

    def _host_core_compact(
        self,
        value: str,
    ) -> str:
        normalized = self._normalize_name(value)

        normalized = re.sub(
            r"_[0-9]{3}[A-Z0-9]*$",
            "",
            normalized,
            flags=re.IGNORECASE,
        )

        normalized = re.sub(
            r"-[0-9]{3}[A-Z0-9]*$",
            "",
            normalized,
            flags=re.IGNORECASE,
        )

        return self._compact_name(normalized)

    def _normalize_name(
        self,
        value: str,
    ) -> str:
        return NameNormalizer.normalize(value)

    def _compact_name(
        self,
        value: str,
    ) -> str:
        normalized = NameNormalizer.normalize(value)

        return re.sub(
            r"[^A-Z0-9]+",
            "",
            normalized,
        )

    def _remove_record_suffix(
        self,
        value: str,
    ) -> str:
        normalized = self._normalize_name(value)

        if not normalized:
            return ""

        no_suffix = NameNormalizer.remove_record_suffix(normalized)

        if no_suffix != normalized:
            return no_suffix

        compact = self._compact_name(normalized)

        compact = re.sub(
            r"[RV][0-9]+$",
            "",
            compact,
            flags=re.IGNORECASE,
        )

        return compact

    def _first_non_empty(
        self,
        *values: str,
    ) -> str:
        for value in values:
            text = str(value or "").strip()

            if text:
                return text

        return ""

    def _unique(
        self,
        values: list[str],
    ) -> list[str]:
        output: list[str] = []

        for value in values:
            text = str(value or "").strip()

            if not text:
                continue

            if text not in output:
                output.append(text)

        return output