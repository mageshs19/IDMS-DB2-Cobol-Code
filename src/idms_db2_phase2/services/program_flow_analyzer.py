from __future__ import annotations

from dataclasses import dataclass, field
import re

from idms_db2_phase2.domain.models import DclgenColumn, IdmsOperation, SheetMappingRow
from idms_db2_phase2.services.name_normalizer import NameNormalizer


@dataclass
class ParagraphSpan:
    name: str
    start_line: int
    end_line: int
    lines: list[str] = field(default_factory=list)


@dataclass
class CursorLoop:
    record_name: str = ""
    set_name: str = ""
    operation: str = ""
    operation_line: int = 0
    process_paragraph: str = ""
    perform_line: int = 0
    until_line: int = 0
    loop_type: str = "unknown"
    cursor_name: str = ""
    open_paragraph: str = ""
    fetch_paragraph: str = ""
    close_paragraph: str = ""


@dataclass
class OutputWrite:
    output_record: str = ""
    paragraph_name: str = ""
    write_line: int = 0
    move_lines: list[str] = field(default_factory=list)


@dataclass
class DateUsage:
    host_field: str = ""
    dclgen_group: str = ""
    idms_record: str = ""
    line_number: int = 0
    line_text: str = ""
    usage_type: str = ""


@dataclass
class ProgramFlowAnalysis:
    paragraphs: list[ParagraphSpan] = field(default_factory=list)
    cursor_loops: list[CursorLoop] = field(default_factory=list)
    output_writes: list[OutputWrite] = field(default_factory=list)
    date_usages: list[DateUsage] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)

    def process_paragraph_for_cursor(
        self,
        cursor_name: str,
    ) -> str:
        cursor = NameNormalizer.to_cobol(
            NameNormalizer.normalize(cursor_name),
        )

        for loop in self.cursor_loops:
            if NameNormalizer.to_cobol(NameNormalizer.normalize(loop.cursor_name)) == cursor:
                return loop.process_paragraph

        return ""

    def loop_for_set(
        self,
        set_name: str,
    ) -> CursorLoop | None:
        target = NameNormalizer.normalize(
            set_name,
        )

        for loop in self.cursor_loops:
            if NameNormalizer.normalize(loop.set_name) == target:
                return loop

        return None

    def loop_for_record(
        self,
        record_name: str,
    ) -> CursorLoop | None:
        target = NameNormalizer.normalize(
            record_name,
        )

        for loop in self.cursor_loops:
            if NameNormalizer.normalize(loop.record_name) == target:
                return loop

        return None


