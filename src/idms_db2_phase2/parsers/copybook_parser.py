import re

from idms_db2_phase2.domain.models import CopybookField


class CopybookParser:
    FIELD = re.compile(
        r"^\s*(?P<level>0[1-9]|[1-4][0-9]|66|77|88)\s+"
        r"(?P<name>[A-Z0-9-]+)"
        r"(?P<rest>.*)$",
        re.IGNORECASE,
    )

    PIC = re.compile(
        r"\bPIC(?:TURE)?\s+"
        r"(?P<pic>[SXA9VZ0-9\(\)\+\-\.,/]+)",
        re.IGNORECASE,
    )

    USAGE = re.compile(
        r"\b(?:USAGE\s+IS\s+|USAGE\s+)?"
        r"(?P<usage>COMP-3|COMPUTATIONAL-3|COMP|COMPUTATIONAL|BINARY|DISPLAY|PACKED-DECIMAL)\b",
        re.IGNORECASE,
    )

    OCCURS = re.compile(
        r"\bOCCURS\s+(?P<occurs>[0-9]+)\s+(?:TIMES\b)?",
        re.IGNORECASE,
    )

    COMMENT_OR_SKIP = re.compile(
        r"^\s*(?:\*|/|EJECT\b|SKIP[0-9]*\b)",
        re.IGNORECASE,
    )

    def parse(
        self,
        text: str,
    ) -> list[CopybookField]:
        if not str(text or "").strip():
            return []

        output: list[CopybookField] = []
        logical_lines = self._logical_lines(text)

        for line in logical_lines:
            match = self.FIELD.search(line)

            if not match:
                continue

            name = str(match.group("name") or "").strip().upper()
            rest = str(match.group("rest") or "")

            if not name:
                continue

            if name == "FILLER":
                continue

            pic = ""
            pic_match = self.PIC.search(rest)

            if pic_match:
                pic = str(pic_match.group("pic") or "").strip().upper()

            usage = ""
            usage_match = self.USAGE.search(rest)

            if usage_match:
                usage = str(usage_match.group("usage") or "").strip().upper()

            occurs = ""
            occurs_match = self.OCCURS.search(rest)

            if occurs_match:
                occurs = str(occurs_match.group("occurs") or "").strip()

            output.append(
                CopybookField(
                    level=str(match.group("level") or "").strip(),
                    name=name,
                    picture=pic,
                    usage=usage,
                    occurs=occurs,
                )
            )

        return output

    def _logical_lines(
        self,
        text: str,
    ) -> list[str]:
        output: list[str] = []
        buffer = ""

        for raw_line in str(text or "").splitlines():
            line = self._strip_sequence_area(raw_line).rstrip()

            if not line.strip():
                continue

            if self.COMMENT_OR_SKIP.search(line):
                continue

            if buffer:
                buffer = f"{buffer} {line.strip()}"
            else:
                buffer = line.strip()

            if "." in line:
                parts = buffer.split(".")

                for part in parts[:-1]:
                    clean = part.strip()

                    if clean:
                        output.append(clean + ".")

                buffer = parts[-1].strip()

        if buffer.strip():
            output.append(buffer.strip())

        return output

    def _strip_sequence_area(
        self,
        line: str,
    ) -> str:
        text = str(line or "").rstrip()

        if len(text) > 6:
            indicator = text[6:7]

            if indicator in ("*", "/"):
                return indicator

            if text[:6].strip().isdigit():
                return text[6:]

        return text