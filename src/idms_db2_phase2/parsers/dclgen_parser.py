import re

from idms_db2_phase2.domain.models import DclgenColumn
from idms_db2_phase2.services.name_normalizer import NameNormalizer


class DclgenParser:
    """
    Parses DB2 DCLGEN text files.

    Supported DCLGEN styles:
    1. Standard SQL declaration:
        EXEC SQL DECLARE DZBFARTV TABLE
        (
            COL_A
            COL_B
            CHAR(4) NOT NULL,
            DECIMAL(8, 0) NOT NULL
        )
        END-EXEC.

    2. Inline SQL declaration:
        EXEC SQL DECLARE DZBFARTV TABLE
        (
            COL_A CHAR(4) NOT NULL,
            COL_B DECIMAL(8, 0) NOT NULL
        )
        END-EXEC.

    3. DCLGEN header comment:
        * DCLGEN TABLE(DZ.DZBFARTV)

    4. COBOL host variable section:
        01 DCLDZBFARTV.
           10 CT-TGDSERV-479BFAR PIC X(4).
           10 NR-CIOFMAS-479BFAR PIC S9(8)V USAGE COMP-3.

    The parser returns DclgenColumn rows with:
    - table_name
    - column_name
    - db2_type
    - cobol_host_name
    - cobol_picture
    - cobol_usage
    - nullable
    """

    DECLARE_TABLE_PATTERN = re.compile(
        r"EXEC\s+SQL\s+DECLARE\s+([A-Z0-9_.#$@]+)\s+TABLE",
        flags=re.IGNORECASE,
    )

    DECLARE_TABLE_ALT_PATTERN = re.compile(
        r"\bDECLARE\s+([A-Z0-9_.#$@]+)\s+TABLE\b",
        flags=re.IGNORECASE,
    )

    DCLGEN_TABLE_COMMENT_PATTERN = re.compile(
        r"\bDCLGEN\s+TABLE\s*$\s*([A-Z0-9_.#$@]+)\s*$",
        flags=re.IGNORECASE,
    )

    COBOL_GROUP_PATTERN = re.compile(
        r"^\s*01\s+(DCL[A-Z0-9-]+)\.?\s*$",
        flags=re.IGNORECASE,
    )

    COBOL_FIELD_PATTERN = re.compile(
        r"^\s*(0[2-9]|[1-4][0-9]|77)\s+([A-Z][A-Z0-9-]*)\b(?P<body>.*)$",
        flags=re.IGNORECASE,
    )

    PIC_PATTERN = re.compile(
        r"\bPIC(?:TURE)?\s+(?:IS\s+)?(?P<pic>[A-Z0-9SV().,+\-]+)",
        flags=re.IGNORECASE,
    )

    USAGE_PATTERN = re.compile(
        r"\b(?:USAGE\s+(?:IS\s+)?)?(COMP-3|COMP|COMP-1|COMP-2|COMP-4|COMP-5|BINARY|PACKED-DECIMAL|DISPLAY)\b",
        flags=re.IGNORECASE,
    )

    SQL_TYPE_STARTERS = {
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
        "XML",
    }

    SQL_SKIP_WORDS = {
        "EXEC",
        "SQL",
        "DECLARE",
        "TABLE",
        "END-EXEC",
        "END",
        "NOT",
        "NULL",
        "WITH",
        "DEFAULT",
        "PRIMARY",
        "FOREIGN",
        "KEY",
        "CONSTRAINT",
        "UNIQUE",
        "CHECK",
        "REFERENCES",
        "CREATE",
        "IN",
        "IS",
        "THE",
        "DCLGEN",
        "COMMAND",
        "THAT",
        "MADE",
        "FOLLOWING",
        "STATEMENTS",
    }

    def __init__(self) -> None:
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
            source_label = f"DCLGEN file {index}"

            self.diagnostics.append(
                f"{source_label} input length: {len(text or '')}"
            )

            parsed = self.parse(
                text=text,
                source_label=source_label,
            )

            self.diagnostics.append(
                f"{source_label} parsed columns: {len(parsed)}"
            )

            output.extend(parsed)

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

        cleaned_text = self._clean_text(text)

        table_name = self._find_table_name(cleaned_text)

        if table_name:
            self.diagnostics.append(
                f"{source_label}: resolved table name: {table_name}"
            )
        else:
            self.diagnostics.append(
                f"{source_label}: table name not found."
            )

        sql_columns = self._parse_sql_declare_columns(
            text=cleaned_text,
            table_name=table_name,
            source_label=source_label,
        )

        cobol_fields = self._parse_cobol_host_fields(
            text=cleaned_text,
            source_label=source_label,
        )

        if sql_columns:
            output = self._merge_sql_columns_with_cobol_hosts(
                sql_columns=sql_columns,
                cobol_fields=cobol_fields,
                fallback_table_name=table_name,
            )

            self.diagnostics.append(
                f"{source_label}: SQL columns parsed: {len(sql_columns)}"
            )
            self.diagnostics.append(
                f"{source_label}: COBOL host fields parsed: {len(cobol_fields)}"
            )
            self.diagnostics.append(
                f"{source_label}: merged DCLGEN columns: {len(output)}"
            )

            return output

        if cobol_fields:
            self.diagnostics.append(
                f"{source_label}: no SQL DECLARE columns found; using COBOL host fields as fallback."
            )

            return self._fallback_columns_from_cobol_fields(
                table_name=table_name,
                cobol_fields=cobol_fields,
            )

        self.diagnostics.append(
            f"{source_label}: no SQL columns or COBOL host fields found."
        )

        return []

    def _clean_text(
        self,
        text: str,
    ) -> str:
        cleaned = str(text or "")

        cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
        cleaned = cleaned.replace("\t", " ")
        cleaned = cleaned.replace("\u00a0", " ")
        cleaned = cleaned.replace("“", '"').replace("”", '"')
        cleaned = cleaned.replace("‘", "'").replace("’", "'")

        return cleaned

    def _find_table_name(
        self,
        text: str,
    ) -> str:
        for pattern in [
            self.DECLARE_TABLE_PATTERN,
            self.DECLARE_TABLE_ALT_PATTERN,
            self.DCLGEN_TABLE_COMMENT_PATTERN,
        ]:
            match = pattern.search(text)

            if match:
                return self._normalize_sql_name(match.group(1))

        return ""

    def _parse_sql_declare_columns(
        self,
        text: str,
        table_name: str,
        source_label: str,
    ) -> list[dict[str, object]]:
        sections = self._extract_declare_sections(text)

        if not sections:
            self.diagnostics.append(
                f"{source_label}: no EXEC SQL DECLARE TABLE section found."
            )
            return []

        output: list[dict[str, object]] = []

        for section_index, section in enumerate(sections, start=1):
            section_table = self._find_table_name(section) or table_name
            body = self._extract_parenthesized_body(section)

            if not body:
                self.diagnostics.append(
                    f"{source_label}: DECLARE section {section_index} has no column body."
                )
                continue

            parsed = self._parse_declare_body(
                body=body,
                table_name=section_table,
            )

            self.diagnostics.append(
                f"{source_label}: DECLARE section {section_index} parsed SQL columns: {len(parsed)}"
            )

            output.extend(parsed)

        return output

    def _extract_declare_sections(
        self,
        text: str,
    ) -> list[str]:
        lines = text.splitlines()
        sections: list[str] = []
        current: list[str] = []
        inside = False

        for line in lines:
            upper = line.upper()

            if "DECLARE" in upper and "TABLE" in upper:
                inside = True
                current = [line]
                continue

            if inside:
                current.append(line)

                if "END-EXEC" in upper:
                    sections.append("\n".join(current))
                    current = []
                    inside = False
                    continue

                if upper.strip() == ")" or upper.strip().startswith(")"):
                    sections.append("\n".join(current))
                    current = []
                    inside = False
                    continue

        if inside and current:
            sections.append("\n".join(current))

        return sections

    def _extract_parenthesized_body(
        self,
        section: str,
    ) -> str:
        start = section.find("(")

        if start < 0:
            return ""

        depth = 0
        body_chars: list[str] = []

        for index in range(start, len(section)):
            char = section[index]

            if char == "(":
                depth += 1

                if depth == 1:
                    continue

            if char == ")":
                depth -= 1

                if depth == 0:
                    break

            if depth >= 1:
                body_chars.append(char)

        return "".join(body_chars)

    def _parse_declare_body(
        self,
        body: str,
        table_name: str,
    ) -> list[dict[str, object]]:
        logical_items = self._split_sql_items(body)

        inline_columns: list[dict[str, object]] = []
        column_names: list[str] = []
        db2_types: list[str] = []

        for item in logical_items:
            normalized_item = self._normalize_sql_item(item)

            if not normalized_item:
                continue

            inline = self._parse_inline_column_definition(
                normalized_item,
                table_name=table_name,
            )

            if inline:
                inline_columns.append(inline)
                continue

            if self._looks_like_column_name(normalized_item):
                column_names.append(
                    self._normalize_sql_name(normalized_item)
                )
                continue

            if self._looks_like_db2_type(normalized_item):
                db2_types.append(
                    self._normalize_db2_type(normalized_item)
                )
                continue

        if inline_columns:
            return inline_columns

        output: list[dict[str, object]] = []

        for index, column_name in enumerate(column_names):
            db2_type = ""

            if index < len(db2_types):
                db2_type = db2_types[index]

            output.append(
                {
                    "table_name": table_name,
                    "column_name": column_name,
                    "db2_type": db2_type,
                    "nullable": self._is_nullable_db2_type(db2_type),
                }
            )

        return output

    def _split_sql_items(
        self,
        body: str,
    ) -> list[str]:
        items: list[str] = []
        current: list[str] = []
        depth = 0

        normalized = body.replace("\n", " ")

        for char in normalized:
            if char == "(":
                depth += 1
                current.append(char)
                continue

            if char == ")":
                if depth > 0:
                    depth -= 1
                current.append(char)
                continue

            if char == "," and depth == 0:
                item = "".join(current).strip()

                if item:
                    items.append(item)

                current = []
                continue

            current.append(char)

        item = "".join(current).strip()

        if item:
            items.append(item)

        expanded: list[str] = []

        for item in items:
            parts = self._split_possible_stacked_column_or_type_lines(item)
            expanded.extend(parts)

        return expanded

    def _split_possible_stacked_column_or_type_lines(
        self,
        item: str,
    ) -> list[str]:
        text = " ".join(str(item or "").split())

        if not text:
            return []

        inline = self._parse_inline_column_definition(
            text,
            table_name="",
        )

        if inline:
            return [text]

        tokens = text.split()

        if len(tokens) <= 1:
            return [text]

        if self._looks_like_db2_type(text):
            return [text]

        if all(self._looks_like_column_name(token) for token in tokens):
            return tokens

        return [text]

    def _parse_inline_column_definition(
        self,
        item: str,
        table_name: str,
    ) -> dict[str, object] | None:
        tokens = item.split()

        if len(tokens) < 2:
            return None

        first = self._normalize_sql_name(tokens[0])

        if not self._looks_like_column_name(first):
            return None

        remaining = " ".join(tokens[1:])

        if not self._looks_like_db2_type(remaining):
            return None

        db2_type = self._normalize_db2_type(remaining)

        return {
            "table_name": table_name,
            "column_name": first,
            "db2_type": db2_type,
            "nullable": self._is_nullable_db2_type(db2_type),
        }

    def _looks_like_column_name(
        self,
        value: str,
    ) -> bool:
        text = self._normalize_sql_name(value)

        if not text:
            return False

        if text in self.SQL_SKIP_WORDS:
            return False

        if text.split(" ")[0] in self.SQL_TYPE_STARTERS:
            return False

        if not re.fullmatch(r"[A-Z][A-Z0-9_#$@]*", text):
            return False

        return True

    def _looks_like_db2_type(
        self,
        value: str,
    ) -> bool:
        text = str(value or "").strip().upper()

        if not text:
            return False

        first = text.split()[0]
        first = first.split("(", 1)[0]

        return first in self.SQL_TYPE_STARTERS

    def _normalize_sql_item(
        self,
        value: str,
    ) -> str:
        text = str(value or "").strip()
        text = text.strip(",").strip()
        text = " ".join(text.split())

        return text

    def _normalize_db2_type(
        self,
        value: str,
    ) -> str:
        text = " ".join(
            str(value or "").replace(", ", ",").split()
        )

        text = text.rstrip(",")

        return text.upper()

    def _is_nullable_db2_type(
        self,
        db2_type: str,
    ) -> bool:
        return "NOT NULL" not in str(db2_type or "").upper()

    def _parse_cobol_host_fields(
        self,
        text: str,
        source_label: str,
    ) -> list[dict[str, str]]:
        fields: list[dict[str, str]] = []

        current_group = ""
        seen_group = False

        for raw_line in text.splitlines():
            line = raw_line.rstrip()

            group_match = self.COBOL_GROUP_PATTERN.match(line)

            if group_match:
                current_group = group_match.group(1).upper()
                seen_group = True
                continue

            if not seen_group:
                continue

            match = self.COBOL_FIELD_PATTERN.match(line)

            if not match:
                continue

            level = match.group(1)
            name = match.group(2).upper()
            body = match.group("body") or ""

            nullable = name.upper().endswith("-NULL")

            pic = ""
            usage = ""

            pic_match = self.PIC_PATTERN.search(body)

            if pic_match:
                pic = pic_match.group("pic").strip().upper()

            usage_match = self.USAGE_PATTERN.search(body)

            if usage_match:
                usage = usage_match.group(1).strip().upper()

            if not pic and not usage:
                continue

            fields.append(
                {
                    "group": current_group,
                    "level": level,
                    "name": name,
                    "picture": pic,
                    "usage": usage,
                    "nullable_indicator": "Y" if nullable else "N",
                }
            )

        self.diagnostics.append(
            f"{source_label}: COBOL host field scan found {len(fields)} field(s)."
        )

        return fields

    def _merge_sql_columns_with_cobol_hosts(
        self,
        sql_columns: list[dict[str, object]],
        cobol_fields: list[dict[str, str]],
        fallback_table_name: str,
    ) -> list[DclgenColumn]:
        output: list[DclgenColumn] = []

        usable_hosts = self._usable_host_fields(cobol_fields)

        for index, sql_column in enumerate(sql_columns):
            table_name = self._normalize_sql_name(
                str(
                    sql_column.get("table_name", "")
                    or fallback_table_name
                    or ""
                )
            )

            column_name = self._normalize_sql_name(
                str(sql_column.get("column_name", ""))
            )

            db2_type = str(sql_column.get("db2_type", "") or "").strip()

            nullable = bool(sql_column.get("nullable", True))

            host = self._best_host_for_column(
                column_name=column_name,
                index=index,
                usable_hosts=usable_hosts,
            )

            output.append(
                DclgenColumn(
                    table_name=table_name,
                    column_name=column_name,
                    db2_type=db2_type,
                    cobol_host_name=host.get("name", column_name),
                    cobol_picture=host.get("picture", ""),
                    cobol_usage=host.get("usage", ""),
                    nullable=nullable,
                )
            )

        return output

    def _usable_host_fields(
        self,
        cobol_fields: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        output: list[dict[str, str]] = []

        for field in cobol_fields:
            name = str(field.get("name", "")).upper()

            if not name:
                continue

            if name.endswith("-NULL"):
                continue

            if name.endswith("-LEN"):
                continue

            if name.endswith("-TEXT"):
                continue

            output.append(field)

        return output

    def _best_host_for_column(
        self,
        column_name: str,
        index: int,
        usable_hosts: list[dict[str, str]],
    ) -> dict[str, str]:
        normalized_column = self._normalize_compare_name(column_name)

        for host in usable_hosts:
            host_name = self._normalize_compare_name(host.get("name", ""))

            if host_name == normalized_column:
                return host

        for host in usable_hosts:
            host_name = self._normalize_compare_name(host.get("name", ""))

            if normalized_column and normalized_column in host_name:
                return host

            if host_name and host_name in normalized_column:
                return host

        if index < len(usable_hosts):
            return usable_hosts[index]

        return {
            "name": NameNormalizer.to_cobol(column_name),
            "picture": "",
            "usage": "",
        }

    def _fallback_columns_from_cobol_fields(
        self,
        table_name: str,
        cobol_fields: list[dict[str, str]],
    ) -> list[DclgenColumn]:
        output: list[DclgenColumn] = []

        for field in self._usable_host_fields(cobol_fields):
            host_name = field.get("name", "")

            if not host_name:
                continue

            output.append(
                DclgenColumn(
                    table_name=table_name,
                    column_name=self._normalize_cobol_name_to_db2(host_name),
                    db2_type="",
                    cobol_host_name=host_name,
                    cobol_picture=field.get("picture", ""),
                    cobol_usage=field.get("usage", ""),
                    nullable=True,
                )
            )

        return output

    def _normalize_sql_name(
        self,
        value: str,
    ) -> str:
        text = str(value or "").strip()

        text = text.strip('"').strip("'")
        text = text.strip(">").strip("[").strip("]")

        if "." in text:
            text = text.split(".")[-1]

        text = text.replace("-", "_")
        text = re.sub(r"[^A-Z0-9_#$@]+", "_", text.upper())
        text = re.sub(r"_+", "_", text)

        return text.strip("_")

    def _normalize_cobol_name_to_db2(
        self,
        value: str,
    ) -> str:
        text = str(value or "").strip().upper()
        text = text.replace("-", "_")
        text = re.sub(r"[^A-Z0-9_]+", "_", text)
        text = re.sub(r"_+", "_", text)

        return text.strip("_")

    def _normalize_compare_name(
        self,
        value: str,
    ) -> str:
        text = self._normalize_cobol_name_to_db2(value)
        text = re.sub(r"^DCL_?", "", text)

        return text