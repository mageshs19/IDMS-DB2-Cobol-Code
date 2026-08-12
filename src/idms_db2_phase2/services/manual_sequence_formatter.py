import re


class ManualSequenceFormatter:
    """
    Applies manual-style COBOL sequence numbering.

    The training output shows two sequence regions:
    - Left sequence area, usually six digits.
    - Right sequence area, usually eight digits.

    Examples observed in manual output:
    - 000010 IDENTIFICATION DIVISION.        00020000
    - 000100 CBL ARITH(EXTEND)              00010000

    This formatter keeps the COBOL body text intact and only normalizes
    sequence numbers at the beginning and end of each physical line.
    """

    LEFT_SEQUENCE_PATTERN = re.compile(r"^\s*(?P<seq>\d{6})(?P<body>.*)$")
    RIGHT_SEQUENCE_PATTERN = re.compile(r"(?P<body>.*?)(?P<right>\d{8})\s*$")

    def format(
        self,
        text: str,
        left_start: int = 10,
        left_step: int = 10,
        right_start: int = 10000,
        right_step: int = 10000,
        preserve_blank_lines: bool = True,
    ) -> str:
        if not text:
            return ""

        output_lines: list[str] = []
        left_number = left_start
        right_number = right_start

        for raw_line in text.splitlines():
            if not raw_line.strip():
                if preserve_blank_lines:
                    output_lines.append("")
                continue

            body = self._strip_existing_sequence_numbers(raw_line)
            body = body.rstrip()

            left_seq = f"{left_number:06d}"
            right_seq = f"{right_number:08d}"

            output_lines.append(
                self._compose_line(
                    left_seq=left_seq,
                    body=body,
                    right_seq=right_seq,
                )
            )

            left_number += left_step
            right_number += right_step

        return "\n".join(output_lines).rstrip() + "\n"

    def detect_left_step(
        self,
        text: str,
    ) -> int:
        """
        Detects whether the uploaded/manual style is closer to:
        - 000010, 000020, 000030
        - 000100, 000200, 000300
        """
        numbers: list[int] = []

        for line in text.splitlines():
            match = self.LEFT_SEQUENCE_PATTERN.match(line)
            if not match:
                continue

            try:
                numbers.append(int(match.group("seq")))
            except ValueError:
                continue

            if len(numbers) >= 3:
                break

        if len(numbers) < 2:
            return 10

        differences: list[int] = []

        for index in range(1, len(numbers)):
            difference = numbers[index] - numbers[index - 1]
            if difference > 0:
                differences.append(difference)

        if not differences:
            return 10

        if 100 in differences:
            return 100

        return 10

    def _strip_existing_sequence_numbers(
        self,
        line: str,
    ) -> str:
        text = str(line or "").rstrip()

        right_match = self.RIGHT_SEQUENCE_PATTERN.match(text)
        if right_match:
            text = right_match.group("body").rstrip()

        left_match = self.LEFT_SEQUENCE_PATTERN.match(text)
        if left_match:
            text = left_match.group("body")

        return text.strip()

    def _compose_line(
        self,
        left_seq: str,
        body: str,
        right_seq: str,
    ) -> str:
        if not body:
            return f"{left_seq} {right_seq}"

        if len(body) >= 64:
            return f"{left_seq} {body} {right_seq}"

        return f"{left_seq} {body:<64} {right_seq}"