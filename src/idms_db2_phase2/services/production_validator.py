import re

from idms_db2_phase2.domain.models import DclgenColumn, SheetMappingRow
from idms_db2_phase2.services.name_normalizer import NameNormalizer


class ProductionValidator:
    """
    Performs production-focused validation for generated DB2 COBOL.

    It detects:
    - Missing required DB2 constructs.
    - Undefined TODO host variables.
    - Generated DCLGEN host variables that do not exist in uploaded DCLGEN.
    - Residual executable IDMS statements.
    - Residual IDMS declarative/control statements.
    - Residual IDMS copy statements.
    - Residual IDMS record references in PROCEDURE DIVISION.
    - MOVE source-to-target numeric PIC length mismatch.

    This validator is sequence-number aware. It can validate both:
    - unsequenced COBOL
    - manual-style sequenced COBOL:
        000010 COBOL STATEMENT 00010000
    """

    REQUIRED_DB2_TOKENS = [
        "EXEC SQL",
        "SQLCA",
        "END-EXEC",
    ]

    FORBIDDEN_EXECUTABLE_PATTERNS = [
        r"^BIND\b",
        r"^READY\b",
        r"^OBTAIN\b",
        r"^FIND\s+CURRENT\b",
        r"^FIND\s+FIRST\b",
        r"^STORE\b",
        r"^MODIFY\b",
        r"^ERASE\b",
        r"^CONNECT\b",
        r"^DISCONNECT\b",
        r"^PERFORM\s+[A-Z0-9-]*IDMS-STATUS\b",
        r"^PERFORM\s+[A-Z0-9-]*IDMS-ABORT\b",
        r"\bUSAGE-MODE\s+IS\s+UPDATE\b",
        r"\bUSAGE-MODE\s+IS\s+RETRIEVAL\b",
    ]

    FORBIDDEN_IDMS_DECLARATIVE_PATTERNS = [
        r"^IDMS-CONTROL\s+SECTION\b",
        r"^PROTOCOL\b",
        r"^IDMS-RECORDS\s+WITHIN\s+WORKING-STORAGE\s+SECTION\b",
        r"^SCHEMA\s+SECTION\b",
        r"^DB\s+[A-Z0-9-]+\s+WITHIN\s+[A-Z0-9-]+\b",
        r"^COPY\s+IDMS\b",
        r"^COPY\s+IDMS\s+IDMS-STATUS\b",
        r"^COPY\s+IDMS\s+SUBSCHEMA-BINDS\b",
    ]

    HOST_REFERENCE_PATTERN = re.compile(
        r":\s*(?P<group>DCL[A-Z0-9-]+)\s*\.\s*(?P<field>[A-Z][A-Z0-9-]*)",
        flags=re.IGNORECASE,
    )

    DATA_FIELD_PATTERN = re.compile(
        r"^\s*(?P<level>0[1-9]|[1-4][0-9]|66|77|88)\s+"
        r"(?P<name>[A-Z][A-Z0-9-]*)\b"
        r"(?P<rest>.*)$",
        flags=re.IGNORECASE,
    )

    PIC_PATTERN = re.compile(
        r"\bPIC(?:TURE)?\s+(?:IS\s+)?(?P<pic>S?9(?:$(?P<len>\d+)$)?)",
        flags=re.IGNORECASE,
    )

    MOVE_PATTERN = re.compile(
        r"\bMOVE\s+"
        r"(?P<source>[A-Z][A-Z0-9-]*(?:\.[A-Z][A-Z0-9-]*)?)"
        r"(?:\s+OF\s+[A-Z][A-Z0-9-]*)?"
        r"\s+TO\s+"
        r"(?P<target>[A-Z][A-Z0-9-]*(?:\.[A-Z][A-Z0-9-]*)?)\b",
        flags=re.IGNORECASE,
    )

    LEFT_SEQUENCE_PATTERN = re.compile(
        r"^\s*\d{6}\s+(?P<body>.*)$",
        flags=re.IGNORECASE,
    )

    RIGHT_SEQUENCE_PATTERN = re.compile(
        r"(?P<body>.*?)(\s+\d{8})\s*$",
        flags=re.IGNORECASE,
    )

    def validate(
        self,
        source_cobol_text: str,
        converted_cobol_text: str,
        mapping_rows: list[SheetMappingRow],
        dclgen_columns: list[DclgenColumn] | None = None,
    ) -> list[str]:
        messages: list[str] = []
        dclgen_columns = dclgen_columns or []

        normalized_converted_text = self._normalized_validation_text(
            converted_cobol_text,
        )

        self._validate_required_db2_tokens(
            converted_cobol_text=normalized_converted_text,
            messages=messages,
        )

        self._validate_no_todo_or_generated_error(
            converted_cobol_text=normalized_converted_text,
            messages=messages,
        )

        self._validate_generated_dclgen_host_variables(
            converted_cobol_text=normalized_converted_text,
            dclgen_columns=dclgen_columns,
            messages=messages,
        )

        self._validate_forbidden_idms_patterns(
            converted_cobol_text=normalized_converted_text,
            messages=messages,
        )

        self._validate_forbidden_idms_declaratives(
            converted_cobol_text=normalized_converted_text,
            messages=messages,
        )

        self._validate_residual_idms_record_references(
            converted_cobol_text=normalized_converted_text,
            mapping_rows=mapping_rows,
            messages=messages,
        )

        self._validate_move_pic_length_mismatch(
            source_cobol_text=source_cobol_text,
            converted_cobol_text=normalized_converted_text,
            messages=messages,
        )

        return messages

    def _normalized_validation_text(
        self,
        text: str,
    ) -> str:
        """
        Removes manual sequence numbers from each line before validation.

        Example:
            001870 BIND RUN-UNIT 01870000

        becomes:
            BIND RUN-UNIT
        """
        output_lines: list[str] = []

        for line in (text or "").splitlines():
            output_lines.append(
                self._logical_line(
                    line,
                )
            )

        return "\n".join(
            output_lines,
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

    def _is_comment_or_blank(
        self,
        line: str,
    ) -> bool:
        stripped = str(line or "").strip()

        if not stripped:
            return True

        if stripped.startswith("*"):
            return True

        return False

    def _validate_required_db2_tokens(
        self,
        converted_cobol_text: str,
        messages: list[str],
    ) -> None:
        upper = converted_cobol_text.upper()

        for token in self.REQUIRED_DB2_TOKENS:
            if token not in upper:
                messages.append(
                    f"Production validation: required DB2 token missing: {token}"
                )

    def _validate_no_todo_or_generated_error(
        self,
        converted_cobol_text: str,
        messages: list[str],
    ) -> None:
        upper = converted_cobol_text.upper()

        if ": TODO-HOST-VARIABLE" in upper:
            messages.append(
                "Production validation: generated COBOL still contains : TODO-HOST-VARIABLE."
            )

        if "TODO DB2" in upper:
            messages.append(
                "Production validation: generated COBOL still contains TODO DB2 items."
            )

        if "ERROR DB2:" in upper:
            messages.append(
                "Production validation: generated COBOL still contains ERROR DB2 items. Check missing DB2 table, column, cursor, or host-variable mapping."
            )

        if "UNABLE TO DECLARE CURSOR" in upper:
            messages.append(
                "Production validation: cursor declaration could not be generated. Check Sheet Mapping relation/FK rows and DCLGEN columns."
            )

        if "NO FETCH HOST VARIABLES MAPPED" in upper:
            messages.append(
                "Production validation: cursor FETCH host variables could not be generated. Check DCLGEN host variables and Sheet Mapping DB2 columns."
            )

    def _validate_generated_dclgen_host_variables(
        self,
        converted_cobol_text: str,
        dclgen_columns: list[DclgenColumn],
        messages: list[str],
    ) -> None:
        generated_hosts = self._generated_dclgen_host_references(
            converted_cobol_text,
        )

        if not generated_hosts:
            return

        valid_hosts = self._valid_dclgen_host_references(
            dclgen_columns,
        )

        if not valid_hosts:
            messages.append(
                "Production validation: generated COBOL contains DCLGEN host variables, but no DCLGEN host variables were parsed from uploaded DCLGEN files."
            )
            return

        missing_hosts = sorted(
            host for host in generated_hosts if host not in valid_hosts
        )

        for host in missing_hosts:
            messages.append(
                f"Production validation: generated host variable :{host} was not found in uploaded DCLGEN columns."
            )

    def _generated_dclgen_host_references(
        self,
        converted_cobol_text: str,
    ) -> set[str]:
        output: set[str] = set()

        for match in self.HOST_REFERENCE_PATTERN.finditer(
            converted_cobol_text,
        ):
            group = NameNormalizer.to_cobol(
                match.group("group"),
            )
            field = NameNormalizer.to_cobol(
                match.group("field"),
            )

            if group and field:
                output.add(
                    f"{group}.{field}",
                )

        return output

    def _valid_dclgen_host_references(
        self,
        dclgen_columns: list[DclgenColumn],
    ) -> set[str]:
        output: set[str] = set()

        for column in dclgen_columns:
            table = NameNormalizer.normalize(
                column.table_name,
            )
            db2_column = NameNormalizer.normalize(
                column.column_name,
            )
            cobol_host = NameNormalizer.to_cobol(
                column.cobol_host_name or column.column_name,
            )

            if not cobol_host:
                continue

            if table:
                group = f"DCL{NameNormalizer.to_cobol(table)}"

                output.add(
                    f"{group}.{cobol_host}",
                )

                if db2_column:
                    output.add(
                        f"{group}.{NameNormalizer.to_cobol(db2_column)}",
                    )

            if db2_column:
                output.add(
                    NameNormalizer.to_cobol(db2_column),
                )

            output.add(
                cobol_host,
            )

        return output

    def _validate_forbidden_idms_patterns(
        self,
        converted_cobol_text: str,
        messages: list[str],
    ) -> None:
        executable_text = self._procedure_division_text(
            converted_cobol_text,
        )

        for line_number, line in enumerate(
            executable_text.splitlines(),
            start=1,
        ):
            logical = self._logical_line(
                line,
            )

            if self._is_comment_or_blank(
                logical,
            ):
                continue

            for pattern in self.FORBIDDEN_EXECUTABLE_PATTERNS:
                if re.search(
                    pattern,
                    logical,
                    flags=re.IGNORECASE,
                ):
                    messages.append(
                        "Production validation: residual executable IDMS statement remains "
                        f"near PROCEDURE line {line_number}: {logical}"
                    )
                    break

    def _validate_forbidden_idms_declaratives(
        self,
        converted_cobol_text: str,
        messages: list[str],
    ) -> None:
        for line_number, line in enumerate(
            converted_cobol_text.splitlines(),
            start=1,
        ):
            logical = self._logical_line(
                line,
            )

            if self._is_comment_or_blank(
                logical,
            ):
                continue

            for pattern in self.FORBIDDEN_IDMS_DECLARATIVE_PATTERNS:
                if re.search(
                    pattern,
                    logical,
                    flags=re.IGNORECASE,
                ):
                    messages.append(
                        "Production validation: residual IDMS declarative/control statement remains "
                        f"near line {line_number}: {logical}"
                    )
                    break

    def _validate_residual_idms_record_references(
        self,
        converted_cobol_text: str,
        mapping_rows: list[SheetMappingRow],
        messages: list[str],
    ) -> None:
        procedure_text = self._procedure_division_text(
            converted_cobol_text,
        )

        record_names = {
            NameNormalizer.to_cobol(
                row.cobol_record_idms,
            )
            for row in mapping_rows
            if row.cobol_record_idms
        }

        record_names = {
            record for record in record_names if record
        }

        if not record_names:
            return

        for record in sorted(record_names):
            pattern = rf"\b(?:OF|IN)\s+{re.escape(record)}\b"

            if re.search(
                pattern,
                procedure_text,
                flags=re.IGNORECASE,
            ):
                messages.append(
                    "Production validation: residual IDMS qualified record reference remains "
                    f"in PROCEDURE DIVISION: {record}"
                )

    def _validate_move_pic_length_mismatch(
        self,
        source_cobol_text: str,
        converted_cobol_text: str,
        messages: list[str],
    ) -> None:
        field_lengths = self._numeric_pic_lengths(
            "\n".join(
                [
                    source_cobol_text or "",
                    converted_cobol_text or "",
                ]
            )
        )

        if not field_lengths:
            return

        move_pairs = self._parse_move_pairs(
            converted_cobol_text,
        )

        for source, target in move_pairs:
            source_key = self._field_key(
                source,
            )
            target_key = self._field_key(
                target,
            )

            source_digits = field_lengths.get(
                source_key,
            )
            target_digits = field_lengths.get(
                target_key,
            )

            if not source_digits or not target_digits:
                continue

            if source_digits > target_digits:
                messages.append(
                    "Production validation: possible numeric PIC length mismatch. "
                    f"MOVE source {source_key} has {source_digits} digit(s), "
                    f"target {target_key} has {target_digits} digit(s)."
                )

    def _numeric_pic_lengths(
        self,
        text: str,
    ) -> dict[str, int]:
        output: dict[str, int] = {}

        for line in (text or "").splitlines():
            logical = self._logical_line(
                line,
            )

            match = self.DATA_FIELD_PATTERN.match(
                logical,
            )

            if not match:
                continue

            name = self._field_key(
                match.group("name"),
            )
            rest = match.group("rest") or ""

            pic_match = self.PIC_PATTERN.search(
                rest,
            )

            if not pic_match:
                continue

            pic = pic_match.group("pic") or ""
            explicit_length = pic_match.group("len")

            if explicit_length:
                try:
                    output[name] = int(
                        explicit_length,
                    )
                except ValueError:
                    continue
                continue

            digits = pic.count(
                "9",
            )

            if digits:
                output[name] = digits

        return output

    def _parse_move_pairs(
        self,
        text: str,
    ) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []

        normalized = re.sub(
            r"\s+",
            " ",
            text or "",
        )

        for match in self.MOVE_PATTERN.finditer(
            normalized,
        ):
            pairs.append(
                (
                    match.group("source"),
                    match.group("target"),
                )
            )

        return pairs

    def _field_key(
        self,
        value: str,
    ) -> str:
        text = str(
            value or "",
        ).strip()

        if "." in text:
            text = text.split(
                ".",
            )[-1]

        return NameNormalizer.to_cobol(
            text,
        )

    def _procedure_division_text(
        self,
        text: str,
    ) -> str:
        match = re.search(
            r"^\s*PROCEDURE\s+DIVISION\b.*\.\s*$",
            text or "",
            flags=re.IGNORECASE | re.MULTILINE,
        )

        if not match:
            return text or ""

        return (text or "")[
            match.start() :
        ]