import re
from collections import defaultdict

from idms_db2_phase2.domain.models import DclgenColumn, IdmsOperation, SheetMappingRow
from idms_db2_phase2.services.name_normalizer import NameNormalizer


class Db2InfrastructureGenerator:
    DB2_BLOCK_MARKER = (
        "* DB2 SQLCA, SQL ERROR WORKING STORAGE, DCLGEN INCLUDES, AND CURSOR FLAGS"
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

        block = self._build_infrastructure_block(
            include_names=context.dclgen_include_names(),
            cursor_specs=cursor_specs,
        )

        text, inserted = self._insert_after_working_storage(
            text=cobol_text,
            block=block,
        )

        if inserted:
            messages.append(
                "DB2 infrastructure: inserted after WORKING-STORAGE SECTION."
            )
            messages.extend(
                self._cursor_spec_messages(
                    cursor_specs,
                )
            )
            return text, messages

        text, inserted = self._insert_before_procedure_division(
            text=cobol_text,
            block=block,
        )

        if inserted:
            messages.append(
                "DB2 infrastructure: WORKING-STORAGE SECTION not found; inserted before PROCEDURE DIVISION."
            )
            messages.extend(
                self._cursor_spec_messages(
                    cursor_specs,
                )
            )
            return text, messages

        text, inserted = self._insert_after_data_division(
            text=cobol_text,
            block=block,
        )

        if inserted:
            messages.append(
                "DB2 infrastructure: inserted WORKING-STORAGE SECTION after DATA DIVISION."
            )
            messages.extend(
                self._cursor_spec_messages(
                    cursor_specs,
                )
            )
            return text, messages

        messages.append(
            "DB2 infrastructure: no DATA DIVISION, WORKING-STORAGE SECTION, or PROCEDURE DIVISION anchor found; inserted at top."
        )
        messages.extend(
            self._cursor_spec_messages(
                cursor_specs,
            )
        )

        return block + "\n\n" + cobol_text, messages

    def _cursor_specs(
        self,
        operations: list[IdmsOperation],
        context: "Db2MappingContext",
    ) -> list[dict[str, object]]:
        specs: list[dict[str, object]] = []
        seen_sets: set[str] = set()

        for operation in operations:
            operation_name = str(
                operation.operation or "",
            ).upper()

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

            if not set_name:
                continue

            if set_name in seen_sets:
                continue

            seen_sets.add(
                set_name,
            )

            table_name = context.best_table_for_record(
                record_name,
            )

            select_columns = context.columns_for_record(
                record_name=record_name,
                table_name=table_name,
            )

            where_conditions = context.cursor_where_conditions(
                record_name=record_name,
                set_name=set_name,
                child_table=table_name,
            )

            order_by_columns = context.cursor_order_by_columns(
                record_name=record_name,
                set_name=set_name,
                child_table=table_name,
                fallback_columns=select_columns,
            )

            cursor_name = self._cursor_name(
                set_name,
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

    def _cursor_spec_messages(
        self,
        cursor_specs: list[dict[str, object]],
    ) -> list[str]:
        messages: list[str] = []

        for spec in cursor_specs:
            cursor_name = str(
                spec.get(
                    "cursor_name",
                    "",
                )
            )
            record_name = str(
                spec.get(
                    "record_name",
                    "",
                )
            )
            table_name = str(
                spec.get(
                    "table_name",
                    "",
                )
            )
            select_columns = list(
                spec.get(
                    "select_columns",
                    [],
                )
            )

            if not table_name:
                messages.append(
                    f"DB2 infrastructure: cursor {cursor_name} has no resolved DB2 table for record {record_name}."
                )

            if not select_columns:
                messages.append(
                    f"DB2 infrastructure: cursor {cursor_name} has no resolved SELECT columns for record {record_name}."
                )

        return messages

    def _build_infrastructure_block(
        self,
        include_names: list[str],
        cursor_specs: list[dict[str, object]],
    ) -> str:
        lines: list[str] = [
            "******************************************************************",
            self.DB2_BLOCK_MARKER,
            "******************************************************************",
        ]

        lines.extend(
            self._include_lines(
                include_names=[
                    "SQLERRWS",
                    "SQLCA",
                    *include_names,
                ]
            )
        )

        lines.extend(
            [
                "",
                "******************************************************************",
                "* DB2 SQL ERROR LOCATION",
                "******************************************************************",
                "01 SQL-LOCATION                 PIC X(40) VALUE SPACES.",
            ]
        )

        if cursor_specs:
            lines.extend(
                [
                    "",
                    "******************************************************************",
                    "* DB2 CURSOR END-OF-CURSOR FLAGS",
                    "******************************************************************",
                ]
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
                    f"01 {flag_name:<34} PIC X VALUE 'N'.",
                    f"   88 {not_eoc_name:<30} VALUE 'N'.",
                    f"   88 {eoc_name:<34} VALUE 'Y'.",
                ]
            )

        if cursor_specs:
            lines.extend(
                [
                    "",
                    "******************************************************************",
                    "* DB2 CURSOR DECLARATIONS",
                    "******************************************************************",
                ]
            )

        for spec in cursor_specs:
            lines.extend(
                self._cursor_declare_lines(
                    spec,
                )
            )
            lines.append(
                "",
            )

        return "\n".join(
            lines,
        ).rstrip() + "\n"

    def _include_lines(
        self,
        include_names: list[str],
    ) -> list[str]:
        lines: list[str] = []
        seen: set[str] = set()

        for include_name in include_names:
            normalized = NameNormalizer.normalize(
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
                    f"INCLUDE {normalized}",
                    "END-EXEC.",
                ]
            )

        return lines

    def _cursor_declare_lines(
        self,
        spec: dict[str, object],
    ) -> list[str]:
        cursor_name = str(
            spec.get(
                "cursor_name",
                "",
            )
        )
        table_name = str(
            spec.get(
                "table_name",
                "",
            )
        )
        select_columns = list(
            spec.get(
                "select_columns",
                [],
            )
        )
        where_conditions = list(
            spec.get(
                "where_conditions",
                [],
            )
        )
        order_by_columns = list(
            spec.get(
                "order_by_columns",
                [],
            )
        )

        if not table_name or not select_columns:
            return [
                f"* ERROR DB2: Unable to declare cursor {cursor_name}; missing DB2 table or selected columns.",
            ]

        lines: list[str] = [
            "EXEC SQL",
            f"DECLARE {cursor_name} CURSOR WITH HOLD FOR",
            "SELECT",
        ]

        lines.extend(
            self._comma_lines(
                select_columns,
                "    ",
            )
        )

        lines.append(
            f"FROM {table_name}",
        )

        if where_conditions:
            lines.append(
                "WHERE",
            )
            lines.extend(
                self._and_lines(
                    where_conditions,
                    "    ",
                )
            )

        if order_by_columns:
            lines.append(
                "ORDER BY",
            )
            lines.extend(
                self._comma_lines(
                    order_by_columns,
                    "    ",
                )
            )

        lines.extend(
            [
                "FOR READ ONLY",
                "END-EXEC.",
            ]
        )

        return lines

    def _insert_after_working_storage(
        self,
        text: str,
        block: str,
    ) -> tuple[str, bool]:
        pattern = re.compile(
            r"(^\s*(?:\d{6}\s+)?WORKING-STORAGE\s+SECTION\.\s*$)",
            flags=re.IGNORECASE | re.MULTILINE,
        )

        match = pattern.search(
            text,
        )

        if not match:
            return text, False

        insert_position = match.end()

        return (
            text[:insert_position]
            + "\n\n"
            + block
            + "\n"
            + text[insert_position:],
            True,
        )

    def _insert_before_procedure_division(
        self,
        text: str,
        block: str,
    ) -> tuple[str, bool]:
        pattern = re.compile(
            r"(^\s*(?:\d{6}\s+)?PROCEDURE\s+DIVISION\.\s*$)",
            flags=re.IGNORECASE | re.MULTILINE,
        )

        match = pattern.search(
            text,
        )

        if not match:
            return text, False

        insert_position = match.start()

        return (
            text[:insert_position]
            + block
            + "\n"
            + text[insert_position:],
            True,
        )

    def _insert_after_data_division(
        self,
        text: str,
        block: str,
    ) -> tuple[str, bool]:
        pattern = re.compile(
            r"(^\s*(?:\d{6}\s+)?DATA\s+DIVISION\.\s*$)",
            flags=re.IGNORECASE | re.MULTILINE,
        )

        match = pattern.search(
            text,
        )

        if not match:
            return text, False

        insert_position = match.end()

        generated_working_storage = (
            "\n"
            "WORKING-STORAGE SECTION.\n\n"
            + block
            + "\n"
        )

        return (
            text[:insert_position]
            + generated_working_storage
            + text[insert_position:],
            True,
        )

    def _cursor_name(
        self,
        set_name: str,
    ) -> str:
        normalized = NameNormalizer.normalize(
            set_name,
        )

        if not normalized:
            return "C-IDMS-SET"

        return "C-" + NameNormalizer.to_cobol(
            normalized,
        )

    def _comma_lines(
        self,
        items: list[str],
        indent: str,
    ) -> list[str]:
        output: list[str] = []

        for index, item in enumerate(items):
            suffix = "," if index < len(items) - 1 else ""
            output.append(
                f"{indent}{item}{suffix}",
            )

        return output

    def _and_lines(
        self,
        items: list[str],
        indent: str,
    ) -> list[str]:
        output: list[str] = []

        for index, item in enumerate(items):
            prefix = "AND " if index > 0 else ""
            output.append(
                f"{indent}{prefix}{item}",
            )

        return output


