import re

from idms_db2_phase2.domain.models import IdmsOperation


class CobolParser:
    PROGRAM_ID = re.compile(
        r"PROGRAM-ID\.\s+([A-Z0-9-]+)",
        re.IGNORECASE,
    )

    OBTAIN_CALC = re.compile(
        r"\bOBTAIN\s+(?:KEEP\s+)?([A-Z0-9-]+)\s+CALC\b",
        re.IGNORECASE,
    )

    OBTAIN_FIRST_NEXT = re.compile(
        r"\bOBTAIN\s+(FIRST|NEXT)\s+([A-Z0-9-]+)\s+WITHIN\s+([A-Z0-9-]+)\b",
        re.IGNORECASE,
    )

    OBTAIN_OWNER = re.compile(
        r"\bOBTAIN\s+OWNER\s+WITHIN\s+([A-Z0-9-]+)\b",
        re.IGNORECASE,
    )

    FIND_FIRST = re.compile(
        r"\bFIND\s+FIRST\s+([A-Z0-9-]+)?\s*WITHIN\s+([A-Z0-9-]+)\b",
        re.IGNORECASE,
    )

    STORE = re.compile(
        r"\bSTORE\s+([A-Z0-9-]+)\b",
        re.IGNORECASE,
    )

    MODIFY = re.compile(
        r"\bMODIFY\s+([A-Z0-9-]+)\b",
        re.IGNORECASE,
    )

    ERASE = re.compile(
        r"\bERASE\s+([A-Z0-9-]+)\b",
        re.IGNORECASE,
    )

    READY_UPDATE = re.compile(
        r"\bREADY\s+AREA\s+([A-Z0-9-]+).*UPDATE\b",
        re.IGNORECASE,
    )

    def program_id(self, cobol_text: str) -> str:
        match = self.PROGRAM_ID.search(cobol_text)

        if not match:
            return ""

        return match.group(1).upper()

    def analyze(self, cobol_text: str) -> list[IdmsOperation]:
        operations: list[IdmsOperation] = []

        for line_number, line in enumerate(cobol_text.splitlines(), start=1):
            upper = line.upper()

            match = self.OBTAIN_CALC.search(upper)

            if match:
                operations.append(
                    IdmsOperation(
                        operation="OBTAIN_CALC",
                        record_name=match.group(1).upper(),
                        line_number=line_number,
                        raw_line=line,
                    )
                )
                continue

            match = self.OBTAIN_FIRST_NEXT.search(upper)

            if match:
                operations.append(
                    IdmsOperation(
                        operation=f"OBTAIN_{match.group(1).upper()}",
                        record_name=match.group(2).upper(),
                        set_name=match.group(3).upper(),
                        line_number=line_number,
                        raw_line=line,
                    )
                )
                continue

            match = self.OBTAIN_OWNER.search(upper)

            if match:
                operations.append(
                    IdmsOperation(
                        operation="OBTAIN_OWNER",
                        set_name=match.group(1).upper(),
                        line_number=line_number,
                        raw_line=line,
                    )
                )
                continue

            match = self.FIND_FIRST.search(upper)

            if match:
                operations.append(
                    IdmsOperation(
                        operation="FIND_FIRST",
                        record_name=(match.group(1) or "").upper(),
                        set_name=match.group(2).upper(),
                        line_number=line_number,
                        raw_line=line,
                    )
                )
                continue

            match = self.STORE.search(upper)

            if match:
                operations.append(
                    IdmsOperation(
                        operation="STORE",
                        record_name=match.group(1).upper(),
                        line_number=line_number,
                        raw_line=line,
                    )
                )
                continue

            match = self.MODIFY.search(upper)

            if match:
                operations.append(
                    IdmsOperation(
                        operation="MODIFY",
                        record_name=match.group(1).upper(),
                        line_number=line_number,
                        raw_line=line,
                    )
                )
                continue

            match = self.ERASE.search(upper)

            if match:
                operations.append(
                    IdmsOperation(
                        operation="ERASE",
                        record_name=match.group(1).upper(),
                        line_number=line_number,
                        raw_line=line,
                    )
                )
                continue

            match = self.READY_UPDATE.search(upper)

            if match:
                operations.append(
                    IdmsOperation(
                        operation="READY_UPDATE",
                        record_name=match.group(1).upper(),
                        line_number=line_number,
                        raw_line=line,
                    )
                )

        return operations