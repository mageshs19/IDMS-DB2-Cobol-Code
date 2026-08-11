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
    - Residual IDMS record references in PROCEDURE DIVISION.
    - MOVE source-to-target numeric PIC length mismatch.
    """

    FORBIDDEN_EXECUTABLE_PATTERNS = [
        r"^\s*BIND\b",
        r"^\s*READY\b",
        r"^\s*OBTAIN\b",
        r"^\s*FIND\s+CURRENT\b",
        r"^\s*FIND\s+FIRST\b",
        r"^\s*STORE\b",
        r"^\s*MODIFY\b",
        r"^\s*ERASE\b",
        r"^\s*CONNECT\b",
        r"^\s*DISCONNECT\b",
        r"^\s*PERFORM\s+[A-Z0-9-]*IDMS-STATUS\b",
        r"^\s*PERFORM\s+[A-Z0-9-]*IDMS-ABORT\b",
        r"^\s*USAGE-MODE\s+IS\s+UPDATE\b",
    ]

    REQUIRED_DB2_TOKENS = [
        "EXEC SQL",
        "SQLCA",
        "END-EXEC",
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

    def validate(
        self,
        source_cobol_text: str,
        converted_cobol_text: str,
        mapping_rows: list[SheetMappingRow],
        dclgen_columns: list[DclgenColumn] | None = None,
    ) -> list[str]:
        messages: list[str] = []
        dclgen_columns = dclgen_columns or []

        self._validate_required_db2_tokens(
            converted_cobol_text=converted_cobol_text,
            messages=messages,
        )

        self._validate_no_todo_host_variable(
            converted_cobol_text=converted_cobol_text,
            messages=messages,
        )

        self._validate_generated_dclgen_host_variables(
            converted_cobol_text=converted_cobol_text,
            dclgen_columns=dclgen_columns,
            messages=messages,
        )

        self._validate_forbidden_idms_patterns(
            converted_cobol_text=converted_cobol_text,
            messages=messages,
        )

        self._validate_residual_idms_record_references(
            converted_cobol_text=converted_cobol_text,
            mapping_rows=mapping_rows,
            messages=messages,
        )

        self._validate_move_pic_length_mismatch(
            source_cobol_text=source_cobol_text,
            converted_cobol_text=converted_cobol_text,
            messages=messages,
        )

        return messages

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

    def _validate_no_todo_host_variable(
        self,
        converted_cobol_text: str,
        messages: list[str],
    ) -> None:
        upper = converted_cobol_text.upper()

        if ":TODO-HOST-VARIABLE" in upper or ": TODO-HOST-VARIABLE" in upper:
            messages.append(
                "Production validation: generated COBOL still contains :TODO-HOST-VARIABLE."
            )

        if "TODO DB2" in upper:
            messages.append(
                "Production validation: generated COBOL still contains TODO DB2 items."
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

        for pattern in self.FORBIDDEN_EXECUTABLE_PATTERNS:
            if re.search(
                pattern,
                executable_text,
                flags=re.IGNORECASE | re.MULTILINE,
            ):
                messages.append(
                    f"Production validation: residual IDMS executable pattern remains: {pattern}"
                )

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
                    f"Production validation: residual IDMS qualified record reference remains in PROCEDURE DIVISION: {record}"
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

        procedure_text = self._procedure_division_text(
            converted_cobol_text,
        )

        move_pairs = self._parse_move_pairs(
            procedure_text,
        )

        for source, target in move_pairs:
            source_key = self._field_key(
                source,
            )
            target_key = self._field_key(
                target,
            )

            source_len = field_lengths.get(
                source_key,
            )
            target_len = field_lengths.get(
                target_key,
            )

            if source_len is None or target_len is None:
                continue

            if source_len > target_len:
                messages.append(
                    "Production validation: possible MOVE numeric PIC length mismatch: "
                    f"{source_key} length {source_len} moved to {target_key} length {target_len}."
                )

    def _numeric_pic_lengths(
        self,
        text: str,
    ) -> dict[str, int]:
        output: dict[str, int] = {}

        for line in text.splitlines():
            match = self.DATA_FIELD_PATTERN.search(
                line,
            )

            if not match:
                continue

            field_name = NameNormalizer.to_cobol(
                match.group("name"),
            )

            rest = match.group("rest") or ""

            pic_match = self.PIC_PATTERN.search(
                rest,
            )

            if not pic_match:
                continue

            length_text = pic_match.group("len")

            if length_text:
                output[field_name] = int(
                    length_text,
                )
                continue

            pic = pic_match.group("pic") or ""

            digits = len(
                re.findall(
                    r"9",
                    pic,
                    flags=re.IGNORECASE,
                )
            )

            if digits:
                output[field_name] = digits

        return output

    def _parse_move_pairs(
        self,
        text: str,
    ) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []

        normalized = re.sub(
            r"\s+",
            " ",
            text,
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
            r"^\s*PROCEDURE\s+DIVISION\.",
            text,
            flags=re.IGNORECASE | re.MULTILINE,
        )

        if not match:
            return text

        return text[
            match.start() :
        ]