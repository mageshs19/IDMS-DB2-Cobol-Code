from io import BytesIO

import pdfplumber


class TextLoader:
    def read_uploaded_text(
        self,
        uploaded_file,
    ) -> str:
        if uploaded_file is None:
            return ""

        raw_bytes = uploaded_file.getvalue()
        file_name = str(uploaded_file.name or "").lower()

        if file_name.endswith(".pdf"):
            return self.read_pdf_bytes(
                raw_bytes,
            )

        return raw_bytes.decode(
            "utf-8",
            errors="ignore",
        )

    def read_pdf_bytes(
        self,
        raw_bytes: bytes,
    ) -> str:
        lines: list[str] = []

        with pdfplumber.open(
            BytesIO(raw_bytes),
        ) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""

                for raw_line in page_text.splitlines():
                    cleaned_line = self._clean_line(
                        raw_line,
                    )

                    if cleaned_line:
                        lines.append(
                            cleaned_line,
                        )

        return "\n".join(
            lines,
        ).strip()

    def _clean_line(
        self,
        line: str | None,
    ) -> str:
        if line is None:
            return ""

        cleaned = str(line).rstrip()
        cleaned = cleaned.replace(
            "\t",
            " ",
        )
        cleaned = cleaned.replace(
            "\u00a0",
            " ",
        )

        while "  " in cleaned:
            cleaned = cleaned.replace(
                "  ",
                " ",
            )

        return cleaned