import re

from idms_db2_phase2.domain.models import DclgenColumn


class DclgenParser:
    DECLARE_TABLE = re.compile(
        r"EXEC\s+SQL\s+DECLARE\s+([A-Z0-9_]+)\s+TABLE",
        re.IGNORECASE,
    )

    SQL_COLUMN = re.compile(
        r"^\s*([A-Z0-9_]+)\s+([A-Z]+(?:\s*$[0-9,\s]+$)?)",
        re.IGNORECASE,
    )

    COBOL_FIELD = re.compile(
        r"^\s*\d{2}\s+([A-Z0-9-]+)\s+PIC\s+(.+?)\.",
        re.IGNORECASE,
    )

    def parse_many_texts(self, texts: list[str]) -> list[DclgenColumn]:
        output: list[DclgenColumn] = []

        for text in texts:
            output.extend(self.parse(text))

        return output

    def parse(self, text: str) -> list[DclgenColumn]:
        if not text.strip():
            return []

        table_name = self._table_name(text)
        sql_columns = self._sql_columns(text)
        cobol_fields = self._cobol_fields(text)

        output: list[DclgenColumn] = []

        for index, column in enumerate(sql_columns):
            cobol_host_name = ""
            cobol_picture = ""

            if index < len(cobol_fields):
                cobol_host_name = cobol_fields[index]["name"]
                cobol_picture = cobol_fields[index]["picture"]

            output.append(
                DclgenColumn(
                    table_name=table_name,
                    column_name=column["name"],
                    db2_type=column["type"],
                    cobol_host_name=cobol_host_name,
                    cobol_picture=cobol_picture,
                    nullable=True,
                )
            )

        return output

    def _table_name(self, text: str) -> str:
        match = self.DECLARE_TABLE.search(text)

        if not match:
            return ""

        return match.group(1).upper()

    def _sql_columns(self, text: str) -> list[dict[str, str]]:
        output: list[dict[str, str]] = []
        inside = False

        for line in text.splitlines():
            upper = line.upper()

            if "DECLARE" in upper and "TABLE" in upper:
                inside = True
                continue

            if inside and ")" in line:
                inside = False
                continue

            if not inside:
                continue

            match = self.SQL_COLUMN.search(line)

            if not match:
                continue

            name = match.group(1).upper()
            db2_type = match.group(2).upper().replace(" ", "")

            if name in {"EXEC", "SQL", "DECLARE"}:
                continue

            output.append(
                {
                    "name": name,
                    "type": db2_type,
                }
            )

        return output

    def _cobol_fields(self, text: str) -> list[dict[str, str]]:
        output: list[dict[str, str]] = []

        for line in text.splitlines():
            match = self.COBOL_FIELD.search(line)

            if not match:
                continue

            output.append(
                {
                    "name": match.group(1).upper(),
                    "picture": match.group(2).strip(),
                }
            )

        return output