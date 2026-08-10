import re

from idms_db2_phase2.domain.models import CopybookField


class CopybookParser:
    FIELD = re.compile(
        r"^\s*(\d{2})\s+([A-Z0-9-]+)"
        r"(?:\s+PIC\s+([^\.]+?))?"
        r"(?:\s+USAGE\s+([^\.]+?))?"
        r"\.",
        re.IGNORECASE,
    )

    OCCURS = re.compile(
        r"OCCURS\s+([0-9]+)\s+TIMES",
        re.IGNORECASE,
    )

    def parse(self, text: str) -> list[CopybookField]:
        if not text.strip():
            return []

        output: list[CopybookField] = []

        for line in text.splitlines():
            match = self.FIELD.search(line)

            if not match:
                continue

            occurs = ""
            occurs_match = self.OCCURS.search(line)

            if occurs_match:
                occurs = occurs_match.group(1)

            output.append(
                CopybookField(
                    level=match.group(1),
                    name=match.group(2).upper(),
                    picture=(match.group(3) or "").strip(),
                    usage=(match.group(4) or "").strip(),
                    occurs=occurs,
                )
            )

        return output