class ProgramFlowAnalyzer:
    """
    Generic flow analyzer for IDMS-to-DB2 conversion.

    This analyzer does not rewrite COBOL. It detects reusable metadata:
    - procedure paragraphs
    - cursor loops
    - loop process paragraphs
    - output writes
    - date usages

    It must not hardcode program names, record names, table names, or paragraph
    names.
    """

    LEFT_SEQUENCE_PATTERN = re.compile(
        r"^\s*(?P<seq>\d{6})(?P<body>\s+.*)$",
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

    PROCEDURE_DIVISION_PATTERN = re.compile(
        r"^\s*PROCEDURE\s+DIVISION\b.*\.?\s*$",
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

    PERFORM_PATTERN = re.compile(
        r"^\s*PERFORM\s+(?P<paragraph>[A-Z0-9][A-Z0-9-]*)\s*\.?\s*$",
        flags=re.IGNORECASE,
    )

    PERFORM_UNTIL_PATTERN = re.compile(
        r"^\s*PERFORM\s+(?P<paragraph>[A-Z0-9][A-Z0-9-]*)\s+"
        r"UNTIL\s+(?P<condition>.+?)\.?\s*$",
        flags=re.IGNORECASE,
    )

    UNTIL_PATTERN = re.compile(
        r"^\s*UNTIL\s+(?P<condition>.+?)\.?\s*$",
        flags=re.IGNORECASE,
    )

    DB_END_OF_SET_PATTERN = re.compile(
        r"\bDB-END-OF-SET\b",
        flags=re.IGNORECASE,
    )

    CURSOR_EOC_PATTERN = re.compile(
        r"\b(?P<cursor>[A-Z0-9-]+)-EOC\b",
        flags=re.IGNORECASE,
    )

    WRITE_PATTERN = re.compile(
        r"^\s*WRITE\s+(?P<record>[A-Z][A-Z0-9-]*)\b",
        flags=re.IGNORECASE,
    )

    MOVE_TO_PATTERN = re.compile(
        r"^\s*MOVE\s+.+?\s+TO\s+(?P<target>[A-Z][A-Z0-9-]*)\b",
        flags=re.IGNORECASE,
    )

    DCLGEN_HOST_REFERENCE_PATTERN = re.compile(
        r"\b(?P<field>[A-Z][A-Z0-9-]*)\s+OF\s+(?P<group>DCL[A-Z0-9-]+)\b",
        flags=re.IGNORECASE,
    )

    IDMS_QUALIFIED_REFERENCE_PATTERN = re.compile(
        r"\b(?P<field>[A-Z][A-Z0-9-]*)\s+(?:OF|IN)\s+"
        r"(?P<record>[A-Z][A-Z0-9-]*)\b",
        flags=re.IGNORECASE,
    )

    SQL_HOST_REFERENCE_PATTERN = re.compile(
        r":\s*(?P<group>DCL[A-Z0-9-]+)\s*\.\s*(?P<field>[A-Z][A-Z0-9-]*)",
        flags=re.IGNORECASE,
    )

    IF_OR_EVALUATE_PATTERN = re.compile(
        r"^\s*(IF|WHEN|EVALUATE)\b",
        flags=re.IGNORECASE,
    )

    IGNORE_TOKENS = {
        "PIC",
        "REDEFINES",
        "OCCURS",
        "VALUE",
        "COMP",
        "COMP_3",
        "GROUP",
        "FILLER",
        "COPY",
        "SECTION",
        "DIVISION",
    }

    def __init__(
        self,
        mapping_rows: list[SheetMappingRow] | None = None,
        dclgen_columns: list[DclgenColumn] | None = None,
    ) -> None:
        self.mapping_rows = mapping_rows or []
        self.dclgen_columns = dclgen_columns or []
        self.diagnostics: list[str] = []

        self.date_host_lookup = self._build_date_host_lookup(
            self.dclgen_columns,
        )

        self.record_to_table_lookup = self._build_record_to_table_lookup(
            self.mapping_rows,
        )

        self.table_catalog = self._build_table_catalog(
            self.mapping_rows,
        )

        self.idms_date_field_lookup = self._build_idms_date_field_lookup(
            self.mapping_rows,
        )

    def analyze(
        self,
        cobol_text: str,
        operations: list[IdmsOperation] | None = None,
    ) -> ProgramFlowAnalysis:
        self.diagnostics = []

        if not cobol_text or not cobol_text.strip():
            return ProgramFlowAnalysis(
                diagnostics=["Program flow analyzer: COBOL text is empty."],
            )

        operations = operations or []

        logical_lines = self._logical_lines_with_numbers(
            cobol_text,
        )

        paragraphs = self._paragraph_spans(
            logical_lines,
        )

        cursor_loops = self._cursor_loops(
            logical_lines=logical_lines,
            operations=operations,
        )

        output_writes = self._output_writes(
            logical_lines=logical_lines,
            paragraphs=paragraphs,
        )

        date_usages = self._date_usages(
            logical_lines=logical_lines,
        )

        self.diagnostics.append(
            f"Program flow analyzer: Sheet Mapping rows received: {len(self.mapping_rows)}",
        )
        self.diagnostics.append(
            f"Program flow analyzer: record-to-table lookup entries: {len(self.record_to_table_lookup)}",
        )
        self.diagnostics.append(
            f"Program flow analyzer: table catalog entries: {len(self.table_catalog)}",
        )
        self.diagnostics.append(
            f"Program flow analyzer: paragraphs detected: {len(paragraphs)}",
        )
        self.diagnostics.append(
            f"Program flow analyzer: cursor loops detected: {len(cursor_loops)}",
        )
        self.diagnostics.append(
            f"Program flow analyzer: output writes detected: {len(output_writes)}",
        )
        self.diagnostics.append(
            f"Program flow analyzer: DB2 date usages detected: {len(date_usages)}",
        )

        for loop in cursor_loops[:5]:
            self.diagnostics.append(
                "Program flow analyzer: loop sample: "
                f"record={loop.record_name}, set={loop.set_name}, "
                f"type={loop.loop_type}, process={loop.process_paragraph}, "
                f"cursor={loop.cursor_name}",
            )

        return ProgramFlowAnalysis(
            paragraphs=paragraphs,
            cursor_loops=cursor_loops,
            output_writes=output_writes,
            date_usages=date_usages,
            diagnostics=list(self.diagnostics),
        )

    #
    # Logical source lines
    #
    def _logical_lines_with_numbers(
        self,
        cobol_text: str,
    ) -> list[tuple[int, str, str]]:
        output: list[tuple[int, str, str]] = []

        for line_number, raw_line in enumerate(cobol_text.splitlines(), start=1):
            logical = self._logical_line(
                raw_line,
            )

            output.append(
                (
                    line_number,
                    logical,
                    raw_line.rstrip(),
                )
            )

        return output

    def _logical_line(
        self,
        line: str,
    ) -> str:
        text = str(line or "").rstrip()

        if self.SEQUENCE_ONLY_PATTERN.fullmatch(
            text,
        ):
            return ""

        while True:
            right_match = self.RIGHT_SEQUENCE_PATTERN.match(
                text,
            )

            if right_match and right_match.group("right"):
                text = right_match.group("body").rstrip()
                continue

            left_match = self.LEFT_SEQUENCE_PATTERN.match(
                text,
            )

            if left_match:
                text = left_match.group("body").strip()
                continue

            break

        return text.strip()

    #
    # Paragraphs
    #
    def _paragraph_spans(
        self,
        logical_lines: list[tuple[int, str, str]],
    ) -> list[ParagraphSpan]:
        procedure_start_index = self._procedure_start_index(
            logical_lines,
        )

        if procedure_start_index < 0:
            return []

        paragraph_starts: list[tuple[int, int, str]] = []

        for index in range(procedure_start_index + 1, len(logical_lines)):
            line_number, logical, _raw = logical_lines[index]
            paragraph_name = self._paragraph_name(
                logical,
            )

            if paragraph_name:
                paragraph_starts.append(
                    (
                        index,
                        line_number,
                        paragraph_name,
                    )
                )

        paragraphs: list[ParagraphSpan] = []

        for start_position, item in enumerate(paragraph_starts):
            start_index, start_line, paragraph_name = item

            if start_position + 1 < len(paragraph_starts):
                end_index = paragraph_starts[start_position + 1][0] - 1
            else:
                end_index = len(logical_lines) - 1

            end_line = logical_lines[end_index][0]

            paragraph_lines = [
                logical_lines[index][1]
                for index in range(start_index, end_index + 1)
            ]

            paragraphs.append(
                ParagraphSpan(
                    name=paragraph_name,
                    start_line=start_line,
                    end_line=end_line,
                    lines=paragraph_lines,
                )
            )

        return paragraphs

    def _procedure_start_index(
        self,
        logical_lines: list[tuple[int, str, str]],
    ) -> int:
        for index, (_line_number, logical, _raw) in enumerate(logical_lines):
            if self.PROCEDURE_DIVISION_PATTERN.match(
                logical,
            ):
                return index

        return -1

    def _paragraph_name(
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

        return name

    #
    # Cursor loop analysis
    #
    def _cursor_loops(
        self,
        logical_lines: list[tuple[int, str, str]],
        operations: list[IdmsOperation],
    ) -> list[CursorLoop]:
        perform_loops = self._perform_until_loops(
            logical_lines,
        )

        cursor_loops: list[CursorLoop] = []

        cursor_operations = [
            operation
            for operation in operations
            if str(operation.operation or "").upper()
            in {
                "OBTAIN_FIRST",
                "OBTAIN_NEXT",
                "FIND_FIRST",
            }
            and str(operation.set_name or "").strip()
        ]

        for operation in cursor_operations:
            loop = self._loop_for_operation(
                operation=operation,
                perform_loops=perform_loops,
            )

            if loop:
                cursor_loops.append(
                    loop,
                )

        return self._dedupe_cursor_loops(
            cursor_loops,
        )

    def _perform_until_loops(
        self,
        logical_lines: list[tuple[int, str, str]],
    ) -> list[dict[str, object]]:
        loops: list[dict[str, object]] = []

        previous_perform: dict[str, object] | None = None

        for line_number, logical, raw_line in logical_lines:
            if not logical:
                continue

            inline_match = self.PERFORM_UNTIL_PATTERN.match(
                logical,
            )

            if inline_match:
                loops.append(
                    {
                        "paragraph": inline_match.group("paragraph").upper(),
                        "perform_line": line_number,
                        "until_line": line_number,
                        "condition": inline_match.group("condition").strip(),
                        "raw_line": raw_line,
                    }
                )
                previous_perform = None
                continue

            perform_match = self.PERFORM_PATTERN.match(
                logical,
            )

            if perform_match:
                previous_perform = {
                    "paragraph": perform_match.group("paragraph").upper(),
                    "perform_line": line_number,
                    "raw_line": raw_line,
                }
                continue

            until_match = self.UNTIL_PATTERN.match(
                logical,
            )

            if until_match and previous_perform:
                loops.append(
                    {
                        "paragraph": str(previous_perform["paragraph"]),
                        "perform_line": int(previous_perform["perform_line"]),
                        "until_line": line_number,
                        "condition": until_match.group("condition").strip(),
                        "raw_line": str(previous_perform["raw_line"]),
                    }
                )
                previous_perform = None
                continue

            if logical.startswith("*"):
                continue

            if not self._is_continuation_like_line(logical):
                previous_perform = None

        return loops

    def _loop_for_operation(
        self,
        operation: IdmsOperation,
        perform_loops: list[dict[str, object]],
    ) -> CursorLoop | None:
        operation_line = int(operation.line_number or 0)
        set_name = NameNormalizer.to_cobol(
            NameNormalizer.normalize(operation.set_name),
        )
        record_name = NameNormalizer.to_cobol(
            NameNormalizer.normalize(operation.record_name),
        )
        operation_name = str(operation.operation or "").upper()

        if not set_name:
            return None

        candidate = self._nearest_perform_loop_after_operation(
            operation_line=operation_line,
            perform_loops=perform_loops,
        )

        if not candidate:
            return None

        condition = str(candidate.get("condition", ""))

        if not self._is_eoc_condition(
            condition,
        ):
            return None

        process_paragraph = str(candidate.get("paragraph", "")).upper()
        perform_line = int(candidate.get("perform_line", 0) or 0)
        until_line = int(candidate.get("until_line", 0) or 0)

        loop_type = self._loop_type_for_set(
            set_name,
        )

        table_name = self._best_table_for_record(
            record_name,
        )

        cursor_name = self._cursor_name_from_table_or_fallback(
            table_name=table_name,
            set_name=set_name,
            record_name=record_name,
        )

        if table_name:
            self.diagnostics.append(
                "Program flow analyzer: record-to-table resolved: "
                f"{record_name} -> {table_name} -> {cursor_name}",
            )
        else:
            self.diagnostics.append(
                "Program flow analyzer: record-to-table unresolved, using fallback: "
                f"record={record_name}, set={set_name}, cursor={cursor_name}",
            )

        return CursorLoop(
            record_name=record_name,
            set_name=set_name,
            operation=operation_name,
            operation_line=operation_line,
            process_paragraph=process_paragraph,
            perform_line=perform_line,
            until_line=until_line,
            loop_type=loop_type,
            cursor_name=cursor_name,
            open_paragraph="",
            fetch_paragraph="",
            close_paragraph="",
        )

    def _nearest_perform_loop_after_operation(
        self,
        operation_line: int,
        perform_loops: list[dict[str, object]],
    ) -> dict[str, object] | None:
        if operation_line <= 0:
            return None

        candidates = [
            loop
            for loop in perform_loops
            if int(loop.get("perform_line", 0) or 0) >= operation_line
        ]

        if not candidates:
            return None

        candidates.sort(
            key=lambda item: int(item.get("perform_line", 0) or 0),
        )

        return candidates[0]

    def _is_eoc_condition(
        self,
        condition: str,
    ) -> bool:
        text = str(condition or "").upper()

        if self.DB_END_OF_SET_PATTERN.search(
            text,
        ):
            return True

        if self.CURSOR_EOC_PATTERN.search(
            text,
        ):
            return True

        return False

    def _loop_type_for_set(
        self,
        set_name: str,
    ) -> str:
        normalized = NameNormalizer.normalize(
            set_name,
        )

        if not normalized:
            return "unknown"

        parts = [
            part
            for part in normalized.split("_")
            if part
        ]

        if not parts:
            return "unknown"

        if parts[0] in {
            "AR",
            "AREA",
            "IX",
            "INDEX",
        }:
            return "root"

        if len(parts) >= 2:
            return "child"

        return "unknown"

    #
    # Record to DB2 table resolution
    #
    def _build_record_to_table_lookup(
        self,
        mapping_rows: list[SheetMappingRow],
    ) -> dict[str, str]:
        output: dict[str, str] = {}

        for row in mapping_rows or []:
            table = self._first_non_empty(
                row.new_db2_record,
                row.cross_application_db2_table,
            )

            if not table:
                continue

            normalized_table = NameNormalizer.normalize(
                table,
            )

            record_candidates = self._record_name_candidates(
                row.cobol_record_idms,
            )

            for candidate in record_candidates:
                if candidate and candidate not in output:
                    output[candidate] = normalized_table

        return output

    def _build_table_catalog(
        self,
        mapping_rows: list[SheetMappingRow],
    ) -> list[str]:
        output: list[str] = []

        for row in mapping_rows or []:
            table = self._first_non_empty(
                row.new_db2_record,
                row.cross_application_db2_table,
            )

            if not table:
                continue

            normalized = NameNormalizer.normalize(
                table,
            )

            if normalized and normalized not in output:
                output.append(
                    normalized,
                )

        return output

    def _best_table_for_record(
        self,
        record_name: str,
    ) -> str:
        record_candidates = self._record_name_candidates(
            record_name,
        )

        for candidate in record_candidates:
            table = self.record_to_table_lookup.get(
                candidate,
                "",
            )

            if table:
                return NameNormalizer.to_cobol(
                    table,
                )

        compact_candidates = {
            self._compact_name(candidate)
            for candidate in record_candidates
            if candidate
        }

        for key, table in self.record_to_table_lookup.items():
            if key in record_candidates:
                return NameNormalizer.to_cobol(
                    table,
                )

            if self._compact_name(key) in compact_candidates:
                return NameNormalizer.to_cobol(
                    table,
                )

        dynamic_table = self._best_table_for_record_by_table_suffix(
            record_name,
        )

        if dynamic_table:
            return dynamic_table

        return ""

    def _best_table_for_record_by_table_suffix(
        self,
        record_name: str,
    ) -> str:
        record_aliases = self._semantic_record_aliases(
            record_name,
        )

        best_table = ""
        best_score = 0

        for table in self.table_catalog:
            table_aliases = self._semantic_table_aliases(
                table,
            )

            score = self._record_table_match_score(
                record_aliases=record_aliases,
                table_aliases=table_aliases,
            )

            if score > best_score:
                best_score = score
                best_table = table

        if best_score >= 70 and best_table:
            self.diagnostics.append(
                "Program flow analyzer: suffix table match used: "
                f"record={record_name}, table={NameNormalizer.to_cobol(best_table)}, score={best_score}",
            )
            return NameNormalizer.to_cobol(
                best_table,
            )

        return ""

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

        if record_set.intersection(table_set):
            return 100

        for record in record_set:
            for table in table_set:
                if not record or not table:
                    continue

                if record == table:
                    return 100

                if len(record) >= 4 and record in table:
                    return 90

                if len(table) >= 4 and table in record:
                    return 85

        return 0

    def _semantic_record_aliases(
        self,
        record_name: str,
    ) -> list[str]:
        aliases = self._record_name_candidates(
            record_name,
        )

        normalized = NameNormalizer.normalize(
            record_name,
        )

        compact = self._compact_name(
            normalized,
        )

        if compact:
            aliases.append(
                compact,
            )

        for prefix in [
            "VMB",
            "VM",
            "IDMS",
        ]:
            if compact.startswith(prefix) and len(compact) > len(prefix):
                aliases.append(
                    compact[len(prefix):],
                )

        return self._unique_non_empty(
            aliases,
        )

    def _semantic_table_aliases(
        self,
        table_name: str,
    ) -> list[str]:
        aliases: list[str] = []

        normalized = NameNormalizer.normalize(
            table_name,
        )

        if normalized:
            aliases.append(
                normalized,
            )

        compact = self._compact_name(
            normalized,
        )

        if compact:
            aliases.append(
                compact,
            )

        core = compact

        for prefix in [
            "DZ",
            "DB2",
        ]:
            if core.startswith(prefix) and len(core) > len(prefix):
                core = core[len(prefix):]

        for suffix in [
            "TV",
            "TB",
            "VIEW",
            "TABLE",
        ]:
            if core.endswith(suffix) and len(core) > len(suffix):
                core = core[: -len(suffix)]

        if core:
            aliases.append(
                core,
            )

        return self._unique_non_empty(
            aliases,
        )

    def _record_name_candidates(
        self,
        *values: str,
    ) -> list[str]:
        candidates: list[str] = []

        for value in values:
            text = str(value or "").strip()

            if not text:
                continue

            normalized = NameNormalizer.normalize(
                text,
            )

            if normalized:
                candidates.append(
                    normalized,
                )

            tokens = re.findall(
                r"[A-Z][A-Z0-9-]*",
                text.upper(),
            )

            for token in tokens:
                token_normalized = NameNormalizer.normalize(
                    token,
                )

                if not token_normalized:
                    continue

                if token_normalized in self.IGNORE_TOKENS:
                    continue

                candidates.append(
                    token_normalized,
                )

                no_suffix = NameNormalizer.remove_record_suffix(
                    token_normalized,
                )

                if no_suffix:
                    candidates.append(
                        no_suffix,
                    )

            if normalized:
                no_suffix = NameNormalizer.remove_record_suffix(
                    normalized,
                )

                if no_suffix:
                    candidates.append(
                        no_suffix,
                    )

        expanded: list[str] = []

        for candidate in candidates:
            if not candidate:
                continue

            expanded.append(
                candidate,
            )

            compact = self._compact_name(
                candidate,
            )

            if compact:
                expanded.append(
                    compact,
                )

        return self._unique_non_empty(
            expanded,
        )

    def _cursor_name_from_table_or_fallback(
        self,
        table_name: str,
        set_name: str,
        record_name: str,
    ) -> str:
        if table_name:
            return self._cursor_name_from_db2_record(
                table_name,
            )

        normalized_set = NameNormalizer.normalize(
            set_name,
        )

        if normalized_set:
            return self._cursor_name_from_db2_record(
                normalized_set,
            )

        normalized_record = NameNormalizer.normalize(
            record_name,
        )

        if normalized_record:
            return self._cursor_name_from_db2_record(
                normalized_record,
            )

        return "DB2CURC1"

    def _cursor_name_from_db2_record(
        self,
        table_name: str,
    ) -> str:
        table = NameNormalizer.normalize(
            table_name,
        )

        if not table:
            return "DB2CURC1"

        if table.endswith("_TV") or table.endswith("_TB"):
            return NameNormalizer.to_cobol(
                table[:-3] + "_C1",
            )

        if table.endswith("TV") or table.endswith("TB"):
            return NameNormalizer.to_cobol(
                table[:-2] + "C1",
            )

        return NameNormalizer.to_cobol(
            table + "_C1",
        )

    def _dedupe_cursor_loops(
        self,
        loops: list[CursorLoop],
    ) -> list[CursorLoop]:
        output: list[CursorLoop] = []
        seen: set[tuple[str, str, str]] = set()

        for loop in loops:
            key = (
                NameNormalizer.normalize(loop.record_name),
                NameNormalizer.normalize(loop.set_name),
                NameNormalizer.normalize(loop.process_paragraph),
            )

            if key in seen:
                continue

            seen.add(
                key,
            )
            output.append(
                loop,
            )

        return self._assign_cursor_paragraph_names(
            output,
        )

    def _assign_cursor_paragraph_names(
        self,
        loops: list[CursorLoop],
    ) -> list[CursorLoop]:
        cursor_order: dict[str, int] = {}

        for loop in loops:
            cursor = NameNormalizer.to_cobol(
                NameNormalizer.normalize(loop.cursor_name),
            )

            if cursor not in cursor_order:
                cursor_order[cursor] = len(cursor_order)

            base = 710 + (cursor_order[cursor] * 100)

            loop.open_paragraph = f"{base}-OPEN-{cursor}"
            loop.fetch_paragraph = f"{base + 10}-FETCH-{cursor}"
            loop.close_paragraph = f"{base + 20}-CLOSE-{cursor}"

        return loops

    #
    # Output write analysis
    #
    def _output_writes(
        self,
        logical_lines: list[tuple[int, str, str]],
        paragraphs: list[ParagraphSpan],
    ) -> list[OutputWrite]:
        paragraph_by_line = self._paragraph_name_by_line(
            paragraphs,
        )

        output: list[OutputWrite] = []

        for index, (line_number, logical, _raw_line) in enumerate(logical_lines):
            write_match = self.WRITE_PATTERN.match(
                logical,
            )

            if not write_match:
                continue

            output_record = NameNormalizer.to_cobol(
                NameNormalizer.normalize(write_match.group("record")),
            )

            paragraph_name = paragraph_by_line.get(
                line_number,
                "",
            )

            move_lines = self._preceding_move_lines_for_write(
                logical_lines=logical_lines,
                write_index=index,
            )

            output.append(
                OutputWrite(
                    output_record=output_record,
                    paragraph_name=paragraph_name,
                    write_line=line_number,
                    move_lines=move_lines,
                )
            )

        return output

    def _paragraph_name_by_line(
        self,
        paragraphs: list[ParagraphSpan],
    ) -> dict[int, str]:
        output: dict[int, str] = {}

        for paragraph in paragraphs:
            for line_number in range(paragraph.start_line, paragraph.end_line + 1):
                output[line_number] = paragraph.name

        return output

    def _preceding_move_lines_for_write(
        self,
        logical_lines: list[tuple[int, str, str]],
        write_index: int,
    ) -> list[str]:
        output: list[str] = []

        index = write_index - 1

        while index >= 0:
            _line_number, logical, _raw = logical_lines[index]
            stripped = logical.strip()

            if not stripped:
                index -= 1
                continue

            if stripped.startswith("*"):
                index -= 1
                continue

            if self.MOVE_TO_PATTERN.match(
                stripped,
            ):
                output.insert(
                    0,
                    stripped,
                )
                index -= 1
                continue

            if stripped.upper().startswith("INITIALIZE "):
                output.insert(
                    0,
                    stripped,
                )
                index -= 1
                continue

            break

        return output

    #
    # Date usage analysis
    #
    def _date_usages(
        self,
        logical_lines: list[tuple[int, str, str]],
    ) -> list[DateUsage]:
        output: list[DateUsage] = []

        for line_number, logical, _raw in logical_lines:
            if not logical:
                continue

            if logical.startswith("*"):
                continue

            output.extend(
                self._dclgen_date_usages_for_line(
                    line_number=line_number,
                    logical=logical,
                )
            )

            output.extend(
                self._idms_date_usages_for_line(
                    line_number=line_number,
                    logical=logical,
                )
            )

        return self._dedupe_date_usages(
            output,
        )

    def _dclgen_date_usages_for_line(
        self,
        line_number: int,
        logical: str,
    ) -> list[DateUsage]:
        output: list[DateUsage] = []

        host_refs = self._host_references(
            logical,
        )

        if not host_refs:
            return output

        for dclgen_group, host_field in host_refs:
            if not self._is_date_host(
                dclgen_group=dclgen_group,
                host_field=host_field,
            ):
                continue

            usage_type = self._date_usage_type(
                logical,
            )

            if not usage_type:
                continue

            output.append(
                DateUsage(
                    host_field=host_field,
                    dclgen_group=dclgen_group,
                    idms_record="",
                    line_number=line_number,
                    line_text=logical,
                    usage_type=usage_type,
                )
            )

        return output

    def _idms_date_usages_for_line(
        self,
        line_number: int,
        logical: str,
    ) -> list[DateUsage]:
        output: list[DateUsage] = []

        usage_type = self._date_usage_type(
            logical,
        )

        if not usage_type:
            return output

        for match in self.IDMS_QUALIFIED_REFERENCE_PATTERN.finditer(
            logical,
        ):
            field = NameNormalizer.to_cobol(
                NameNormalizer.normalize(match.group("field")),
            )
            record = NameNormalizer.to_cobol(
                NameNormalizer.normalize(match.group("record")),
            )

            if not self._is_idms_date_field(
                record_name=record,
                field_name=field,
            ):
                continue

            output.append(
                DateUsage(
                    host_field=field,
                    dclgen_group="",
                    idms_record=record,
                    line_number=line_number,
                    line_text=logical,
                    usage_type=usage_type,
                )
            )

        return output

    def _host_references(
        self,
        logical: str,
    ) -> list[tuple[str, str]]:
        output: list[tuple[str, str]] = []

        for match in self.DCLGEN_HOST_REFERENCE_PATTERN.finditer(
            logical,
        ):
            output.append(
                (
                    NameNormalizer.to_cobol(
                        NameNormalizer.normalize(match.group("group")),
                    ),
                    NameNormalizer.to_cobol(
                        NameNormalizer.normalize(match.group("field")),
                    ),
                )
            )

        for match in self.SQL_HOST_REFERENCE_PATTERN.finditer(
            logical,
        ):
            output.append(
                (
                    NameNormalizer.to_cobol(
                        NameNormalizer.normalize(match.group("group")),
                    ),
                    NameNormalizer.to_cobol(
                        NameNormalizer.normalize(match.group("field")),
                    ),
                )
            )

        return output

    def _build_date_host_lookup(
        self,
        dclgen_columns: list[DclgenColumn],
    ) -> set[tuple[str, str]]:
        output: set[tuple[str, str]] = set()

        for column in dclgen_columns or []:
            db2_type = str(column.db2_type or "").upper()

            if "DATE" not in db2_type:
                continue

            table = NameNormalizer.normalize(
                column.table_name,
            )
            host = NameNormalizer.normalize(
                column.cobol_host_name,
            )

            if not table or not host:
                continue

            group = "DCL" + table

            output.add(
                (
                    NameNormalizer.to_cobol(group),
                    NameNormalizer.to_cobol(host),
                )
            )

        return output

    def _build_idms_date_field_lookup(
        self,
        mapping_rows: list[SheetMappingRow],
    ) -> set[tuple[str, str]]:
        output: set[tuple[str, str]] = set()

        for row in mapping_rows or []:
            db2_type = self._first_non_empty(
                row.new_db2_data_type,
                row.cross_application_db2_data_type,
            ).upper()

            if "DATE" not in db2_type:
                continue

            record_candidates = self._record_name_candidates(
                row.cobol_record_idms,
                row.cobol_zone,
            )

            field_candidates = self._field_name_candidates(
                row.cobol_zone,
                row.reference_field_name_copybook,
            )

            for record in record_candidates:
                for field in field_candidates:
                    if record and field:
                        output.add(
                            (
                                record,
                                field,
                            )
                        )
                        output.add(
                            (
                                self._compact_name(record),
                                self._compact_name(field),
                            )
                        )

        return output

    def _field_name_candidates(
        self,
        *values: str,
    ) -> list[str]:
        output: list[str] = []

        for value in values:
            text = str(value or "").strip()

            if not text:
                continue

            normalized = NameNormalizer.normalize(
                text,
            )

            if normalized:
                output.append(
                    normalized,
                )

            tokens = re.findall(
                r"[A-Z][A-Z0-9-]*",
                text.upper(),
            )

            for token in tokens:
                token_normalized = NameNormalizer.normalize(
                    token,
                )

                if not token_normalized:
                    continue

                if token_normalized in self.IGNORE_TOKENS:
                    continue

                output.append(
                    token_normalized,
                )
                output.append(
                    self._compact_name(token_normalized),
                )

                no_suffix = NameNormalizer.remove_record_suffix(
                    token_normalized,
                )

                if no_suffix:
                    output.append(
                        no_suffix,
                    )
                    output.append(
                        self._compact_name(no_suffix),
                    )

        return self._unique_non_empty(
            output,
        )

    def _is_date_host(
        self,
        dclgen_group: str,
        host_field: str,
    ) -> bool:
        key = (
            NameNormalizer.to_cobol(NameNormalizer.normalize(dclgen_group)),
            NameNormalizer.to_cobol(NameNormalizer.normalize(host_field)),
        )

        if key in self.date_host_lookup:
            return True

        field = NameNormalizer.normalize(
            host_field,
        )

        return (
            field.startswith("DA_")
            or field.startswith("DT_")
            or field.startswith("TS_")
        )

    def _is_idms_date_field(
        self,
        record_name: str,
        field_name: str,
    ) -> bool:
        record_candidates = self._record_name_candidates(
            record_name,
        )
        field_candidates = self._field_name_candidates(
            field_name,
        )

        for record in record_candidates:
            for field in field_candidates:
                if (
                    record,
                    field,
                ) in self.idms_date_field_lookup:
                    return True

                if (
                    self._compact_name(record),
                    self._compact_name(field),
                ) in self.idms_date_field_lookup:
                    return True

        normalized_field = NameNormalizer.normalize(
            field_name,
        )

        return normalized_field.startswith("DA_") or normalized_field.startswith("DT_")

    def _date_usage_type(
        self,
        logical: str,
    ) -> str:
        upper = str(logical or "").upper()

        if self.IF_OR_EVALUATE_PATTERN.match(
            upper,
        ):
            return "comparison"

        if upper.startswith("MOVE "):
            return "move"

        return ""

    def _dedupe_date_usages(
        self,
        usages: list[DateUsage],
    ) -> list[DateUsage]:
        output: list[DateUsage] = []
        seen: set[tuple[str, str, str, int, str]] = set()

        for usage in usages:
            key = (
                NameNormalizer.normalize(usage.dclgen_group),
                NameNormalizer.normalize(usage.idms_record),
                NameNormalizer.normalize(usage.host_field),
                usage.line_number,
                usage.usage_type,
            )

            if key in seen:
                continue

            seen.add(
                key,
            )
            output.append(
                usage,
            )

        return output

    #
    # Helpers
    #
    def _is_continuation_like_line(
        self,
        logical: str,
    ) -> bool:
        upper = str(logical or "").strip().upper()

        return (
            not upper
            or upper.startswith("AND ")
            or upper.startswith("OR ")
            or upper.startswith("UNTIL ")
            or upper.startswith(",")
        )

    def _first_non_empty(
        self,
        *values: str,
    ) -> str:
        for value in values:
            text = str(value or "").strip()

            if text:
                return text

        return ""

    def _compact_name(
        self,
        value: str,
    ) -> str:
        normalized = NameNormalizer.normalize(
            value,
        )

        return re.sub(
            r"[^A-Z0-9]+",
            "",
            normalized,
        )

    def _unique_non_empty(
        self,
        values: list[str],
    ) -> list[str]:
        output: list[str] = []

        for value in values:
            text = str(value or "").strip()

            if not text:
                continue

            if text not in output:
                output.append(
                    text,
                )

        return output