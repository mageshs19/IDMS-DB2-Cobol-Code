import re

from idms_db2_phase2.domain.models import SheetMappingRow
from idms_db2_phase2.services.name_normalizer import NameNormalizer


class ProductionValidator:
    """
    Performs production-focused validation for generated DB2 COBOL.

    It detects:
        - Undefined TODO host variables.
        - Residual IDMS control logic.
        - Residual IDMS record field references.
        - Source-to-output PIC length mismatch in MOVE statements.
        - Missing required DB2 constructs.
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

    def validate(
        self,
        source_cobol_text: str,
        converted_cobol_text: str,
        mapping_rows: list[SheetMappingRow],
    ) -> list[str]:
        messages: list[str] = []

        self._validate_required_db2_tokens(
            converted_cobol_text=converted_cobol_text,
            messages=messages,
        )

        self._validate_no_todo_host_variable(
            converted_cobol_text=converted_cobol_text,
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
        if ":TODO-HOST-VARIABLE" in converted_cobol_text.upper():
            messages.append(
                "Production validation: generated COBOL still contains :TODO-HOST-VARIABLE."
            )

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
        record_names = {
            NameNormalizer.to_cobol(
                row.cobol_record_idms,
            )
            for row in mapping_rows
            if row.cobol_record_idms
        }

        record_names = {
            record
            for record in record_names
            if record
        }

        if not record_names:
            return

        record_pattern = "|".join(
            re.escape(
                record,
            )
            for record in sorted(
                record_names,
                key=len,
                reverse=True,
            )
        )

        pattern = rf"\b[A-Z][A-Z0-9-]*\s+(?:OF|IN)\s+(?:{record_pattern})\b"

        matches = sorted(
            set(
                re.findall(
                    pattern,
                    converted_cobol_text,
                    flags=re.IGNORECASE,
                )
            )
        )

        for match in matches:
            messages.append(
                f"Production validation: residual IDMS record reference remains: {match}"
            )

    def _validate_move_pic_length_mismatch(
        self,
        source_cobol_text: str,
        converted_cobol_text: str,
        messages: list[str],
    ) -> None:
        all_text = "\n".join(
            [
                source_cobol_text or "",
                converted_cobol_text or "",
            ]
        )

        pic_lengths = self._parse_pic_digit_lengths(
            all_text,
        )

        if not pic_lengths:
            return

        move_pairs = self._parse_move_pairs(
            all_text,
        )

        for source_name, target_name in move_pairs:
            source_key = NameNormalizer.to_cobol(
                source_name,
            )

            target_key = NameNormalizer.to_cobol(
                target_name,
            )

            source_digits = pic_lengths.get(
                source_key,
            )

            target_digits = pic_lengths.get(
                target_key,
            )

            if source_digits is None or target_digits is None:
                continue

            if source_digits > target_digits:
                messages.append(
                    "Production validation: possible data truncation: "
                    f"MOVE {source_key} PIC digits {source_digits} "
                    f"TO {target_key} PIC digits {target_digits}."
                )

    def _parse_pic_digit_lengths(
        self,
        text: str,
    ) -> dict[str, int]:
        result: dict[str, int] = {}

        entries = self._collect_cobol_data_entries(
            text,
        )

        for name, body in entries:
            digits = self._pic_digits(
                body,
            )

            if digits is None:
                continue

            result[
                NameNormalizer.to_cobol(
                    name,
                )
            ] = digits

        return result

    def _collect_cobol_data_entries(
        self,
        text: str,
    ) -> list[tuple[str, str]]:
        lines = text.splitlines()
        entries: list[tuple[str, str]] = []
        index = 0

        start_pattern = re.compile(
            r"^\s*(?:0[1-9]|[1-4][0-9]|77)\s+([A-Z][A-Z0-9-]*)\b(.*)$",
            re.IGNORECASE,
        )

        while index < len(lines):
            line = lines[index]
            match = start_pattern.match(
                line,
            )

            if not match:
                index += 1
                continue

            name = match.group(
                1,
            )

            parts = [
                match.group(
                    2,
                )
                or ""
            ]

            lookahead = index + 1

            while lookahead < len(lines):
                next_line = lines[lookahead]
                next_match = start_pattern.match(
                    next_line,
                )

                if next_match:
                    break

                parts.append(
                    next_line.strip(),
                )

                if "." in next_line:
                    lookahead += 1
                    break

                lookahead += 1

            body = " ".join(
                parts,
            )

            entries.append(
                (
                    name,
                    body,
                )
            )

            index = max(
                lookahead,
                index + 1,
            )

        return entries

    def _pic_digits(
        self,
        text: str,
    ) -> int | None:
        match = re.search(
            r"\bPIC(?:TURE)?\s+(?:IS\s+)?([A-Z0-9()VXS+-]+)",
            text,
            flags=re.IGNORECASE,
        )

        if not match:
            return None

        pic = match.group(
            1,
        ).upper()

        if "X" in pic:
            x_match = re.search(
                r"X$(\d+)$",
                pic,
            )

            if x_match:
                return int(
                    x_match.group(
                        1,
                    )
                )

            if pic == "X":
                return 1

            return None

        total_digits = 0

        for digit_match in re.finditer(
            r"9(?:$(\d+)$)?",
            pic,
        ):
            if digit_match.group(
                1,
            ):
                total_digits += int(
                    digit_match.group(
                        1,
                    )
                )
            else:
                total_digits += 1

        if total_digits:
            return total_digits

        return None

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

        for match in re.finditer(
            r"\bMOVE\s+([A-Z0-9-]+)(?:\s+OF\s+[A-Z0-9-]+)?\s+TO\s+([A-Z0-9-]+)\b",
            normalized,
            flags=re.IGNORECASE,
        ):
            pairs.append(
                (
                    match.group(
                        1,
                    ),
                    match.group(
                        2,
                    ),
                )
            )

        return pairs

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