from __future__ import annotations

import re

from idms_db2_phase2.domain.models import ConversionInput, ConversionResult
from idms_db2_phase2.services.cobol_formatter import CobolFormatter
from idms_db2_phase2.services.cobol_transformer import CobolTransformer
from idms_db2_phase2.services.db2_cursor_paragraph_generator import (
    Db2CursorParagraphGenerator,
)
from idms_db2_phase2.services.db2_infrastructure_generator import (
    Db2InfrastructureGenerator,
)
from idms_db2_phase2.services.field_reference_rewriter import FieldReferenceRewriter
from idms_db2_phase2.services.pic_length_auto_fixer import PicLengthAutoFixer
from idms_db2_phase2.services.production_validator import ProductionValidator
from idms_db2_phase2.services.program_flow_analyzer import ProgramFlowAnalyzer
from idms_db2_phase2.services.sql_generator import SqlGenerator
from idms_db2_phase2.services.timestamp_generator import TimestampGenerator
from idms_db2_phase2.services.validation_service import ValidationService


class ConversionService:
    """
    Main IDMS COBOL to DB2 COBOL conversion orchestration service.

    Production rules:
    - Sheet Mapping is the authority for DB2 record/table names.
    - Sheet Mapping is the authority for DB2 column names.
    - DCLGEN is the authority for COBOL host variable names and PIC clauses.
    - Original COBOL is the authority for business flow.
    - Final output is resequenced in manual-style COBOL format.

    This service must not hardcode business records, tables, columns,
    DCLGEN names, or host variables.
    """

    BODY_WIDTH = 76

    CURSOR_PARAGRAPH_MARKER = "DB2 GENERATED CURSOR OPEN FETCH CLOSE PARAGRAPHS"

    INFORMATIONAL_MESSAGE_PREFIXES = (
        "DB2 infrastructure:",
        "DB2 cursor paragraphs:",
        "Timestamp generator:",
    )

    LEFT_SEQUENCE_PATTERN = re.compile(
        r"^\s*(?P<seq>\d{6})(?P<body>\s+.*)$",
        flags=re.IGNORECASE,
    )

    LEFT_SEQUENCE_IMMEDIATE_PATTERN = re.compile(
        r"^\s*(?P<seq>\d{6})(?P<body>[\*/].*)$",
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

    IDMS_ABORT_PARAGRAPH_PATTERN = re.compile(
        r"^\s*IDMS-ABORT\.\s*$",
        flags=re.IGNORECASE,
    )

    NEXT_PARAGRAPH_PATTERN = re.compile(
        r"^\s*[A-Z0-9][A-Z0-9-]*\.\s*$",
        flags=re.IGNORECASE,
    )

    SQL_ERROR_PARAGRAPH_PATTERN = re.compile(
        r"^\s*(SQL-ERROR|SQLERROR)\.\s*$",
        flags=re.IGNORECASE,
    )

    END_PROGRAM_PATTERN = re.compile(
        r"^\s*END\s+PROGRAM\b.*\.\s*$",
        flags=re.IGNORECASE,
    )

    GENERATED_CURSOR_PARAGRAPH_PATTERN = re.compile(
        r"^\s*\d{3}-(?:OPEN|FETCH|CLOSE)-(?P<cursor>[A-Z0-9-]+)\.\s*$",
        flags=re.IGNORECASE,
    )

    IF_NOT_CURSOR_EOC_PATTERN = re.compile(
        r"^(?P<indent>\s*)IF\s+NOT\s+(?P<cursor>[A-Z0-9-]+)-EOC\.?\s*$",
        flags=re.IGNORECASE,
    )

    IF_CURSOR_EOC_PATTERN_TEMPLATE = (
        r"^(?P<indent>\s*)IF\s+{cursor}-EOC\.?\s*$"
    )

    PERFORM_FETCH_PATTERN_TEMPLATE = (
        r"^\s*PERFORM\s+\d{{3}}-FETCH-{cursor}\.?\s*$"
    )

    SQLCODE_CHECK_PATTERN = re.compile(
        r"^\s*IF\s+SQLCODE\s+NOT\s*=\s*0\s+AND\s+SQLCODE\s+NOT\s*=\s*100\.?\s*$",
        flags=re.IGNORECASE,
    )

    IF_PATTERN = re.compile(
        r"^\s*IF\b",
        flags=re.IGNORECASE,
    )

    END_IF_PATTERN = re.compile(
        r"^\s*END-IF\.?\s*$",
        flags=re.IGNORECASE,
    )

    END_IF_WITHOUT_PERIOD_PATTERN = re.compile(
        r"^(?P<indent>\s*)END-IF\s*$",
        flags=re.IGNORECASE,
    )

    DECORATIVE_STAR_LINE_PATTERN = re.compile(
        r"^\s*\*{5,}\s*$",
        flags=re.IGNORECASE,
    )

    PAGE_EJECT_PATTERN = re.compile(
        r"^\s*/\s*$",
        flags=re.IGNORECASE,
    )

    COMMENT_PATTERN = re.compile(
        r"^\s*\*",
        flags=re.IGNORECASE,
    )

    def __init__(self) -> None:
        self.validation_service = ValidationService()
        self.db2_infrastructure_generator = Db2InfrastructureGenerator()
        self.production_validator = ProductionValidator()
        self.formatter = CobolFormatter()

    def convert(
        self,
        conversion_input: ConversionInput,
    ) -> ConversionResult:
        validation_messages: list[str] = []

        if not conversion_input.idms_cobol_text.strip():
            return ConversionResult(
                converted_cobol="",
                validation_messages=["IDMS COBOL source text is empty."],
                operations=[],
            )

        sql_generator = SqlGenerator(
            rows=conversion_input.sheet_mapping_rows,
            dclgen_columns=conversion_input.dclgen_columns,
        )

        transformer = CobolTransformer(
            sql_generator=sql_generator,
        )

        converted_cobol, transform_messages, operations = transformer.transform(
            cobol_text=conversion_input.idms_cobol_text,
            target_program_id=conversion_input.target_program_id,
        )

        self._extend_validation_messages(
            validation_messages=validation_messages,
            new_messages=transform_messages,
        )

        self._analyze_program_flow_for_diagnostics(
            conversion_input=conversion_input,
            operations=operations,
            validation_messages=validation_messages,
        )

        converted_cobol, infrastructure_messages = (
            self.db2_infrastructure_generator.apply(
                cobol_text=converted_cobol,
                dclgen_columns=conversion_input.dclgen_columns,
                operations=operations,
                mapping_rows=conversion_input.sheet_mapping_rows,
            )
        )

        self._extend_validation_messages(
            validation_messages=validation_messages,
            new_messages=infrastructure_messages,
        )

        cursor_generator = Db2CursorParagraphGenerator(
            mapping_rows=conversion_input.sheet_mapping_rows,
            dclgen_columns=conversion_input.dclgen_columns,
            operations=operations,
        )

        converted_cobol, cursor_messages = cursor_generator.apply(
            cobol_text=converted_cobol,
        )

        self._extend_validation_messages(
            validation_messages=validation_messages,
            new_messages=cursor_messages,
        )

        converted_cobol = self._apply_field_reference_rewrite(
            converted_cobol=converted_cobol,
            conversion_input=conversion_input,
            validation_messages=validation_messages,
            pass_name="pre-format",
        )

        timestamp_generator = TimestampGenerator(
            mapping_rows=conversion_input.sheet_mapping_rows,
            dclgen_columns=conversion_input.dclgen_columns,
        )

        converted_cobol, timestamp_messages = timestamp_generator.apply(
            cobol_text=converted_cobol,
            target_program_id=conversion_input.target_program_id,
        )

        self._extend_validation_messages(
            validation_messages=validation_messages,
            new_messages=timestamp_messages,
        )

        if conversion_input.auto_fix_pic_length_mismatches:
            pic_length_auto_fixer = PicLengthAutoFixer()

            converted_cobol = pic_length_auto_fixer.fix(
                source_cobol_text=conversion_input.idms_cobol_text,
                converted_cobol_text=converted_cobol,
            )

            self._extend_validation_messages(
                validation_messages=validation_messages,
                new_messages=pic_length_auto_fixer.messages,
            )

        converted_cobol = self._clean_embedded_sequence_artifacts_in_text(
            text=converted_cobol,
            preserve_indentation=True,
        )

        converted_cobol = self.formatter.format(
            text=converted_cobol,
        )

        if converted_cobol is None:
            converted_cobol = ""

        converted_cobol = self._clean_embedded_sequence_artifacts_in_text(
            text=converted_cobol,
            preserve_indentation=True,
        )

        converted_cobol = self._apply_field_reference_rewrite(
            converted_cobol=converted_cobol,
            conversion_input=conversion_input,
            validation_messages=validation_messages,
            pass_name="post-format",
        )

        converted_cobol = self._remove_orphan_idms_abort_paragraph(
            converted_cobol,
        )

        converted_cobol = self._remove_redundant_continue_before_cursor_block(
            converted_cobol,
        )

        converted_cobol = self._split_nested_cursor_eoc_write_blocks(
            converted_cobol,
        )

        converted_cobol = self._normalize_terminal_end_if_periods(
            converted_cobol,
        )

        converted_cobol = self._move_sql_error_after_cursor_paragraphs(
            converted_cobol,
        )

        converted_cobol = self._normalize_single_infrastructure_block(
            converted_cobol,
        )

        converted_cobol = self._clean_embedded_sequence_artifacts_in_text(
            text=converted_cobol,
            preserve_indentation=True,
        )

        production_messages = self.production_validator.validate(
            source_cobol_text=conversion_input.idms_cobol_text,
            converted_cobol_text=converted_cobol,
            mapping_rows=conversion_input.sheet_mapping_rows,
            dclgen_columns=conversion_input.dclgen_columns,
        )

        self._extend_validation_messages(
            validation_messages=validation_messages,
            new_messages=production_messages,
        )

        converted_cobol = self._apply_manual_sequence_numbers(
            source_cobol_text=conversion_input.idms_cobol_text,
            converted_cobol_text=converted_cobol,
        )

        return ConversionResult(
            converted_cobol=converted_cobol,
            validation_messages=validation_messages,
            operations=operations,
        )

    #
    # Program flow analysis diagnostics
    #
    def _analyze_program_flow_for_diagnostics(
        self,
        conversion_input: ConversionInput,
        operations: list,
        validation_messages: list[str],
    ) -> None:
        analyzer = ProgramFlowAnalyzer(
            mapping_rows=conversion_input.sheet_mapping_rows,
            dclgen_columns=conversion_input.dclgen_columns,
        )

        analysis = analyzer.analyze(
            cobol_text=conversion_input.idms_cobol_text,
            operations=operations,
        )

        diagnostic_messages: list[str] = []

        diagnostic_messages.extend(
            analysis.diagnostics,
        )

        for loop in analysis.cursor_loops:
            diagnostic_messages.append(
                "Program flow analyzer: cursor loop: "
                f"type={loop.loop_type}, "
                f"record={loop.record_name}, "
                f"set={loop.set_name}, "
                f"cursor={loop.cursor_name}, "
                f"process={loop.process_paragraph}, "
                f"open={loop.open_paragraph}, "
                f"fetch={loop.fetch_paragraph}, "
                f"close={loop.close_paragraph}",
            )

        for write in analysis.output_writes:
            diagnostic_messages.append(
                "Program flow analyzer: output write: "
                f"record={write.output_record}, "
                f"paragraph={write.paragraph_name}, "
                f"line={write.write_line}, "
                f"nearby_moves={len(write.move_lines)}",
            )

        for usage in analysis.date_usages:
            diagnostic_messages.append(
                "Program flow analyzer: date usage: "
                f"type={usage.usage_type}, "
                f"group={usage.dclgen_group}, "
                f"field={usage.host_field}, "
                f"line={usage.line_number}",
            )

        self._extend_validation_messages(
            validation_messages=validation_messages,
            new_messages=diagnostic_messages,
        )

    #
    # Field reference rewrite
    #
    def _apply_field_reference_rewrite(
        self,
        converted_cobol: str,
        conversion_input: ConversionInput,
        validation_messages: list[str],
        pass_name: str,
    ) -> str:
        field_reference_rewriter = FieldReferenceRewriter(
            mapping_rows=conversion_input.sheet_mapping_rows,
            dclgen_columns=conversion_input.dclgen_columns,
        )

        rewritten_cobol = field_reference_rewriter.rewrite(
            text=converted_cobol,
        )

        rewrite_messages: list[str] = []

        for message in field_reference_rewriter.rewrite_messages:
            rewrite_messages.append(
                f"Field reference rewrite {pass_name}: {message}",
            )

        self._extend_validation_messages(
            validation_messages=validation_messages,
            new_messages=rewrite_messages,
        )

        return rewritten_cobol

    #
    # Validation messages
    #
    def _extend_validation_messages(
        self,
        validation_messages: list[str],
        new_messages: list[str],
    ) -> None:
        for message in new_messages or []:
            normalized = str(message or "").strip()

            if not normalized:
                continue

            if normalized.startswith(
                self.INFORMATIONAL_MESSAGE_PREFIXES,
            ):
                continue

            validation_messages.append(
                normalized,
            )

    #
    # Structural cleanup
    #
    def _normalize_single_infrastructure_block(
        self,
        text: str,
    ) -> str:
        if not text:
            return ""

        text = self._normalize_blank_lines(
            text,
        )

        return text.rstrip() + "\n"

    def _remove_redundant_continue_before_cursor_block(
        self,
        text: str,
    ) -> str:
        if not text:
            return ""

        lines = text.splitlines()
        output: list[str] = []
        index = 0

        while index < len(lines):
            logical = self._strip_existing_sequence_numbers(
                lines[index],
                preserve_indentation=False,
            ).strip()

            next_logical = ""

            if index + 1 < len(lines):
                next_logical = self._strip_existing_sequence_numbers(
                    lines[index + 1],
                    preserve_indentation=False,
                ).strip()

            if (
                logical.upper() == "CONTINUE."
                and self.CURSOR_PARAGRAPH_MARKER in next_logical.upper()
            ):
                index += 1
                continue

            output.append(
                lines[index],
            )
            index += 1

        return "\n".join(output).rstrip() + "\n"

    def _remove_orphan_idms_abort_paragraph(
        self,
        text: str,
    ) -> str:
        if not text:
            return ""

        lines = text.splitlines()
        output: list[str] = []
        index = 0

        while index < len(lines):
            logical = self._strip_existing_sequence_numbers(
                lines[index],
                preserve_indentation=False,
            ).strip()

            if self.IDMS_ABORT_PARAGRAPH_PATTERN.match(
                logical,
            ):
                index = self._skip_idms_abort_block(
                    lines=lines,
                    start_index=index,
                )
                continue

            output.append(
                lines[index],
            )
            index += 1

        return "\n".join(output).rstrip() + "\n"

    def _skip_idms_abort_block(
        self,
        lines: list[str],
        start_index: int,
    ) -> int:
        index = start_index + 1

        while index < len(lines):
            logical = self._strip_existing_sequence_numbers(
                lines[index],
                preserve_indentation=False,
            ).strip()

            if not logical:
                index += 1
                continue

            if (
                self.NEXT_PARAGRAPH_PATTERN.match(logical)
                and logical.upper() != "EXIT."
            ):
                break

            if logical.upper() == "EXIT.":
                index += 1
                break

            index += 1

        return index

    def _split_nested_cursor_eoc_write_blocks(
        self,
        text: str,
    ) -> str:
        if not text:
            return ""

        lines = text.splitlines()
        output: list[str] = []
        index = 0

        while index < len(lines):
            outer_match = self.IF_NOT_CURSOR_EOC_PATTERN.match(
                self._logical_line_preserve_indent(lines[index]),
            )

            if not outer_match:
                output.append(lines[index])
                index += 1
                continue

            replacement, next_index = self._try_split_one_nested_cursor_eoc_block(
                lines=lines,
                start_index=index,
                outer_match=outer_match,
            )

            if replacement is None:
                output.append(lines[index])
                index += 1
                continue

            output.extend(replacement)
            index = next_index

        return "\n".join(output).rstrip() + "\n"

    def _try_split_one_nested_cursor_eoc_block(
        self,
        lines: list[str],
        start_index: int,
        outer_match: re.Match,
    ) -> tuple[list[str] | None, int]:
        cursor_name = outer_match.group("cursor").upper()
        outer_indent = outer_match.group("indent") or ""

        search_end = min(
            len(lines),
            start_index + 20,
        )

        fetch_pattern = re.compile(
            self.PERFORM_FETCH_PATTERN_TEMPLATE.format(
                cursor=re.escape(cursor_name),
            ),
            flags=re.IGNORECASE,
        )

        inner_eoc_pattern = re.compile(
            self.IF_CURSOR_EOC_PATTERN_TEMPLATE.format(
                cursor=re.escape(cursor_name),
            ),
            flags=re.IGNORECASE,
        )

        fetch_index = -1
        sqlcode_if_index = -1
        sqlcode_end_if_index = -1
        inner_eoc_if_index = -1

        for candidate_index in range(start_index + 1, search_end):
            logical = self._logical_line_preserve_indent(
                lines[candidate_index],
            )

            if fetch_index < 0 and fetch_pattern.match(logical):
                fetch_index = candidate_index
                continue

            if fetch_index >= 0 and sqlcode_if_index < 0:
                if self.SQLCODE_CHECK_PATTERN.match(logical):
                    sqlcode_if_index = candidate_index
                    continue

            if sqlcode_if_index >= 0 and sqlcode_end_if_index < 0:
                if self.END_IF_PATTERN.match(logical):
                    sqlcode_end_if_index = candidate_index
                    continue

            if sqlcode_end_if_index >= 0:
                if not logical.strip():
                    continue

                if logical.strip().startswith("*"):
                    continue

                if inner_eoc_pattern.match(logical):
                    inner_eoc_if_index = candidate_index
                    break

                break

        if (
            fetch_index < 0
            or sqlcode_if_index < 0
            or sqlcode_end_if_index < 0
            or inner_eoc_if_index < 0
        ):
            return None, start_index

        inner_end_if_index = self._find_matching_end_if(
            lines=lines,
            start_index=inner_eoc_if_index,
        )

        if inner_end_if_index < 0:
            return None, start_index

        outer_end_if_index = self._next_nonblank_index(
            lines=lines,
            start_index=inner_end_if_index + 1,
        )

        if outer_end_if_index < 0:
            return None, start_index

        outer_end_logical = self._logical_line_preserve_indent(
            lines[outer_end_if_index],
        )

        if not self.END_IF_PATTERN.match(
            outer_end_logical,
        ):
            return None, start_index

        replacement: list[str] = []

        replacement.extend(
            lines[start_index:inner_eoc_if_index],
        )

        replacement.append(
            outer_indent + "END-IF.",
        )

        replacement.append("")

        inner_block = lines[inner_eoc_if_index:outer_end_if_index]

        replacement.extend(
            self._deindent_block_one_level(
                lines=inner_block,
                spaces=4,
            )
        )

        return replacement, outer_end_if_index + 1

    def _deindent_block_one_level(
        self,
        lines: list[str],
        spaces: int = 4,
    ) -> list[str]:
        output: list[str] = []
        prefix = " " * spaces

        for line in lines:
            text = str(line or "").rstrip()

            if text.startswith(prefix):
                output.append(
                    text[spaces:],
                )
                continue

            output.append(
                text,
            )

        return output

    def _find_matching_end_if(
        self,
        lines: list[str],
        start_index: int,
    ) -> int:
        depth = 0

        for index in range(start_index, len(lines)):
            logical = self._logical_line(
                lines[index],
            )

            if not logical:
                continue

            upper = logical.upper()

            if upper.startswith("*"):
                continue

            if self.IF_PATTERN.match(
                logical,
            ):
                depth += 1

            if self.END_IF_PATTERN.match(
                logical,
            ):
                depth -= 1

                if depth == 0:
                    return index

        return -1

    def _next_nonblank_index(
        self,
        lines: list[str],
        start_index: int,
    ) -> int:
        for index in range(start_index, len(lines)):
            logical = self._logical_line(
                lines[index],
            )

            if logical:
                return index

        return -1

    def _normalize_terminal_end_if_periods(
        self,
        text: str,
    ) -> str:
        if not text:
            return ""

        lines = text.splitlines()
        output: list[str] = []

        for index, line in enumerate(lines):
            logical_with_indent = self._logical_line_preserve_indent(
                line,
            )

            match = self.END_IF_WITHOUT_PERIOD_PATTERN.match(
                logical_with_indent,
            )

            if not match:
                output.append(line)
                continue

            next_meaningful = self._next_meaningful_logical_line(
                lines=lines,
                start_index=index + 1,
            )

            if self._should_terminal_end_if_have_period(
                next_meaningful,
            ):
                indent = match.group("indent") or ""
                output.append(
                    indent + "END-IF.",
                )
                continue

            output.append(line)

        return "\n".join(output).rstrip() + "\n"

    def _next_meaningful_logical_line(
        self,
        lines: list[str],
        start_index: int,
    ) -> str:
        for index in range(start_index, len(lines)):
            logical = self._logical_line(
                lines[index],
            )

            if not logical:
                continue

            if self.COMMENT_PATTERN.match(
                logical,
            ):
                continue

            return logical

        return ""

    def _should_terminal_end_if_have_period(
        self,
        next_meaningful: str,
    ) -> bool:
        logical = str(next_meaningful or "").strip()
        upper = logical.upper()

        if not logical:
            return True

        if self.PAGE_EJECT_PATTERN.match(
            logical,
        ):
            return True

        if self.CURSOR_PARAGRAPH_MARKER in upper:
            return True

        if self.SQL_ERROR_PARAGRAPH_PATTERN.match(
            logical,
        ):
            return True

        if self.END_PROGRAM_PATTERN.match(
            logical,
        ):
            return True

        if self.GENERATED_CURSOR_PARAGRAPH_PATTERN.match(
            logical,
        ):
            return True

        if self.NEXT_PARAGRAPH_PATTERN.match(
            logical,
        ):
            if upper not in {
                "END-IF.",
                "END-EVALUATE.",
                "END-EXEC.",
            }:
                return True

        return False

    def _move_sql_error_after_cursor_paragraphs(
        self,
        text: str,
    ) -> str:
        if not text:
            return ""

        lines = text.splitlines()

        sql_error_start = self._find_sql_error_start_index(
            lines,
        )

        cursor_marker_index = self._find_cursor_marker_index(
            lines,
        )

        if sql_error_start < 0 or cursor_marker_index < 0:
            return text.rstrip() + "\n"

        if sql_error_start > cursor_marker_index:
            return text.rstrip() + "\n"

        sql_error_end = self._find_sql_error_end_index(
            lines=lines,
            start_index=sql_error_start,
        )

        if sql_error_end <= sql_error_start:
            return text.rstrip() + "\n"

        sql_error_block = self._trim_blank_edges(
            lines[sql_error_start:sql_error_end],
        )

        remaining_lines = lines[:sql_error_start] + lines[sql_error_end:]
        remaining_lines = self._remove_excess_blank_runs_around_index(
            remaining_lines,
            sql_error_start,
        )

        insertion_index = self._find_sql_error_reinsert_index(
            remaining_lines,
        )

        output_lines = (
            remaining_lines[:insertion_index]
            + [""]
            + sql_error_block
            + remaining_lines[insertion_index:]
        )

        return self._normalize_blank_lines(
            "\n".join(output_lines),
        ).rstrip() + "\n"

    def _find_sql_error_start_index(
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

    def _find_cursor_marker_index(
        self,
        lines: list[str],
    ) -> int:
        for index, line in enumerate(lines):
            logical = self._logical_line(
                line,
            ).upper()

            if self.CURSOR_PARAGRAPH_MARKER in logical:
                return index

        return -1

    def _find_sql_error_end_index(
        self,
        lines: list[str],
        start_index: int,
    ) -> int:
        index = start_index + 1
        seen_sql_error_body = False

        while index < len(lines):
            logical = self._logical_line(
                lines[index],
            )

            if not logical:
                index += 1
                continue

            upper = logical.upper()

            if upper.startswith("DISPLAY ") or upper.startswith("MOVE "):
                seen_sql_error_body = True
                index += 1
                continue

            if seen_sql_error_body and self.DECORATIVE_STAR_LINE_PATTERN.match(
                logical,
            ):
                return self._trim_end_index_before_blank_run(
                    lines=lines,
                    end_index=index,
                )

            if self.CURSOR_PARAGRAPH_MARKER in upper:
                return self._trim_end_index_before_blank_run(
                    lines=lines,
                    end_index=index,
                )

            if self.GENERATED_CURSOR_PARAGRAPH_PATTERN.match(
                logical,
            ):
                return self._trim_end_index_before_blank_run(
                    lines=lines,
                    end_index=index,
                )

            if (
                self.NEXT_PARAGRAPH_PATTERN.match(logical)
                and not self.SQL_ERROR_PARAGRAPH_PATTERN.match(logical)
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

            index += 1

        return len(lines)

    def _find_sql_error_reinsert_index(
        self,
        lines: list[str],
    ) -> int:
        end_program_index = -1

        for index, line in enumerate(lines):
            logical = self._logical_line(
                line,
            )

            if self.END_PROGRAM_PATTERN.match(
                logical,
            ):
                end_program_index = index
                break

        if end_program_index >= 0:
            return self._trim_start_index_before_blank_run(
                lines=lines,
                start_index=end_program_index,
            )

        return len(lines)

    def _remove_excess_blank_runs_around_index(
        self,
        lines: list[str],
        index: int,
    ) -> list[str]:
        _ = index
        text = "\n".join(lines)
        text = self._normalize_blank_lines(
            text,
        )

        return text.splitlines()

    def _trim_blank_edges(
        self,
        lines: list[str],
    ) -> list[str]:
        start = 0
        end = len(lines)

        while start < end and not str(lines[start] or "").strip():
            start += 1

        while end > start and not str(lines[end - 1] or "").strip():
            end -= 1

        return lines[start:end]

    def _trim_end_index_before_blank_run(
        self,
        lines: list[str],
        end_index: int,
    ) -> int:
        while end_index > 0 and not lines[end_index - 1].strip():
            end_index -= 1

        return end_index

    def _trim_start_index_before_blank_run(
        self,
        lines: list[str],
        start_index: int,
    ) -> int:
        while start_index > 0 and not lines[start_index - 1].strip():
            start_index -= 1

        return start_index

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

    #
    # Generic embedded sequence cleanup
    #
    def _clean_embedded_sequence_artifacts_in_text(
        self,
        text: str,
        preserve_indentation: bool,
    ) -> str:
        if not text:
            return ""

        output_lines: list[str] = []

        for raw_line in text.splitlines():
            body = self._strip_existing_sequence_numbers(
                raw_line,
                preserve_indentation=preserve_indentation,
            )

            body = self._clean_embedded_sequence_artifacts(
                value=body,
                preserve_indentation=preserve_indentation,
            )

            output_lines.append(
                body,
            )

        return "\n".join(output_lines).rstrip() + "\n"

    def _clean_embedded_sequence_artifacts(
        self,
        value: str,
        preserve_indentation: bool,
    ) -> str:
        text = str(value or "").rstrip()

        if not text:
            return ""

        text = self._strip_duplicate_left_sequence_from_body(
            text=text,
            preserve_indentation=preserve_indentation,
        )

        text = self._strip_trailing_right_sequence_from_body(
            text,
        )

        text = self._strip_comment_sequence_artifacts(
            text,
        )

        text = self._strip_identifier_sequence_artifacts(
            text,
        )

        text = self._strip_decorative_sequence_artifacts(
            text,
        )

        return text.rstrip()

    def _strip_existing_sequence_numbers(
        self,
        line: str,
        preserve_indentation: bool,
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
                body = left_match.group("body")

                if preserve_indentation:
                    text = body
                else:
                    text = body.lstrip()

                continue

            immediate_left_match = self.LEFT_SEQUENCE_IMMEDIATE_PATTERN.match(
                text,
            )

            if immediate_left_match:
                body = immediate_left_match.group("body")

                if preserve_indentation:
                    text = body
                else:
                    text = body.lstrip()

                continue

            break

        return text.rstrip()

    def _strip_duplicate_left_sequence_from_body(
        self,
        text: str,
        preserve_indentation: bool,
    ) -> str:
        cleaned = str(text or "").rstrip()

        left_match = self.LEFT_SEQUENCE_PATTERN.match(
            cleaned,
        )

        if left_match:
            body = left_match.group("body")

            if preserve_indentation:
                return body.rstrip()

            return body.lstrip().rstrip()

        immediate_left_match = self.LEFT_SEQUENCE_IMMEDIATE_PATTERN.match(
            cleaned,
        )

        if immediate_left_match:
            body = immediate_left_match.group("body")

            if preserve_indentation:
                return body.rstrip()

            return body.lstrip().rstrip()

        return cleaned

    def _strip_trailing_right_sequence_from_body(
        self,
        text: str,
    ) -> str:
        cleaned = str(text or "").rstrip()

        right_match = self.RIGHT_SEQUENCE_PATTERN.match(
            cleaned,
        )

        if right_match and right_match.group("right"):
            return right_match.group("body").rstrip()

        return cleaned

    def _strip_comment_sequence_artifacts(
        self,
        text: str,
    ) -> str:
        leading_match = re.match(
            r"^(?P<indent>\s*)(?P<body>.*)$",
            text.rstrip(),
        )

        if not leading_match:
            return text.rstrip()

        indent = leading_match.group("indent") or ""
        body = leading_match.group("body") or ""
        stripped = body.strip()

        if not stripped.startswith("*"):
            return text.rstrip()

        body = re.sub(
            r"(?<=\*)\d{1,8}$",
            "",
            body,
        )

        body = re.sub(
            r"(?<=\*)0{3,}\d*$",
            "",
            body,
        )

        return (indent + body).rstrip()

    def _strip_decorative_sequence_artifacts(
        self,
        text: str,
    ) -> str:
        leading_match = re.match(
            r"^(?P<indent>\s*)(?P<body>.*)$",
            text.rstrip(),
        )

        if not leading_match:
            return text.rstrip()

        indent = leading_match.group("indent") or ""
        body = leading_match.group("body") or ""
        stripped = body.strip()

        if not stripped:
            return text.rstrip()

        decorative_body = stripped.rstrip("0123456789")

        if decorative_body and not re.search(
            r"[A-Z]",
            decorative_body,
            flags=re.IGNORECASE,
        ):
            body = re.sub(
                r"\d{1,8}$",
                "",
                body,
            )
            return (indent + body).rstrip()

        if stripped.startswith("*") and stripped.count("*") >= 2:
            body = re.sub(
                r"\d{1,8}$",
                "",
                body,
            )
            return (indent + body).rstrip()

        return text.rstrip()

    def _strip_identifier_sequence_artifacts(
        self,
        text: str,
    ) -> str:
        cleaned = text.rstrip()

        cleaned = re.sub(
            r"(?<=[A-Z0-9-])0{3,}\d{4,}$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )

        cleaned = re.sub(
            r"(?<=[A-Z0-9-])\d{8}$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )

        return cleaned.rstrip()

    #
    # Manual sequence numbering
    #
    def _apply_manual_sequence_numbers(
        self,
        source_cobol_text: str,
        converted_cobol_text: str,
    ) -> str:
        if not converted_cobol_text:
            return ""

        cleaned_text = self._clean_embedded_sequence_artifacts_in_text(
            text=converted_cobol_text,
            preserve_indentation=True,
        )

        prepared_lines = self._expand_lines_for_manual_layout(
            cleaned_text,
        )

        left_step = self._detect_left_sequence_step(
            source_cobol_text,
        )

        left_start = left_step
        right_start = 10000
        right_step = 10000

        body_width = self._detect_output_body_width(
            prepared_lines,
        )

        output_lines: list[str] = []
        left_number = left_start
        right_number = right_start

        for body in prepared_lines:
            if not str(body or "").strip():
                output_lines.append("")
                continue

            cleaned_body = self._clean_embedded_sequence_artifacts(
                value=body,
                preserve_indentation=True,
            )

            left_seq = f"{left_number:06d}"
            right_seq = f"{right_number:08d}"

            output_lines.append(
                self._compose_sequenced_line(
                    left_seq=left_seq,
                    body=cleaned_body,
                    right_seq=right_seq,
                    body_width=body_width,
                )
            )

            left_number += left_step
            right_number += right_step

        return "\n".join(output_lines).rstrip() + "\n"

    def _expand_lines_for_manual_layout(
        self,
        text: str,
    ) -> list[str]:
        output: list[str] = []

        for raw_line in text.splitlines():
            body = self._strip_existing_sequence_numbers(
                raw_line,
                preserve_indentation=True,
            )

            body = self._clean_embedded_sequence_artifacts(
                value=body,
                preserve_indentation=True,
            )

            if not body.strip():
                output.append("")
                continue

            if self._is_generated_long_comment(
                body,
            ):
                output.extend(
                    self._wrap_comment_line(
                        body,
                    )
                )
                continue

            output.append(
                body,
            )

        return output

    def _is_generated_long_comment(
        self,
        body: str,
    ) -> bool:
        stripped = str(body or "").strip()

        if not stripped.startswith("* DB2:"):
            return False

        return len(stripped) > self.BODY_WIDTH

    def _wrap_comment_line(
        self,
        body: str,
    ) -> list[str]:
        stripped = str(body or "").strip()

        if len(stripped) <= self.BODY_WIDTH:
            return [body.rstrip()]

        prefix = "* "
        continuation_prefix = "*     "

        if stripped.startswith("* "):
            text = stripped[2:].strip()
        elif stripped.startswith("*"):
            text = stripped[1:].strip()
        else:
            text = stripped

        words = text.split()
        lines: list[str] = []
        current_prefix = prefix
        current = ""

        for word in words:
            max_text_width = self.BODY_WIDTH - len(current_prefix)

            if not current:
                current = word
                continue

            candidate = current + " " + word

            if len(candidate) <= max_text_width:
                current = candidate
                continue

            lines.append(
                current_prefix + current,
            )

            current_prefix = continuation_prefix
            current = word

        if current:
            lines.append(
                current_prefix + current,
            )

        return lines

    def _detect_left_sequence_step(
        self,
        source_cobol_text: str,
    ) -> int:
        numbers: list[int] = []

        for line in str(source_cobol_text or "").splitlines():
            match = re.match(
                r"^\s*(\d{6})\b",
                line,
            )

            if not match:
                continue

            try:
                numbers.append(
                    int(match.group(1)),
                )
            except ValueError:
                continue

            if len(numbers) >= 10:
                break

        if len(numbers) >= 2:
            deltas = [
                numbers[index] - numbers[index - 1]
                for index in range(1, len(numbers))
                if numbers[index] > numbers[index - 1]
            ]

            if deltas:
                return min(deltas)

        return 10

    def _detect_output_body_width(
        self,
        prepared_lines: list[str],
    ) -> int:
        width = self.BODY_WIDTH

        for line in prepared_lines:
            length = len(str(line or "").rstrip())

            if length > width:
                width = min(
                    max(length, self.BODY_WIDTH),
                    120,
                )

        return width

    def _compose_sequenced_line(
        self,
        left_seq: str,
        body: str,
        right_seq: str,
        body_width: int,
    ) -> str:
        clean_body = str(body or "").rstrip()

        if not clean_body:
            return f"{left_seq} {right_seq}"

        if len(clean_body) >= body_width:
            return f"{left_seq} {clean_body} {right_seq}"

        return f"{left_seq} {clean_body:<{body_width}} {right_seq}"

    #
    # Logical-line helpers
    #
    def _logical_line(
        self,
        line: str,
    ) -> str:
        text = self._strip_existing_sequence_numbers(
            line,
            preserve_indentation=False,
        )

        return text.strip()

    def _logical_line_preserve_indent(
        self,
        line: str,
    ) -> str:
        return self._strip_existing_sequence_numbers(
            line,
            preserve_indentation=True,
        ).rstrip()