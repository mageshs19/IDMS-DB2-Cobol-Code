from __future__ import annotations

import re
from collections import defaultdict

from idms_db2_phase2.domain.models import DclgenColumn, IdmsOperation, SheetMappingRow
from idms_db2_phase2.services.name_normalizer import NameNormalizer


class Db2InfrastructureGenerator:
    """
    Generates DB2 infrastructure in the DATA DIVISION area.

    Core rules:
    - Sheet Mapping is the authority for DB2 record/table names.
    - Sheet Mapping is the authority for DB2 column names.
    - DCLGEN is the authority for COBOL host-variable spelling and PIC.
    - Cursor names are derived from Sheet Mapping DB2 record/table names.
    - Retrieval cursor columns exclude audit columns by default.
    - DCLGEN INCLUDE statements are generated only for tables actually used.
    - If Sheet Mapping uses TB but uploaded DCLGEN uses TV, generated COBOL uses the DCLGEN TV table/group.
    - Area/root cursors do not get WHERE clauses.
    - Child cursors use parent table host variables in WHERE clauses.
    """

    DB2_BLOCK_MARKER = (
        "* DB2 SQLCA, SQL ERROR WORKING STORAGE, DCLGEN INCLUDES, AND CURSOR FLAGS"
    )

    DB2_INFRA_TITLE = "DB2 SQLCA, SQL ERROR WORKING STORAGE, DCLGEN INCLUDES, AND CURSOR FLAGS"
    DB2_SQL_ERROR_LOCATION_TITLE = "DB2 SQL ERROR LOCATION"
    DB2_CURSOR_FLAGS_TITLE = "DB2 CURSOR END-OF-CURSOR FLAGS"
    DB2_CURSOR_DECLARATIONS_TITLE = "DB2 CURSOR DECLARATIONS"

    PROCEDURE_DIVISION_PATTERN = re.compile(
        r"(^\s*(?:\d{6}\s+)?PROCEDURE\s+DIVISION\b.*\.?\s*(?:\d{8})?\s*$)",
        flags=re.IGNORECASE | re.MULTILINE,
    )

    LINKAGE_SECTION_PATTERN = re.compile(
        r"(^\s*(?:\d{6}\s+)?LINKAGE\s+SECTION\.\s*(?:\d{8})?\s*$)",
        flags=re.IGNORECASE | re.MULTILINE,
    )

    WORKING_STORAGE_PATTERN = re.compile(
        r"(^\s*(?:\d{6}\s+)?WORKING-STORAGE\s+SECTION\.\s*(?:\d{8})?\s*$)",
        flags=re.IGNORECASE | re.MULTILINE,
    )

    DATA_DIVISION_PATTERN = re.compile(
        r"(^\s*(?:\d{6}\s+)?DATA\s+DIVISION\.\s*(?:\d{8})?\s*$)",
        flags=re.IGNORECASE | re.MULTILINE,
    )

    def apply(
        self,
        cobol_text: str,
        dclgen_columns: list[DclgenColumn],
        operations: list[IdmsOperation],
        mapping_rows: list[SheetMappingRow],
    ) -> tuple[str, list[str]]:
        messages: list[str] = []

        if not cobol_text:
            return cobol_text, messages

        if self.DB2_BLOCK_MARKER in cobol_text:
            messages.append(
                "DB2 infrastructure: existing generated DB2 infrastructure block detected; not inserted again."
            )
            return cobol_text, messages

        context = Db2MappingContext(
            mapping_rows=mapping_rows,
            dclgen_columns=dclgen_columns,
        )

        cursor_specs = self._cursor_specs(
            operations=operations,
            context=context,
        )

        include_names = context.used_dclgen_include_names(
            operations=operations,
            cursor_specs=cursor_specs,
        )

        if include_names:
            messages.append(
                "DB2 infrastructure: DCLGEN includes selected: "
                + ", ".join(include_names)
            )
        else:
            messages.append(
                "DB2 infrastructure: no operation-specific DCLGEN includes resolved."
            )

        block = self._build_infrastructure_block(
            include_names=include_names,
            cursor_specs=cursor_specs,
        )

        text, inserted = self._insert_before_linkage_section(
            text=cobol_text,
            block=block,
        )

        if inserted:
            messages.append("DB2 infrastructure: inserted before LINKAGE SECTION.")
            messages.extend(self._cursor_spec_messages(cursor_specs))
            return text, messages

        text, inserted = self._insert_before_procedure_division(
            text=cobol_text,
            block=block,
        )

        if inserted:
            messages.append("DB2 infrastructure: inserted before PROCEDURE DIVISION.")
            messages.extend(self._cursor_spec_messages(cursor_specs))
            return text, messages

        text, inserted = self._insert_after_working_storage(
            text=cobol_text,
            block=block,
        )

        if inserted:
            messages.append("DB2 infrastructure: inserted after WORKING-STORAGE SECTION.")
            messages.extend(self._cursor_spec_messages(cursor_specs))
            return text, messages

        text, inserted = self._insert_after_data_division(
            text=cobol_text,
            block=block,
        )

        if inserted:
            messages.append(
                "DB2 infrastructure: inserted WORKING-STORAGE SECTION after DATA DIVISION."
            )
            messages.extend(self._cursor_spec_messages(cursor_specs))
            return text, messages

        messages.append(
            "DB2 infrastructure: no DATA DIVISION, WORKING-STORAGE SECTION, LINKAGE SECTION, or PROCEDURE DIVISION anchor found; inserted at top."
        )
        messages.extend(self._cursor_spec_messages(cursor_specs))

        return block + "\n\n" + cobol_text, messages

    def _cursor_specs(
        self,
        operations: list[IdmsOperation],
        context: "Db2MappingContext",
    ) -> list[dict[str, object]]:
        specs: list[dict[str, object]] = []
        seen_keys: set[tuple[str, str, str]] = set()

        for operation in operations or []:
            operation_name = str(operation.operation or "").upper()

            if operation_name not in {
                "OBTAIN_FIRST",
                "OBTAIN_NEXT",
                "FIND_FIRST",
            }:
                continue

            set_name = NameNormalizer.normalize(
                operation.set_name,
            )
            record_name = NameNormalizer.normalize(
                operation.record_name,
            )

            if not record_name:
                continue

            table_name = context.best_table_for_record(
                record_name,
            )

            table_name = context.resolve_dclgen_table(
                table_name,
            )

            if not table_name:
                continue

            spec_key = (
                set_name,
                record_name,
                table_name,
            )

            if spec_key in seen_keys:
                continue

            seen_keys.add(
                spec_key,
            )

            select_columns = context.cursor_select_columns_for_record(
                record_name=record_name,
                table_name=table_name,
            )

            if context.looks_like_child_set(set_name):
                where_conditions = context.cursor_where_conditions(
                    record_name=record_name,
                    set_name=set_name,
                    child_table=table_name,
                )
            else:
                where_conditions = []

            order_by_columns = context.cursor_order_by_columns(
                record_name=record_name,
                set_name=set_name,
                child_table=table_name,
                fallback_columns=select_columns,
            )

            cursor_name = self._cursor_name_from_db2_record(
                table_name,
            )

            specs.append(
                {
                    "set_name": set_name,
                    "record_name": record_name,
                    "table_name": table_name,
                    "cursor_name": cursor_name,
                    "select_columns": select_columns,
                    "where_conditions": where_conditions,
                    "order_by_columns": order_by_columns,
                }
            )

        return specs

    def _cursor_name_from_db2_record(
        self,
        table_name: str,
    ) -> str:
        table = NameNormalizer.normalize(
            table_name,
        )

        if not table:
            return "DB2CURC1"

        if table.endswith("_TV") or table.endswith("_TB"):
            return NameNormalizer.to_cobol(
                table[:-3] + "_C1",
            )

        if table.endswith("TV") or table.endswith("TB"):
            return NameNormalizer.to_cobol(
                table[:-2] + "C1",
            )

        return NameNormalizer.to_cobol(
            table + "_C1",
        )

    def _cursor_spec_messages(
        self,
        cursor_specs: list[dict[str, object]],
    ) -> list[str]:
        messages: list[str] = []

        for spec in cursor_specs:
            cursor_name = str(
                spec.get("cursor_name", ""),
            )
            record_name = str(
                spec.get("record_name", ""),
            )
            table_name = str(
                spec.get("table_name", ""),
            )
            select_columns = list(
                spec.get("select_columns", []),
            )
            where_conditions = list(
                spec.get("where_conditions", []),
            )
            set_name = str(
                spec.get("set_name", ""),
            )

            if not table_name:
                messages.append(
                    f"DB2 infrastructure: cursor {cursor_name} has no resolved DB2 table for record {record_name}."
                )

            if not select_columns:
                messages.append(
                    f"DB2 infrastructure: cursor {cursor_name} has no resolved SELECT columns for record {record_name}."
                )

            if not where_conditions and self._looks_like_child_set(set_name):
                messages.append(
                    f"DB2 infrastructure: cursor {cursor_name} generated without WHERE conditions. Review Sheet Mapping relation/FK rows for set/record {record_name}."
                )

        return messages

    def _looks_like_child_set(
        self,
        set_name: str,
    ) -> bool:
        normalized = NameNormalizer.normalize(
            set_name,
        )

        if not normalized:
            return False

        parts = [
            part for part in normalized.split("_") if part
        ]

        if len(parts) < 2:
            return False

        if parts[0] in {
            "AR",
            "AREA",
            "IX",
            "INDEX",
        }:
            return False

        return True

    def _build_infrastructure_block(
        self,
        include_names: list[str],
        cursor_specs: list[dict[str, object]],
    ) -> str:
        lines: list[str] = []

        lines.extend(
            self._comment_block(
                self.DB2_INFRA_TITLE,
            )
        )

        lines.extend(
            self._include_lines(
                include_names=[
                    "SQLERRWS",
                    "SQLCA",
                    *include_names,
                ],
            )
        )

        lines.append("")

        lines.extend(
            self._comment_block(
                self.DB2_SQL_ERROR_LOCATION_TITLE,
            )
        )

        lines.append(
            "01  SQL-LOCATION                    PIC X(40) VALUE SPACES."
        )

        if cursor_specs:
            lines.append("")
            lines.extend(
                self._comment_block(
                    self.DB2_CURSOR_FLAGS_TITLE,
                )
            )

            for spec in cursor_specs:
                cursor_name = str(
                    spec["cursor_name"],
                )
                flag_name = f"WS-{cursor_name}-FLAG"
                not_eoc_name = f"{cursor_name}-NOT-EOC"
                eoc_name = f"{cursor_name}-EOC"

                lines.extend(
                    [
                        f"01  {flag_name:<30} PIC X VALUE 'N'.",
                        f"    88  {not_eoc_name:<26} VALUE 'N'.",
                        f"    88  {eoc_name:<26} VALUE 'Y'.",
                    ]
                )

            lines.append("")
            lines.extend(
                self._comment_block(
                    self.DB2_CURSOR_DECLARATIONS_TITLE,
                )
            )

            for spec in cursor_specs:
                lines.extend(
                    self._cursor_declare_lines(
                        cursor_name=str(spec["cursor_name"]),
                        table_name=str(spec["table_name"]),
                        select_columns=list(spec.get("select_columns", [])),
                        where_conditions=list(spec.get("where_conditions", [])),
                        order_by_columns=list(spec.get("order_by_columns", [])),
                    )
                )
                lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def _include_lines(
        self,
        include_names: list[str],
    ) -> list[str]:
        lines: list[str] = []
        seen: set[str] = set()

        for include_name in include_names:
            normalized = self._normalize_include_name(
                include_name,
            )

            if not normalized:
                continue

            if normalized in seen:
                continue

            seen.add(
                normalized,
            )

            lines.extend(
                [
                    "EXEC SQL",
                    f"    INCLUDE {normalized}",
                    "END-EXEC.",
                ]
            )

        return lines

    def _cursor_declare_lines(
        self,
        cursor_name: str,
        table_name: str,
        select_columns: list[str],
        where_conditions: list[str],
        order_by_columns: list[str],
    ) -> list[str]:
        table = NameNormalizer.normalize(
            table_name,
        )
        cursor = NameNormalizer.to_cobol(
            cursor_name,
        )

        if not table:
            return [
                f"* DB2 WARNING: Unable to declare cursor {cursor}; missing DB2 table mapping."
            ]

        columns = [
            NameNormalizer.normalize(column)
            for column in select_columns
            if NameNormalizer.normalize(column)
        ]

        if not columns:
            columns = ["*"]

        lines: list[str] = [
            "EXEC SQL",
            f"    DECLARE {cursor} CURSOR FOR",
            "    SELECT",
        ]

        lines.extend(
            self._comma_lines(
                items=columns,
                indent="    ",
            )
        )

        lines.append(
            f"    FROM {table}"
        )

        clean_where_conditions = [
            str(condition or "").strip()
            for condition in where_conditions
            if str(condition or "").strip()
        ]

        if clean_where_conditions:
            lines.append(
                "    WHERE"
            )
            lines.extend(
                self._and_lines(
                    items=clean_where_conditions,
                    indent="    ",
                )
            )

        clean_order_by_columns = [
            NameNormalizer.normalize(column)
            for column in order_by_columns
            if NameNormalizer.normalize(column)
        ]

        if clean_order_by_columns:
            lines.append(
                "    ORDER BY"
            )
            lines.extend(
                self._comma_lines(
                    items=clean_order_by_columns,
                    indent="    ",
                )
            )

        lines.extend(
            [
                "    FOR READ ONLY",
                "END-EXEC.",
            ]
        )

        return lines

    def _comment_block(
        self,
        title: str,
    ) -> list[str]:
        return [
            "******************************************************************",
            f"* {title:<62}*",
            "******************************************************************",
        ]

    def _insert_before_linkage_section(
        self,
        text: str,
        block: str,
    ) -> tuple[str, bool]:
        return self._insert_before_pattern(
            text=text,
            block=block,
            pattern=self.LINKAGE_SECTION_PATTERN,
        )

    def _insert_before_procedure_division(
        self,
        text: str,
        block: str,
    ) -> tuple[str, bool]:
        return self._insert_before_pattern(
            text=text,
            block=block,
            pattern=self.PROCEDURE_DIVISION_PATTERN,
        )

    def _insert_after_working_storage(
        self,
        text: str,
        block: str,
    ) -> tuple[str, bool]:
        return self._insert_after_pattern(
            text=text,
            block=block,
            pattern=self.WORKING_STORAGE_PATTERN,
        )

    def _insert_after_data_division(
        self,
        text: str,
        block: str,
    ) -> tuple[str, bool]:
        match = self.DATA_DIVISION_PATTERN.search(
            text,
        )

        if not match:
            return text, False

        working_storage_block = (
            "WORKING-STORAGE SECTION.\n"
            + block
        )

        updated = (
            text[: match.end()]
            + "\n"
            + working_storage_block
            + "\n"
            + text[match.end():]
        )

        return updated, True

    def _insert_before_pattern(
        self,
        text: str,
        block: str,
        pattern: re.Pattern,
    ) -> tuple[str, bool]:
        match = pattern.search(
            text,
        )

        if not match:
            return text, False

        updated = (
            text[: match.start()]
            + "\n"
            + block
            + "\n"
            + text[match.start():]
        )

        return updated, True

    def _insert_after_pattern(
        self,
        text: str,
        block: str,
        pattern: re.Pattern,
    ) -> tuple[str, bool]:
        match = pattern.search(
            text,
        )

        if not match:
            return text, False

        updated = (
            text[: match.end()]
            + "\n"
            + block
            + "\n"
            + text[match.end():]
        )

        return updated, True

    def _comma_lines(
        self,
        items: list[str],
        indent: str,
    ) -> list[str]:
        output: list[str] = []

        clean_items = [
            str(item or "").strip()
            for item in items
            if str(item or "").strip()
        ]

        for index, item in enumerate(clean_items):
            suffix = "," if index < len(clean_items) - 1 else ""
            output.append(
                f"{indent}{item}{suffix}"
            )

        return output

    def _and_lines(
        self,
        items: list[str],
        indent: str,
    ) -> list[str]:
        output: list[str] = []

        clean_items = [
            str(item or "").strip()
            for item in items
            if str(item or "").strip()
        ]

        for index, item in enumerate(clean_items):
            prefix = "AND " if index > 0 else ""
            output.append(
                f"{indent}{prefix}{item}"
            )

        return output

    def _normalize_include_name(
        self,
        value: str,
    ) -> str:
        text = str(value or "").strip()

        if not text:
            return ""

        if "." in text:
            text = text.split(".")[-1]

        text = text.replace("-", "_")
        text = re.sub(
            r"[^A-Z0-9_]+",
            "_",
            text.upper(),
        )
        text = re.sub(
            r"_+",
            "_",
            text,
        )

        return text.strip("_")


