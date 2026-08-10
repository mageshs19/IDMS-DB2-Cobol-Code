import re

from idms_db2_phase2.domain.models import DclgenColumn


class DclgenParser:
    DECLARE_TABLE = re.compile(
        r"DECLARE\s+([A-Z0-9_.$#@]+)\s+TABLE",
        re.IGNORECASE,
    )

    DCLGEN_TABLE = re.compile(
        r"\bTABLE\s*$\s*([A-Z0-9_.$#@]+)\s*$",
        re.IGNORECASE,
    )

    COLUMN_LINE = re.compile(
        r"^\s*([A-Z][A-Z0-9_@$#]*)\s+(.+?)(?:,)?\s*$",
        re.IGNORECASE,
    )

    COBOL_FIELD_LINE = re.compile(
        r"^\s*(0[1-9]|[1-4][0-9]|77)\s+([A-Z][A-Z0-9-]*)\b(.*)$",
        re.IGNORECASE,
    )

    PIC_PATTERN = re.compile(
        r"\bPIC(?:TURE)?\s+(?:IS\s+)?(.+?)(?:\.|$)",
        re.IGNORECASE,
    )

    USAGE_PATTERN = re.compile(
        r"\bUSAGE\s+(?:IS\s+)?([A-Z0-9-]+)",
        re.IGNORECASE,
    )

    SQL_DATATYPE_STARTERS = {
        "CHAR",
        "CHARACTER",
        "VARCHAR",
        "LONG",
        "GRAPHIC",
        "VARGRAPHIC",
        "SMALLINT",
        "INTEGER",
        "INT",
        "BIGINT",
        "DECIMAL",
        "DEC",
        "NUMERIC",
        "NUM",
        "FLOAT",
        "REAL",
        "DOUBLE",
        "DATE",
        "TIME",
        "TIMESTAMP",
        "BLOB",
        "CLOB",
        "DBCLOB",
    }

    COLUMN_EXCLUDE_WORDS = {
        "EXEC",
        "SQL",
        "DECLARE",
        "CREATE",
        "TABLE",
        "PRIMARY",
        "FOREIGN",
        "CONSTRAINT",
        "UNIQUE",
        "CHECK",
        "REFERENCES",
        "END-EXEC",
        "END",
        "DSNH",
        "DCLGEN",
    }

    def __init__(
        self,
    ) -> None:
        self.diagnostics: list[str] = []

    def parse_many_texts(
        self,
        texts: list[str],
    ) -> list[DclgenColumn]:
        self.diagnostics = []
        output: list[DclgenColumn] = []

        self.diagnostics.append(
            f"DCLGEN input file count: {len(texts)}"
        )

        for index, text in enumerate(texts, start=1):
            self.diagnostics.append(
                f"DCLGEN file {index} input length: {len(text or '')}"
            )

            parsed = self.parse(
                text=text,
                source_label=f"DCLGEN file {index}",
            )

            self.diagnostics.append(
                f"DCLGEN file {index} parsed columns: {len(parsed)}"
            )

            output.extend(
                parsed,
            )

        self.diagnostics.append(
            f"DCLGEN total parsed columns: {len(output)}"
        )

        return output

    def parse(
        self,
        text: str,
        source_label: str = "DCLGEN",
    ) -> list[DclgenColumn]:
        if not text or not text.strip():
            self.diagnostics.append(
                f"{source_label}: empty text."
            )
            return []

        cleaned_text = self._clean_text(
            text,
        )

        table_name = self._find_table_name(
            cleaned_text,
        )

        self.diagnostics.append(
            f"{source_label}: detected table name: {table_name}"
        )

        sql_columns = self._find_sql_columns(
            text=cleaned_text,
            source_label=source_label,
        )

        self.diagnostics.append(
            f"{source_label}: SQL columns found: {len(sql_columns)}"
        )

        cobol_fields = self._find_cobol_fields(
            text=cleaned_text,
            source_label=source_label,
        )

        self.diagnostics.append(
            f"{source_label}: COBOL PIC fields found: {len(cobol_fields)}"
        )

        output: list[DclgenColumn] = []

        for index, column in enumerate(sql_columns):
            cobol_host_name = ""
            cobol_picture = ""
            cobol_usage = ""

            if index < len(cobol_fields):
                cobol_host_name = cobol_fields[index]["name"]
                cobol_picture = cobol_fields[index]["picture"]
                cobol_usage = cobol_fields[index]["usage"]

            output.append(
                DclgenColumn(
                    table_name=table_name,
                    column_name=column["name"],
                    db2_type=column["type"],
                    cobol_host_name=cobol_host_name,
                    cobol_picture=cobol_picture,
                    cobol_usage=cobol_usage,
                    nullable=column["nullable"],
                )
            )

        if output:
            return output

        fallback = self._fallback_columns_from_cobol_fields(
            table_name=table_name,
            cobol_fields=cobol_fields,
        )

        self.diagnostics.append(
            f"{source_label}: fallback columns from COBOL fields: {len(fallback)}"
        )

        return fallback

    def _clean_text(
        self,
        text: str,
    ) -> str:
        value = str(
            text or "",
        )

        value = value.replace(
            "\ufeff",
            "",
        )
        value = value.replace(
            "\u00a0",
            " ",
        )
        value = value.replace(
            "\r\n",
            "\n",
        )
        value = value.replace(
            "\r",
            "\n",
        )

        return value

    def _find_table_name(
        self,
        text: str,
    ) -> str:
        match = self.DECLARE_TABLE.search(
            text,
        )

        if match:
            return self._normalize_sql_name(
                match.group(
                    1,
                )
            )

        match = self.DCLGEN_TABLE.search(
            text,
        )

        if match:
            return self._normalize_sql_name(
                match.group(
                    1,
                )
            )

        return ""

    def _find_sql_columns(
        self,
        text: str,
        source_label: str,
    ) -> list[dict[str, str]]:
        declare_body = self._extract_declare_table_body(
            text=text,
            source_label=source_label,
        )

        if not declare_body:
            return []

        output: list[dict[str, str]] = []

        logical_lines = self._join_sql_lines(
            declare_body,
        )

        self.diagnostics.append(
            f"{source_label}: SQL logical lines in DECLARE TABLE: {len(logical_lines)}"
        )

        for line in logical_lines:
            column = self._parse_sql_column_line(
                line,
            )

            if column is None:
                continue

            output.append(
                column,
            )

        return output

    def _extract_declare_table_body(
        self,
        text: str,
        source_label: str,
    ) -> str:
        declare_match = self.DECLARE_TABLE.search(
            text,
        )

        if not declare_match:
            self.diagnostics.append(
                f"{source_label}: DECLARE TABLE block not found."
            )
            return ""

        start_position = declare_match.end()
        remaining = text[start_position:]

        open_paren_position = remaining.find(
            "(",
        )

        if open_paren_position < 0:
            self.diagnostics.append(
                f"{source_label}: DECLARE TABLE opening parenthesis not found."
            )
            return ""

        body_start = open_paren_position + 1
        depth = 1
        index = body_start

        while index < len(remaining):
            char = remaining[index]

            if char == "(":
                depth += 1

            elif char == ")":
                depth -= 1

                if depth == 0:
                    body = remaining[body_start:index]
                    self.diagnostics.append(
                        f"{source_label}: DECLARE TABLE body length: {len(body)}"
                    )
                    return body

            index += 1

        body = remaining[body_start:]
        self.diagnostics.append(
            f"{source_label}: DECLARE TABLE body used until EOF length: {len(body)}"
        )

        return body

    def _join_sql_lines(
        self,
        body: str,
    ) -> list[str]:
        logical_lines: list[str] = []
        current_parts: list[str] = []

        for raw_line in body.splitlines():
            line = raw_line.strip()

            if not line:
                continue

            if line.startswith("--"):
                continue

            if line.startswith("*"):
                continue

            line = re.sub(
                r"\s+",
                " ",
                line,
            ).strip()

            current_parts.append(
                line,
            )

            if line.endswith(","):
                logical_lines.append(
                    " ".join(
                        current_parts,
                    ).rstrip(",").strip()
                )

                current_parts = []

        if current_parts:
            logical_lines.append(
                " ".join(
                    current_parts,
                ).rstrip(",").strip()
            )

        return logical_lines

    def _parse_sql_column_line(
        self,
        line: str,
    ) -> dict[str, str] | None:
        if not line:
            return None

        stripped = line.strip().rstrip(",")

        parts = stripped.split()

        if not parts:
            return None

        first_word = parts[0].upper()

        if first_word in self.COLUMN_EXCLUDE_WORDS:
            return None

        match = self.COLUMN_LINE.match(
            stripped,
        )

        if not match:
            return None

        column_name = self._normalize_sql_name(
            match.group(
                1,
            )
        )

        datatype_text = match.group(
            2,
        ).strip()

        if not column_name:
            return None

        if column_name.upper() in self.COLUMN_EXCLUDE_WORDS:
            return None

        datatype = self._extract_datatype(
            datatype_text,
        )

        if not datatype:
            return None

        nullable = not self._contains_not_null(
            datatype_text,
        )

        return {
            "name": column_name,
            "type": datatype,
            "nullable": nullable,
        }

    def _extract_datatype(
        self,
        datatype_text: str,
    ) -> str:
        text = str(
            datatype_text or "",
        ).strip()

        text = text.rstrip(
            ",",
        )

        text = re.sub(
            r"\s+",
            " ",
            text,
        )

        if not text:
            return ""

        tokens = text.split()

        if not tokens:
            return ""

        first = tokens[0].upper()

        first_base = first.split(
            "(",
            1,
        )[0].upper()

        if first_base not in self.SQL_DATATYPE_STARTERS:
            return ""

        datatype_parts: list[str] = []

        for token in tokens:
            upper_token = token.upper()

            if upper_token in {
                "NOT",
                "NULL",
                "WITH",
                "DEFAULT",
                "GENERATED",
                "IDENTITY",
                "PRIMARY",
                "REFERENCES",
                "CONSTRAINT",
                "CHECK",
            }:
                break

            datatype_parts.append(
                token,
            )

        datatype = " ".join(
            datatype_parts,
        )

        datatype = datatype.replace(
            " ",
            "",
        )

        return datatype.upper()

    def _contains_not_null(
        self,
        text: str,
    ) -> bool:
        return bool(
            re.search(
                r"\bNOT\s+NULL\b",
                text,
                flags=re.IGNORECASE,
            )
        )

    def _find_cobol_fields(
        self,
        text: str,
        source_label: str,
    ) -> list[dict[str, str]]:
        output: list[dict[str, str]] = []

        lines = text.splitlines()
        index = 0

        while index < len(lines):
            line = lines[index].rstrip()

            match = self.COBOL_FIELD_LINE.match(
                line,
            )

            if not match:
                index += 1
                continue

            level = match.group(
                1,
            )

            name = match.group(
                2,
            ).upper()

            rest = match.group(
                3,
            ) or ""

            continuation_lines: list[str] = [
                rest,
            ]

            lookahead = index + 1

            while lookahead < len(lines):
                next_line = lines[lookahead].rstrip()
                next_match = self.COBOL_FIELD_LINE.match(
                    next_line,
                )

                if next_match:
                    break

                stripped_next = next_line.strip()

                if not stripped_next:
                    lookahead += 1
                    continue

                if stripped_next.startswith("*"):
                    lookahead += 1
                    continue

                continuation_lines.append(
                    stripped_next,
                )

                if stripped_next.endswith("."):
                    lookahead += 1
                    break

                lookahead += 1

            combined = " ".join(
                continuation_lines,
            )

            picture = self._extract_picture(
                combined,
            )

            usage = self._extract_usage(
                combined,
            )

            if picture:
                output.append(
                    {
                        "level": level,
                        "name": name,
                        "picture": picture,
                        "usage": usage,
                    }
                )

            index = max(
                lookahead,
                index + 1,
            )

        if output:
            self.diagnostics.append(
                f"{source_label}: first COBOL field: {output[0]}"
            )

        return output

    def _extract_picture(
        self,
        text: str,
    ) -> str:
        match = self.PIC_PATTERN.search(
            text,
        )

        if not match:
            return ""

        picture = match.group(
            1,
        ).strip()

        picture = picture.rstrip(
            ".",
        ).strip()

        return picture

    def _extract_usage(
        self,
        text: str,
    ) -> str:
        match = self.USAGE_PATTERN.search(
            text,
        )

        if not match:
            return ""

        return match.group(
            1,
        ).strip().upper()

    def _fallback_columns_from_cobol_fields(
        self,
        table_name: str,
        cobol_fields: list[dict[str, str]],
    ) -> list[DclgenColumn]:
        output: list[DclgenColumn] = []

        for field in cobol_fields:
            output.append(
                DclgenColumn(
                    table_name=table_name,
                    column_name=self._normalize_cobol_name_to_db2(
                        field["name"],
                    ),
                    db2_type="",
                    cobol_host_name=field["name"],
                    cobol_picture=field["picture"],
                    cobol_usage=field.get(
                        "usage",
                        "",
                    ),
                    nullable=True,
                )
            )

        return output

    def _normalize_sql_name(
        self,
        value: str,
    ) -> str:
        text = str(
            value or "",
        ).strip()

        text = text.strip(
            '"',
        )
        text = text.strip(
            "'",
        )
        text = text.strip(
            "`",
        )
        text = text.strip(
            "[",
        )
        text = text.strip(
            "]",
        )

        if "." in text:
            text = text.split(
                ".",
            )[-1]

        return text.upper()

    def _normalize_cobol_name_to_db2(
        self,
        value: str,
    ) -> str:
        text = str(
            value or "",
        ).strip().upper()

        text = text.replace(
            "-",
            "_",
        )

        text = re.sub(
            r"[^A-Z0-9_]+",
            "_",
            text,
        )

        text = re.sub(
            r"_+",
            "_",
            text,
        )

        return text.strip(
            "_",
        )