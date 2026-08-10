import re

from idms_db2_phase2.services.name_normalizer import NameNormalizer


class PicLengthAutoFixer:
    """
    Auto-fixes target COBOL PIC lengths when a MOVE source has more digits
    than the MOVE target.

    Example:
        20 WS-NR-CIO-CRE  PIC 9(8) COMP-3.
        03 UIT-NR-CIO-CRE PIC 9(6).

        MOVE WS-NR-CIO-CRE TO UIT-NR-CIO-CRE

    Becomes:
        03 UIT-NR-CIO-CRE PIC 9(8).

    Scope:
        - Numeric PIC 9(n) and S9(n) are supported.
        - Only target fields used in MOVE statements are changed.
        - This is generic and does not hardcode CIO or any business field.
    """

    DATA_ENTRY_START = re.compile(
        r"^\s*(?P<level>0[1-9]|[1-4][0-9]|77)\s+"
        r"(?P<name>[A-Z][A-Z0-9-]*)\b"
        r"(?P<rest>.*)$",
        re.IGNORECASE,
    )

    PIC_PATTERN = re.compile(
        r"\bPIC(?:TURE)?\s+(?:IS\s+)?(?P<pic>S?9(?:$\d+$)?)(?P<trailing>[^.\n]*)(?P<dot>\.)?",
        re.IGNORECASE,
    )

    MOVE_PATTERN = re.compile(
        r"\bMOVE\s+"
        r"(?P<source>[A-Z][A-Z0-9-]*(?:\.[A-Z][A-Z0-9-]*)?)"
        r"(?:\s+OF\s+[A-Z][A-Z0-9-]*)?"
        r"\s+TO\s+"
        r"(?P<target>[A-Z][A-Z0-9-]*)\b",
        re.IGNORECASE,
    )

    def __init__(
        self,
    ) -> None:
        self.messages: list[str] = []

    def fix(
        self,
        source_cobol_text: str,
        converted_cobol_text: str,
    ) -> str:
        self.messages = []

        if not converted_cobol_text:
            return converted_cobol_text

        combined_text = "\n".join(
            [
                source_cobol_text or "",
                converted_cobol_text or "",
            ]
        )

        pic_lengths = self._parse_numeric_pic_lengths(
            combined_text,
        )

        if not pic_lengths:
            self.messages.append(
                "Auto-fix PIC: no numeric PIC fields found."
            )
            return converted_cobol_text

        move_pairs = self._parse_move_pairs(
            converted_cobol_text,
        )

        if not move_pairs:
            self.messages.append(
                "Auto-fix PIC: no MOVE source/target pairs found."
            )
            return converted_cobol_text

        fixes: dict[str, int] = {}

        for source_name, target_name in move_pairs:
            source_key = self._normalize_move_identifier(
                source_name,
            )

            target_key = self._normalize_move_identifier(
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
                existing_fix = fixes.get(
                    target_key,
                    0,
                )

                fixes[target_key] = max(
                    existing_fix,
                    source_digits,
                )

                self.messages.append(
                    "Auto-fix PIC: target field requires expansion: "
                    f"{target_key} {target_digits} -> {source_digits} "
                    f"because of MOVE from {source_key}."
                )

        if not fixes:
            self.messages.append(
                "Auto-fix PIC: no target PIC length mismatches found."
            )
            return converted_cobol_text

        fixed_text = self._apply_fixes(
            converted_cobol_text=converted_cobol_text,
            fixes=fixes,
        )

        return fixed_text

    def _parse_numeric_pic_lengths(
        self,
        text: str,
    ) -> dict[str, int]:
        entries = self._collect_data_entries(
            text,
        )

        result: dict[str, int] = {}

        for name, body in entries:
            digits = self._numeric_pic_digits(
                body,
            )

            if digits is None:
                continue

            normalized_name = self._normalize_move_identifier(
                name,
            )

            result[normalized_name] = digits

        return result

    def _collect_data_entries(
        self,
        text: str,
    ) -> list[tuple[str, str]]:
        lines = text.splitlines()
        entries: list[tuple[str, str]] = []

        index = 0

        while index < len(lines):
            line = lines[index]

            match = self.DATA_ENTRY_START.match(
                line,
            )

            if not match:
                index += 1
                continue

            name = match.group(
                "name",
            )

            parts = [
                match.group(
                    "rest",
                )
                or ""
            ]

            lookahead = index + 1

            while lookahead < len(lines):
                next_line = lines[lookahead]

                if self.DATA_ENTRY_START.match(
                    next_line,
                ):
                    break

                stripped_next = next_line.strip()

                if stripped_next:
                    parts.append(
                        stripped_next,
                    )

                if "." in stripped_next:
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

    def _numeric_pic_digits(
        self,
        text: str,
    ) -> int | None:
        match = self.PIC_PATTERN.search(
            text,
        )

        if not match:
            return None

        pic = match.group(
            "pic",
        ).upper()

        if not re.fullmatch(
            r"S?9(?:$\d+$)?",
            pic,
            flags=re.IGNORECASE,
        ):
            return None

        paren_match = re.search(
            r"$(\d+)$",
            pic,
        )

        if paren_match:
            return int(
                paren_match.group(
                    1,
                )
            )

        return 1

    def _parse_move_pairs(
        self,
        text: str,
    ) -> list[tuple[str, str]]:
        normalized_text = re.sub(
            r"\s+",
            " ",
            text,
        )

        pairs: list[tuple[str, str]] = []

        for match in self.MOVE_PATTERN.finditer(
            normalized_text,
        ):
            source_name = match.group(
                "source",
            )

            target_name = match.group(
                "target",
            )

            pairs.append(
                (
                    source_name,
                    target_name,
                )
            )

        return pairs

    def _apply_fixes(
        self,
        converted_cobol_text: str,
        fixes: dict[str, int],
    ) -> str:
        lines = converted_cobol_text.splitlines()
        output_lines: list[str] = []

        index = 0

        while index < len(lines):
            line = lines[index]

            match = self.DATA_ENTRY_START.match(
                line,
            )

            if not match:
                output_lines.append(
                    line,
                )
                index += 1
                continue

            field_name = match.group(
                "name",
            )

            normalized_field_name = self._normalize_move_identifier(
                field_name,
            )

            required_digits = fixes.get(
                normalized_field_name,
            )

            if required_digits is None:
                output_lines.append(
                    line,
                )
                index += 1
                continue

            entry_lines = [
                line,
            ]

            lookahead = index + 1

            while lookahead < len(lines):
                next_line = lines[lookahead]

                if self.DATA_ENTRY_START.match(
                    next_line,
                ):
                    break

                entry_lines.append(
                    next_line,
                )

                if "." in next_line:
                    lookahead += 1
                    break

                lookahead += 1

            fixed_entry_lines = self._fix_entry_lines(
                field_name=normalized_field_name,
                entry_lines=entry_lines,
                required_digits=required_digits,
            )

            output_lines.extend(
                fixed_entry_lines,
            )

            index = max(
                lookahead,
                index + 1,
            )

        return "\n".join(
            output_lines,
        )

    def _fix_entry_lines(
        self,
        field_name: str,
        entry_lines: list[str],
        required_digits: int,
    ) -> list[str]:
        fixed_lines: list[str] = []
        fixed = False

        for line in entry_lines:
            if fixed:
                fixed_lines.append(
                    line,
                )
                continue

            if "PIC" not in line.upper() and "PICTURE" not in line.upper():
                fixed_lines.append(
                    line,
                )
                continue

            fixed_line = self._replace_pic_digits(
                line=line,
                required_digits=required_digits,
            )

            if fixed_line != line:
                self.messages.append(
                    f"Auto-fix PIC: changed {field_name} declaration to PIC 9({required_digits})."
                )
                fixed = True

            fixed_lines.append(
                fixed_line,
            )

        if not fixed:
            fixed_lines = self._fix_multiline_pic(
                field_name=field_name,
                entry_lines=fixed_lines,
                required_digits=required_digits,
            )

        return fixed_lines

    def _fix_multiline_pic(
        self,
        field_name: str,
        entry_lines: list[str],
        required_digits: int,
    ) -> list[str]:
        output: list[str] = []

        fixed = False

        for line in entry_lines:
            if fixed:
                output.append(
                    line,
                )
                continue

            if re.search(
                r"\bPIC(?:TURE)?\b",
                line,
                flags=re.IGNORECASE,
            ):
                fixed_line = self._replace_pic_digits(
                    line=line,
                    required_digits=required_digits,
                )

                if fixed_line != line:
                    self.messages.append(
                        f"Auto-fix PIC: changed {field_name} declaration to PIC 9({required_digits})."
                    )
                    fixed = True

                output.append(
                    fixed_line,
                )
                continue

            output.append(
                line,
            )

        return output

    def _replace_pic_digits(
        self,
        line: str,
        required_digits: int,
    ) -> str:
        def repl(
            match: re.Match,
        ) -> str:
            pic = match.group(
                "pic",
            )

            trailing = match.group(
                "trailing",
            ) or ""

            dot = match.group(
                "dot",
            ) or ""

            sign_prefix = "S" if pic.upper().startswith("S") else ""

            new_pic = f"{sign_prefix}9({required_digits})"

            return f"PIC {new_pic}{trailing}{dot}"

        return self.PIC_PATTERN.sub(
            repl,
            line,
            count=1,
        )

    def _normalize_move_identifier(
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