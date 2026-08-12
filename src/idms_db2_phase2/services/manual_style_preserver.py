import re


class ManualStylePreserver:
    """
    Preserves manual COBOL style by avoiding broad formatting changes.

    Requirement:
    - Output should look similar to the original IDMS COBOL.
    - Only DB2 conversion changes should be carried out.
    - Existing comments should remain as-is where possible.

    This class performs safe cleanup only.
    """

    MULTIPLE_BLANK_LINES_PATTERN = re.compile(r"\n{4,}")

    def preserve(
        self,
        original_text: str,
        converted_text: str,
    ) -> str:
        if not converted_text:
            return ""

        text = converted_text

        text = self._preserve_comment_density(
            text=text,
        )

        text = self._normalize_excess_blank_lines(
            text=text,
        )

        return text.rstrip() + "\n"

    def _preserve_comment_density(
        self,
        text: str,
    ) -> str:
        output_lines: list[str] = []

        for line in text.splitlines():
            output_lines.append(line.rstrip())

        return "\n".join(output_lines)

    def _normalize_excess_blank_lines(
        self,
        text: str,
    ) -> str:
        return self.MULTIPLE_BLANK_LINES_PATTERN.sub("\n\n\n", text)