class Db2MappingContext:
    def __init__(
        self,
        mapping_rows: list[SheetMappingRow],
        dclgen_columns: list[DclgenColumn],
    ) -> None:
        self.mapping_rows = mapping_rows
        self.dclgen_columns = dclgen_columns
        self.rows_by_record = self._group_rows_by_record(
            mapping_rows,
        )
        self.dclgen_by_table = self._group_dclgen_by_table(
            dclgen_columns,
        )
        self.dclgen_host_lookup = self._build_dclgen_host_lookup(
            dclgen_columns,
        )

    def dclgen_include_names(
        self,
    ) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()

        for column in self.dclgen_columns:
            table = NameNormalizer.normalize(
                column.table_name,
            )

            if not table:
                continue

            if table in seen:
                continue

            seen.add(
                table,
            )
            names.append(
                table,
            )

        return names

    def best_table_for_record(
        self,
        record_name: str,
    ) -> str:
        rows = self.record_rows(
            record_name,
        )

        explicit_scores: dict[str, int] = {}

        for row in rows:
            table = NameNormalizer.normalize(
                row.new_db2_record,
            )
            column = NameNormalizer.normalize(
                row.new_db2_field_name,
            )

            if table and column:
                explicit_scores[table] = explicit_scores.get(
                    table,
                    0,
                ) + 1

        if explicit_scores:
            return max(
                explicit_scores.items(),
                key=lambda item: item[1],
            )[0]

        mapping_columns = {
            NameNormalizer.normalize(
                row.new_db2_field_name,
            )
            for row in rows
            if row.new_db2_field_name
        }

        dclgen_scores: dict[str, int] = {}

        for column in self.dclgen_columns:
            table = NameNormalizer.normalize(
                column.table_name,
            )
            db2_column = NameNormalizer.normalize(
                column.column_name,
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

        if len(self.dclgen_by_table) == 1:
            return next(
                iter(
                    self.dclgen_by_table.keys(),
                )
            )

        return ""

    def columns_for_record(
        self,
        record_name: str,
        table_name: str,
    ) -> list[str]:
        rows = self.record_rows(
            record_name,
        )

        normalized_table = NameNormalizer.normalize(
            table_name,
        )

        dclgen_columns = {
            NameNormalizer.normalize(
                column.column_name,
            )
            for column in self.dclgen_by_table.get(
                normalized_table,
                [],
            )
        }

        columns: list[str] = []
        seen: set[str] = set()

        for row in rows:
            row_table = NameNormalizer.normalize(
                row.new_db2_record,
            )

            if row_table and normalized_table and row_table != normalized_table:
                continue

            column = NameNormalizer.normalize(
                row.new_db2_field_name,
            )

            if not column:
                continue

            if column in seen:
                continue

            if dclgen_columns and column not in dclgen_columns:
                continue

            seen.add(
                column,
            )
            columns.append(
                column,
            )

        if columns:
            return columns

        if normalized_table and normalized_table in self.dclgen_by_table:
            return [
                NameNormalizer.normalize(
                    column.column_name,
                )
                for column in self.dclgen_by_table[normalized_table]
                if NameNormalizer.normalize(
                    column.column_name,
                )
            ]

        return []

    def host_variables_for_record(
        self,
        record_name: str,
        table_name: str,
    ) -> list[str]:
        columns = self.columns_for_record(
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

    def cursor_where_conditions(
        self,
        record_name: str,
        set_name: str,
        child_table: str,
    ) -> list[str]:
        relationship_rows = self.relationship_rows_for_cursor(
            record_name=record_name,
            set_name=set_name,
            child_table=child_table,
        )

        conditions: list[str] = []
        seen: set[str] = set()

        for row in relationship_rows:
            child_column = NameNormalizer.normalize(
                row.new_db2_field_name,
            )

            if not child_column:
                continue

            parent_host = self.parent_host_for_relation_row(
                row,
            )

            if not parent_host:
                continue

            condition = f"{child_column} = {parent_host}"

            if condition in seen:
                continue

            seen.add(
                condition,
            )
            conditions.append(
                condition,
            )

        return conditions

    def cursor_order_by_columns(
        self,
        record_name: str,
        set_name: str,
        child_table: str,
        fallback_columns: list[str],
    ) -> list[str]:
        relationship_rows = self.relationship_rows_for_cursor(
            record_name=record_name,
            set_name=set_name,
            child_table=child_table,
        )

        output: list[str] = []
        seen: set[str] = set()

        for row in relationship_rows:
            column = NameNormalizer.normalize(
                row.new_db2_field_name,
            )

            if not column:
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

        return fallback_columns[:2]

    def relationship_rows_for_cursor(
        self,
        record_name: str,
        set_name: str,
        child_table: str,
    ) -> list[SheetMappingRow]:
        normalized_set = NameNormalizer.normalize(
            set_name,
        )
        normalized_record = NameNormalizer.normalize(
            record_name,
        )
        normalized_record_no_suffix = NameNormalizer.remove_record_suffix(
            normalized_record,
        )
        normalized_child_table = NameNormalizer.normalize(
            child_table,
        )

        output: list[SheetMappingRow] = []

        for row in self.mapping_rows:
            relation = NameNormalizer.normalize(
                row.relation,
            )

            if relation != normalized_set:
                continue

            row_record = NameNormalizer.normalize(
                row.cobol_record_idms,
            )
            row_record_no_suffix = NameNormalizer.remove_record_suffix(
                row_record,
            )
            row_table = NameNormalizer.normalize(
                row.new_db2_record,
            )

            record_matches = row_record in {
                normalized_record,
                normalized_record_no_suffix,
            } or row_record_no_suffix in {
                normalized_record,
                normalized_record_no_suffix,
            }

            table_matches = bool(
                normalized_child_table
                and row_table
                and row_table == normalized_child_table
            )

            if record_matches or table_matches:
                output.append(
                    row,
                )

        return output

    def parent_host_for_relation_row(
        self,
        row: SheetMappingRow,
    ) -> str:
        parent_table = NameNormalizer.normalize(
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

        reference_field = self._source_field_from_cobol_zone(
            row.reference_field_name_copybook,
        )

        if reference_field:
            mapped_parent = self._find_mapping_by_source_field(
                reference_field,
            )

            if mapped_parent is not None:
                mapped_table = NameNormalizer.normalize(
                    mapped_parent.new_db2_record,
                )
                mapped_column = NameNormalizer.normalize(
                    mapped_parent.new_db2_field_name,
                )

                return self.host_for_column(
                    table_name=mapped_table,
                    column_name=mapped_column,
                )

        return ""

    def host_for_column(
        self,
        table_name: str,
        column_name: str,
    ) -> str:
        table = NameNormalizer.normalize(
            table_name,
        )
        column = NameNormalizer.normalize(
            column_name,
        )

        if not column:
            return ""

        host = self.dclgen_host_lookup.get(
            (
                table,
                column,
            )
        )

        if host:
            return ":" + host

        host = self.dclgen_host_lookup.get(
            (
                "",
                column,
            )
        )

        if host:
            return ":" + host

        if table:
            return f":DCL{table}.{NameNormalizer.to_cobol(column)}"

        return ":" + NameNormalizer.to_cobol(
            column,
        )

    def record_rows(
        self,
        record_name: str,
    ) -> list[SheetMappingRow]:
        normalized = NameNormalizer.normalize(
            record_name,
        )

        if normalized in self.rows_by_record:
            return self.rows_by_record[normalized]

        no_suffix = NameNormalizer.remove_record_suffix(
            normalized,
        )

        return self.rows_by_record.get(
            no_suffix,
            [],
        )

    def _group_rows_by_record(
        self,
        rows: list[SheetMappingRow],
    ) -> dict[str, list[SheetMappingRow]]:
        grouped: dict[str, list[SheetMappingRow]] = defaultdict(
            list,
        )

        for row in rows:
            record = NameNormalizer.normalize(
                row.cobol_record_idms,
            )

            if not record:
                continue

            grouped[record].append(
                row,
            )

            no_suffix = NameNormalizer.remove_record_suffix(
                record,
            )

            if no_suffix and no_suffix != record:
                grouped[no_suffix].append(
                    row,
                )

        return grouped

    def _group_dclgen_by_table(
        self,
        columns: list[DclgenColumn],
    ) -> dict[str, list[DclgenColumn]]:
        grouped: dict[str, list[DclgenColumn]] = defaultdict(
            list,
        )

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
            table = NameNormalizer.normalize(
                column.table_name,
            )
            db2_column = NameNormalizer.normalize(
                column.column_name,
            )
            host = NameNormalizer.to_cobol(
                column.cobol_host_name or column.column_name,
            )

            if not db2_column or not host:
                continue

            if table:
                lookup[(table, db2_column)] = f"DCL{table}.{host}"
                lookup[("", db2_column)] = f"DCL{table}.{host}"
            else:
                lookup[("", db2_column)] = host

        return lookup

    def _find_mapping_by_source_field(
        self,
        source_field: str,
    ) -> SheetMappingRow | None:
        normalized_source = NameNormalizer.normalize(
            source_field,
        )

        for row in self.mapping_rows:
            row_source = self._source_field_from_cobol_zone(
                row.cobol_zone,
            )

            if NameNormalizer.normalize(
                row_source,
            ) == normalized_source:
                if row.new_db2_field_name:
                    return row

        return None

    def _source_field_from_cobol_zone(
        self,
        value: str,
    ) -> str:
        text = str(
            value or "",
        ).strip()

        if not text:
            return ""

        text = " ".join(
            text.split(),
        )

        parts = text.split(
            " ",
            1,
        )

        if parts and parts[0].isdigit():
            if len(parts) > 1:
                return NameNormalizer.to_cobol(
                    parts[1],
                )

            return ""

        return NameNormalizer.to_cobol(
            text,
        )