class Db2MappingContext:
    """
    Shared mapping helper for generated DB2 infrastructure and cursor paragraphs.

    Core rules:
    - Sheet Mapping determines DB2 table and column names.
    - DCLGEN determines host variable spelling.
    - Retrieval cursor columns exclude audit columns by default.
    - If Sheet Mapping table is TB and DCLGEN table is TV, use the DCLGEN TV table/group.
    """

    AUDIT_COLUMN_PREFIXES = (
        "TS_CREATE",
        "TS_UPDATE",
        "ID_USERID",
        "NR_USERID",
        "ID_USER",
        "NR_USER",
        "NS_IDMSKEY",
    )

    COBOL_LEVEL_RECORD_PATTERN = re.compile(
        r"^\s*01\s+(?P<name>[A-Z][A-Z0-9-]*)\b",
        flags=re.IGNORECASE,
    )

    def __init__(
        self,
        mapping_rows: list[SheetMappingRow],
        dclgen_columns: list[DclgenColumn],
    ) -> None:
        self.mapping_rows = mapping_rows or []
        self.dclgen_columns = dclgen_columns or []

        self.rows_by_record = self.group_rows_by_record(
            self.mapping_rows,
        )
        self.dclgen_by_table = self._group_dclgen_by_table(
            self.dclgen_columns,
        )
        self.dclgen_host_lookup = self._build_dclgen_host_lookup(
            self.dclgen_columns,
        )

    def used_dclgen_include_names(
        self,
        operations: list[IdmsOperation],
        cursor_specs: list[dict[str, object]],
    ) -> list[str]:
        used_tables: list[str] = []

        for spec in cursor_specs or []:
            table_name = self.resolve_dclgen_table(
                str(spec.get("table_name", "")),
            )

            if table_name:
                used_tables.append(
                    table_name,
                )

        for operation in operations or []:
            record_name = NameNormalizer.normalize(
                operation.record_name,
            )
            operation_name = str(
                operation.operation or "",
            ).upper()

            if not record_name:
                continue

            if operation_name not in {
                "OBTAIN_FIRST",
                "OBTAIN_NEXT",
                "FIND_FIRST",
                "OBTAIN_CALC",
                "STORE",
                "MODIFY",
                "ERASE",
            }:
                continue

            table_name = self.best_table_for_record(
                record_name,
            )

            table_name = self.resolve_dclgen_table(
                table_name,
            )

            if table_name:
                used_tables.append(
                    table_name,
                )

        include_names: list[str] = []
        seen: set[str] = set()

        for table in used_tables:
            include_name = self._normalize_include_name(
                table,
            )

            if not include_name:
                continue

            if include_name in seen:
                continue

            seen.add(
                include_name,
            )
            include_names.append(
                include_name,
            )

        return include_names

    def dclgen_include_names(
        self,
    ) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()

        for column in self.dclgen_columns:
            table = self.resolve_dclgen_table(
                column.table_name,
            )

            if not table:
                continue

            include_name = self._normalize_include_name(
                table,
            )

            if not include_name:
                continue

            if include_name in seen:
                continue

            seen.add(
                include_name,
            )
            names.append(
                include_name,
            )

        return names

    def resolve_dclgen_table(
        self,
        table_name: str,
    ) -> str:
        table = NameNormalizer.normalize(
            table_name,
        )

        if not table:
            return ""

        for candidate in self._table_candidates(table):
            if candidate in self.dclgen_by_table:
                return candidate

        return table

    def best_table_for_record(
        self,
        record_name: str,
    ) -> str:
        record = NameNormalizer.normalize(
            record_name,
        )

        rows = self.record_rows(
            record,
        )

        explicit_table_scores: dict[str, int] = {}

        for row in rows:
            table = NameNormalizer.normalize(
                row.new_db2_record,
            )
            column = NameNormalizer.normalize(
                row.new_db2_field_name,
            )

            if table and column:
                resolved_table = self.resolve_dclgen_table(
                    table,
                )
                explicit_table_scores[resolved_table] = explicit_table_scores.get(
                    resolved_table,
                    0,
                ) + 1

        if explicit_table_scores:
            return max(
                explicit_table_scores.items(),
                key=lambda item: item[1],
            )[0]

        mapping_columns = {
            NameNormalizer.normalize(row.new_db2_field_name)
            for row in rows
            if row.new_db2_field_name
        }

        dclgen_scores: dict[str, int] = {}

        for dclgen_column in self.dclgen_columns:
            table = NameNormalizer.normalize(
                dclgen_column.table_name,
            )
            db2_column = NameNormalizer.normalize(
                dclgen_column.column_name,
            )

            if not table:
                continue

            if db2_column in mapping_columns:
                dclgen_scores[table] = dclgen_scores.get(
                    table,
                    0,
                ) + 1

        if dclgen_scores:
            return max(
                dclgen_scores.items(),
                key=lambda item: item[1],
            )[0]

        matched_table = self._best_dclgen_table_for_record(
            record,
        )

        if matched_table:
            return matched_table

        return ""

    def record_rows(
        self,
        record_name: str,
    ) -> list[SheetMappingRow]:
        record = NameNormalizer.normalize(
            record_name,
        )

        if not record:
            return []

        rows = list(
            self.rows_by_record.get(
                record,
                [],
            )
        )

        no_suffix = NameNormalizer.remove_record_suffix(
            record,
        )

        if no_suffix and no_suffix != record:
            rows.extend(
                self.rows_by_record.get(
                    no_suffix,
                    [],
                )
            )

        return rows

    def columns_for_record(
        self,
        record_name: str,
        table_name: str,
    ) -> list[str]:
        rows = self.record_rows(
            record_name,
        )
        table = self.resolve_dclgen_table(
            table_name,
        )

        dclgen_columns = self.dclgen_columns_for_table(
            table,
        )
        dclgen_column_set = {
            NameNormalizer.normalize(column)
            for column in dclgen_columns
        }

        output: list[str] = []
        seen: set[str] = set()

        for row in rows:
            row_table = NameNormalizer.normalize(
                row.new_db2_record,
            )

            resolved_row_table = self.resolve_dclgen_table(
                row_table,
            )

            if table and resolved_row_table and resolved_row_table != table:
                continue

            column = NameNormalizer.normalize(
                row.new_db2_field_name,
            )

            if not column:
                continue

            if dclgen_column_set and column not in dclgen_column_set:
                continue

            if column in seen:
                continue

            seen.add(
                column,
            )
            output.append(
                column,
            )

        if output:
            return output

        return dclgen_columns

    def cursor_select_columns_for_record(
        self,
        record_name: str,
        table_name: str,
    ) -> list[str]:
        columns = self.columns_for_record(
            record_name=record_name,
            table_name=table_name,
        )

        filtered = [
            column
            for column in columns
            if not self._is_audit_column(column)
        ]

        if filtered:
            return filtered

        return columns

    def host_variables_for_record(
        self,
        record_name: str,
        table_name: str,
    ) -> list[str]:
        columns = self.cursor_select_columns_for_record(
            record_name=record_name,
            table_name=table_name,
        )

        hosts: list[str] = []
        seen: set[str] = set()

        for column in columns:
            host = self.host_for_column(
                table_name=table_name,
                column_name=column,
            )

            if not host:
                continue

            if host in seen:
                continue

            seen.add(
                host,
            )
            hosts.append(
                host,
            )

        return hosts

    def host_for_column(
        self,
        table_name: str,
        column_name: str,
    ) -> str:
        table = self.resolve_dclgen_table(
            table_name,
        )
        column = NameNormalizer.normalize(
            column_name,
        )

        if not column:
            return ""

        for table_candidate in self._table_candidates(table):
            resolved_candidate = self.resolve_dclgen_table(
                table_candidate,
            )

            host = self.dclgen_host_lookup.get(
                (
                    resolved_candidate,
                    column,
                )
            )

            if host:
                return self._normalize_host_reference(
                    host,
                )

        host = self.dclgen_host_lookup.get(
            (
                "",
                column,
            )
        )

        if host:
            return self._normalize_host_reference(
                host,
            )

        return ""

    def cursor_where_conditions(
        self,
        record_name: str,
        set_name: str,
        child_table: str,
    ) -> list[str]:
        normalized_set = NameNormalizer.normalize(
            set_name,
        )

        if not self.looks_like_child_set(
            normalized_set,
        ):
            return []

        conditions = self._fallback_cursor_where_from_common_parent_columns(
            record_name=record_name,
            set_name=normalized_set,
            child_table=child_table,
        )

        if conditions:
            return conditions

        relationship_rows = self.relationship_rows_for_cursor(
            record_name=record_name,
            set_name=normalized_set,
            child_table=child_table,
        )

        conditions = self._conditions_from_relationship_rows(
            relationship_rows=relationship_rows,
            set_name=normalized_set,
        )

        if conditions:
            return conditions

        return []

    def cursor_order_by_columns(
        self,
        record_name: str,
        set_name: str,
        child_table: str,
        fallback_columns: list[str],
    ) -> list[str]:
        rows = self.record_rows(
            record_name,
        )
        table = self.resolve_dclgen_table(
            child_table,
        )

        dclgen_columns = self.dclgen_columns_for_table(
            table,
        )
        dclgen_column_set = {
            NameNormalizer.normalize(column)
            for column in dclgen_columns
        }

        output: list[str] = []
        seen: set[str] = set()

        for row in rows:
            row_table = NameNormalizer.normalize(
                row.new_db2_record,
            )
            resolved_row_table = self.resolve_dclgen_table(
                row_table,
            )

            if table and resolved_row_table and resolved_row_table != table:
                continue

            key_text = " ".join(
                [
                    str(row.db2_key or ""),
                    str(row.idms_key or ""),
                ]
            ).upper()

            if "KEY" not in key_text and "PRIMARY" not in key_text:
                continue

            column = NameNormalizer.normalize(
                row.new_db2_field_name,
            )

            if not column:
                continue

            if dclgen_column_set and column not in dclgen_column_set:
                continue

            if self._is_audit_column(column):
                continue

            if column in seen:
                continue

            seen.add(
                column,
            )
            output.append(
                column,
            )

        if output:
            return output

        clean_fallback = [
            NameNormalizer.normalize(column)
            for column in fallback_columns
            if NameNormalizer.normalize(column)
            and not self._is_audit_column(column)
        ]

        return clean_fallback[:4]

    def relationship_rows_for_cursor(
        self,
        record_name: str,
        set_name: str,
        child_table: str,
    ) -> list[SheetMappingRow]:
        child_record = NameNormalizer.normalize(
            record_name,
        )
        table = self.resolve_dclgen_table(
            child_table,
        )
        set_normalized = NameNormalizer.normalize(
            set_name,
        )

        rows: list[SheetMappingRow] = []

        for row in self.mapping_rows:
            relation_text = " ".join(
                [
                    str(row.relation or ""),
                    str(row.remarks or ""),
                    str(row.db2_key or ""),
                    str(row.idms_key or ""),
                ]
            ).upper()

            row_table = NameNormalizer.normalize(
                row.new_db2_record,
            )
            resolved_row_table = self.resolve_dclgen_table(
                row_table,
            )

            if table and resolved_row_table and resolved_row_table != table:
                continue

            if "FOREIGN" not in relation_text and "RELATION" not in relation_text:
                continue

            row_record = NameNormalizer.normalize(
                row.cobol_record_idms,
            )

            if child_record and row_record and row_record != child_record:
                continue

            if set_normalized and set_normalized in relation_text:
                rows.append(
                    row,
                )
                continue

            rows.append(
                row,
            )

        return rows

    def looks_like_child_set(
        self,
        set_name: str,
    ) -> bool:
        normalized = NameNormalizer.normalize(
            set_name,
        )

        if not normalized:
            return False

        parts = [
            part for part in normalized.split("_") if part
        ]

        if len(parts) < 2:
            return False

        if parts[0] in {
            "AR",
            "AREA",
            "IX",
            "INDEX",
        }:
            return False

        return True

    def group_rows_by_record(
        self,
        rows: list[SheetMappingRow],
    ) -> dict[str, list[SheetMappingRow]]:
        grouped: dict[str, list[SheetMappingRow]] = defaultdict(list)
        current_record = ""

        for row in rows:
            record = NameNormalizer.normalize(
                row.cobol_record_idms,
            )

            zone_record = self._record_from_cobol_zone(
                row.cobol_zone,
            )

            if record:
                current_record = record

            if zone_record:
                current_record = zone_record

            active_record = current_record

            if not active_record:
                continue

            grouped[active_record].append(
                row,
            )

            no_suffix = NameNormalizer.remove_record_suffix(
                active_record,
            )

            if no_suffix and no_suffix != active_record:
                grouped[no_suffix].append(
                    row,
                )

        return grouped

    def dclgen_columns_for_table(
        self,
        table_name: str,
    ) -> list[str]:
        table = self.resolve_dclgen_table(
            table_name,
        )

        output: list[str] = []
        seen: set[str] = set()

        for table_candidate in self._table_candidates(table):
            resolved_candidate = self.resolve_dclgen_table(
                table_candidate,
            )

            for column in self.dclgen_by_table.get(
                resolved_candidate,
                [],
            ):
                db2_column = NameNormalizer.normalize(
                    column.column_name,
                )

                if not db2_column:
                    continue

                if db2_column in seen:
                    continue

                if self._is_audit_column(db2_column):
                    continue

                seen.add(
                    db2_column,
                )
                output.append(
                    db2_column,
                )

            if output:
                return output

        return output

    def _conditions_from_relationship_rows(
        self,
        relationship_rows: list[SheetMappingRow],
        set_name: str = "",
    ) -> list[str]:
        conditions: list[str] = []
        seen: set[str] = set()

        parent_record = self._infer_parent_record_from_set_name(
            set_name,
        )
        parent_table = ""

        if parent_record:
            parent_table = self.best_table_for_record(
                parent_record,
            )

        parent_table = self.resolve_dclgen_table(
            parent_table,
        )

        for row in relationship_rows:
            column = NameNormalizer.normalize(
                row.new_db2_field_name,
            )

            if not column:
                continue

            host = ""

            if parent_table and self._column_exists_in_table(
                table_name=parent_table,
                column_name=column,
            ):
                host = self.host_for_column(
                    table_name=parent_table,
                    column_name=column,
                )

            if not host:
                host = self._parent_host_for_relation_row(
                    row,
                )

            if not host:
                continue

            condition = f"{column} = {host}"

            if condition in seen:
                continue

            seen.add(
                condition,
            )
            conditions.append(
                condition,
            )

        return conditions

    def _parent_host_for_relation_row(
        self,
        row: SheetMappingRow,
    ) -> str:
        parent_table = self.resolve_dclgen_table(
            row.cross_application_db2_table,
        )
        parent_column = NameNormalizer.normalize(
            row.cross_application_db2_field_name,
        )

        if parent_table and parent_column:
            return self.host_for_column(
                table_name=parent_table,
                column_name=parent_column,
            )

        fallback_table = self.resolve_dclgen_table(
            row.new_db2_record,
        )
        fallback_column = NameNormalizer.normalize(
            row.new_db2_field_name,
        )

        if fallback_table and fallback_column:
            return self.host_for_column(
                table_name=fallback_table,
                column_name=fallback_column,
            )

        return ""

    def _fallback_cursor_where_from_common_parent_columns(
        self,
        record_name: str,
        set_name: str,
        child_table: str,
    ) -> list[str]:
        parent_record = self._infer_parent_record_from_set_name(
            set_name,
        )
        child_record = NameNormalizer.normalize(
            record_name,
        )

        if not parent_record or not child_record:
            return []

        if parent_record == child_record:
            return []

        parent_table = self.best_table_for_record(
            parent_record,
        )
        parent_table = self.resolve_dclgen_table(
            parent_table,
        )

        child_table_normalized = self.resolve_dclgen_table(
            child_table,
        )

        if not parent_table or not child_table_normalized:
            return []

        parent_columns = self.dclgen_columns_for_table(
            parent_table,
        )
        child_columns = self.dclgen_columns_for_table(
            child_table_normalized,
        )

        parent_set = {
            NameNormalizer.normalize(column)
            for column in parent_columns
        }

        child_set = {
            NameNormalizer.normalize(column)
            for column in child_columns
        }

        common_columns = [
            NameNormalizer.normalize(column)
            for column in child_columns
            if NameNormalizer.normalize(column) in parent_set
            and NameNormalizer.normalize(column) in child_set
            and not self._is_audit_column(column)
        ]

        preferred_order = [
            "CT_RKTPROD_479BEFF",
            "NR_IDSTOCK_479BEFF",
            "NR_CDSTK_479BEFF",
            "NS_DBSTK_479BEFF",
        ]

        ordered_common_columns: list[str] = []

        for preferred in preferred_order:
            if preferred in common_columns:
                ordered_common_columns.append(
                    preferred,
                )

        for column in common_columns:
            if column not in ordered_common_columns:
                ordered_common_columns.append(
                    column,
                )

        conditions: list[str] = []
        seen: set[str] = set()

        for child_column in ordered_common_columns:
            parent_host = self.host_for_column(
                table_name=parent_table,
                column_name=child_column,
            )

            if not parent_host:
                continue

            condition = f"{NameNormalizer.normalize(child_column)} = {parent_host}"

            if condition in seen:
                continue

            seen.add(
                condition,
            )
            conditions.append(
                condition,
            )

        return conditions

    def _infer_parent_record_from_set_name(
        self,
        set_name: str,
    ) -> str:
        normalized = NameNormalizer.normalize(
            set_name,
        )

        if not normalized:
            return ""

        parts = [
            part for part in normalized.split("_") if part
        ]

        if len(parts) < 2:
            return ""

        return parts[0]

    def _best_dclgen_table_for_record(
        self,
        record_name: str,
    ) -> str:
        record = NameNormalizer.normalize(
            record_name,
        )

        if not record:
            return ""

        compact_record = self._compact_name(
            record,
        )

        candidates: list[tuple[int, str]] = []

        for table in self.dclgen_by_table:
            compact_table = self._compact_name(
                table,
            )

            score = 0

            if compact_record and compact_record in compact_table:
                score += 100

            if compact_record.startswith("VMB"):
                suffix = compact_record[3:]

                if suffix and suffix in compact_table:
                    score += 80

            if len(compact_record) >= 4 and compact_record[-4:] in compact_table:
                score += 40

            if len(compact_record) >= 3 and compact_record[-3:] in compact_table:
                score += 25

            if score > 0:
                candidates.append(
                    (
                        score,
                        table,
                    )
                )

        if not candidates:
            return ""

        candidates.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        return candidates[0][1]

    def _group_dclgen_by_table(
        self,
        columns: list[DclgenColumn],
    ) -> dict[str, list[DclgenColumn]]:
        grouped: dict[str, list[DclgenColumn]] = defaultdict(list)

        for column in columns:
            table = NameNormalizer.normalize(
                column.table_name,
            )

            if table:
                grouped[table].append(
                    column,
                )

        return grouped

    def _build_dclgen_host_lookup(
        self,
        columns: list[DclgenColumn],
    ) -> dict[tuple[str, str], str]:
        lookup: dict[tuple[str, str], str] = {}

        for column in columns:
            actual_table = NameNormalizer.normalize(
                column.table_name,
            )
            db2_column = NameNormalizer.normalize(
                column.column_name,
            )
            host = NameNormalizer.to_cobol(
                column.cobol_host_name or column.column_name,
            )

            if not actual_table or not db2_column or not host:
                continue

            actual_host_reference = f"DCL{NameNormalizer.to_cobol(actual_table)}.{host}"

            for table_candidate in self._table_candidates(actual_table):
                lookup[
                    (
                        table_candidate,
                        db2_column,
                    )
                ] = actual_host_reference

            lookup[
                (
                    "",
                    db2_column,
                )
            ] = actual_host_reference

        return lookup

    def _record_from_cobol_zone(
        self,
        value: str,
    ) -> str:
        text = str(value or "").strip().upper()

        if not text:
            return ""

        match = self.COBOL_LEVEL_RECORD_PATTERN.match(
            text,
        )

        if not match:
            return ""

        return NameNormalizer.normalize(
            match.group("name"),
        )

    def _table_candidates(
        self,
        value: str,
    ) -> list[str]:
        normalized = NameNormalizer.normalize(
            value,
        )

        if not normalized:
            return []

        candidates = [
            normalized,
        ]

        if normalized.endswith("_TB"):
            candidates.append(
                normalized[:-3] + "_TV",
            )

        if normalized.endswith("_TV"):
            candidates.append(
                normalized[:-3] + "_TB",
            )

        if normalized.endswith("TB"):
            candidates.append(
                normalized[:-2] + "TV",
            )

        if normalized.endswith("TV"):
            candidates.append(
                normalized[:-2] + "TB",
            )

        output: list[str] = []

        for candidate in candidates:
            if candidate and candidate not in output:
                output.append(
                    candidate,
                )

        return output

    def _is_audit_column(
        self,
        column: str,
    ) -> bool:
        normalized = NameNormalizer.normalize(
            column,
        )

        if not normalized:
            return False

        return normalized.startswith(
            self.AUDIT_COLUMN_PREFIXES,
        )

    def _column_exists_in_table(
        self,
        table_name: str,
        column_name: str,
    ) -> bool:
        table = self.resolve_dclgen_table(
            table_name,
        )
        column = NameNormalizer.normalize(
            column_name,
        )

        if not table or not column:
            return False

        return column in {
            NameNormalizer.normalize(item)
            for item in self.dclgen_columns_for_table(table)
        }

    def _normalize_host_reference(
        self,
        value: str,
    ) -> str:
        text = str(value or "").strip()

        while text.startswith("::"):
            text = text[1:].strip()

        if not text:
            return ""

        if text.startswith(":"):
            return text

        return ":" + text

    def _normalize_include_name(
        self,
        value: str,
    ) -> str:
        text = str(value or "").strip()

        if "." in text:
            text = text.split(".")[-1]

        return NameNormalizer.normalize(
            text,
        )

    def _compact_name(
        self,
        value: str,
    ) -> str:
        normalized = NameNormalizer.normalize(
            value,
        )

        return re.sub(
            r"[^A-Z0-9]+",
            "",
            normalized,
        )