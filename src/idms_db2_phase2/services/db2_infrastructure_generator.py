import re

from idms_db2_phase2.domain.models import DclgenColumn, IdmsOperation, SheetMappingRow
from idms_db2_phase2.services.name_normalizer import NameNormalizer


class Db2InfrastructureGenerator:
    """
    Adds generic production DB2 infrastructure to converted COBOL.

    Generic rules:
    - No hardcoded program names.
    - No hardcoded DB2 table/view names.
    - No hardcoded cursor names.
    - No hardcoded set names.
    - DCLGEN include names come from uploaded DCLGEN table names.
    - Cursor names come from IDMS set names.
    - Cursor declarations come from Sheet Mapping + DCLGEN column matching.
    """

    DB2_BLOCK_MARKER = "* DB2 SQLCA, SQL ERROR WORKING STORAGE, DCLGEN INCLUDES, AND CURSOR FLAGS"

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

        cursor_specs = self._cursor_specs(
            operations=operations,
            mapping_rows=mapping_rows,
            dclgen_columns=dclgen_columns,
        )

        dclgen_include_names = self._dclgen_include_names(
            dclgen_columns=dclgen_columns,
        )

        block = self._build_infrastructure_block(
            dclgen_include_names=dclgen_include_names,
            cursor_specs=cursor_specs,
        )

        text, inserted = self._insert_after_working_storage(
            text=cobol_text,
            block=block,
        )

        if inserted:
            messages.append(
                "DB2 infrastructure: inserted SQLERRWS, SQLCA, DCLGEN includes, SQL-LOCATION, cursor flags, and cursor declarations."
            )
            return text, messages

        text, inserted = self._insert_before_procedure_division(
            text=cobol_text,
            block=block,
        )

        if inserted:
            messages.append(
                "DB2 infrastructure: WORKING-STORAGE SECTION not found; inserted block before PROCEDURE DIVISION."
            )
            return text, messages

        messages.append(
            "DB2 infrastructure: WORKING-STORAGE and PROCEDURE DIVISION not found; inserted block at top of file."
        )

        return block + "\n\n" + cobol_text, messages

    def _build_infrastructure_block(
        self,
        dclgen_include_names: list[str],
        cursor_specs: list[dict[str, object]],
    ) -> str:
        lines: list[str] = [
            "",
            "      ******************************************************************",
            f"      * {self.DB2_BLOCK_MARKER}",
            "      ******************************************************************",
        ]

        lines.extend(
            self._include_lines(
                include_names=[
                    "SQLERRWS",
                    "SQLCA",
                    *dclgen_include_names,
                ]
            )
        )

        lines.extend(
            [
                "",
                "      ******************************************************************",
                "      * DB2 SQL ERROR LOCATION",
                "      ******************************************************************",
                "       01 SQL-LOCATION                 PIC X(40) VALUE SPACES.",
            ]
        )

        if cursor_specs:
            lines.extend(
                [
                    "",
                    "      ******************************************************************",
                    "      * DB2 CURSOR END-OF-CURSOR FLAGS",
                    "      ******************************************************************",
                ]
            )

            for spec in cursor_specs:
                cursor_name = str(spec["cursor_name"])
                flag_name = f"WS-{cursor_name}-FLAG"
                not_eoc_name = f"{cursor_name}-NOT-EOC"
                eoc_name = f"{cursor_name}-EOC"

                lines.extend(
                    [
                        f"       01 {flag_name:<30} PIC X VALUE 'N'.",
                        f"          88 {not_eoc_name:<27} VALUE 'N'.",
                        f"          88 {eoc_name:<31} VALUE 'Y'.",
                        "",
                    ]
                )

            lines.extend(
                [
                    "      ******************************************************************",
                    "      * DB2 CURSOR DECLARATIONS",
                    "      ******************************************************************",
                ]
            )

            for spec in cursor_specs:
                lines.extend(
                    self._cursor_declare_lines(
                        spec=spec,
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
                    "           EXEC SQL",
                    f"                INCLUDE {normalized}",
                    "           END-EXEC.",
                ]
            )

        return lines

    def _cursor_declare_lines(
        self,
        spec: dict[str, object],
    ) -> list[str]:
        cursor_name = str(spec["cursor_name"])
        table_name = str(spec["table_name"])
        select_columns = list(spec["select_columns"])
        where_conditions = list(spec["where_conditions"])
        order_by_columns = list(spec["order_by_columns"])

        if not table_name or not select_columns:
            return [
                f"      * TODO DB2: Unable to declare cursor {cursor_name}; missing table or selected columns.",
            ]

        lines: list[str] = [
            "           EXEC SQL",
            f"                DECLARE {cursor_name} CURSOR WITH HOLD FOR",
            "                SELECT",
        ]

        for index, column in enumerate(select_columns):
            prefix = "                    " if index == 0 else "                   ,"
            lines.append(
                f"{prefix}{column}"
            )

        lines.append(
            f"                FROM {table_name}"
        )

        if where_conditions:
            lines.append(
                "                WHERE"
            )

            for index, condition in enumerate(where_conditions):
                prefix = "                    " if index == 0 else "                  AND "
                lines.append(
                    f"{prefix}{condition}"
                )

        if order_by_columns:
            lines.append(
                "                ORDER BY"
            )

            for index, column in enumerate(order_by_columns):
                prefix = "                    " if index == 0 else "                   ,"
                lines.append(
                    f"{prefix}{column}"
                )

        lines.extend(
            [
                "                FOR READ ONLY",
                "           END-EXEC.",
            ]
        )

        return lines

    def _cursor_specs(
        self,
        operations: list[IdmsOperation],
        mapping_rows: list[SheetMappingRow],
        dclgen_columns: list[DclgenColumn],
    ) -> list[dict[str, object]]:
        specs: list[dict[str, object]] = []
        seen: set[str] = set()

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

            if set_name in seen:
                continue

            seen.add(
                set_name,
            )

            record_rows = self._rows_for_record(
                record_name=record_name,
                mapping_rows=mapping_rows,
            )

            table_name = self._best_dclgen_table_for_rows(
                rows=record_rows,
                dclgen_columns=dclgen_columns,
            )

            if not table_name:
                table_name = self._first_mapping_table(
                    rows=record_rows,
                )

            select_columns = self._select_columns(
                rows=record_rows,
                table_name=table_name,
                dclgen_columns=dclgen_columns,
            )

            where_conditions = self._where_conditions(
                set_name=set_name,
                record_name=record_name,
                mapping_rows=mapping_rows,
                dclgen_columns=dclgen_columns,
            )

            order_by_columns = self._order_by_columns(
                rows=record_rows,
                where_conditions=where_conditions,
            )

            cursor_name = self._cursor_name(
                set_name=set_name,
            )

            specs.append(
                {
                    "set_name": set_name,
                    "record_name": record_name,
                    "cursor_name": cursor_name,
                    "table_name": table_name,
                    "select_columns": select_columns,
                    "where_conditions": where_conditions,
                    "order_by_columns": order_by_columns,
                }
            )

        return specs

    def _dclgen_include_names(
        self,
        dclgen_columns: list[DclgenColumn],
    ) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()

        for column in dclgen_columns:
            table_name = NameNormalizer.normalize(
                column.table_name,
            )

            if not table_name:
                continue

            if table_name in seen:
                continue

            seen.add(
                table_name,
            )
            names.append(
                table_name,
            )

        return names

    def _rows_for_record(
        self,
        record_name: str,
        mapping_rows: list[SheetMappingRow],
    ) -> list[SheetMappingRow]:
        normalized_record = NameNormalizer.normalize(
            record_name,
        )
        no_suffix = NameNormalizer.remove_record_suffix(
            normalized_record,
        )

        output: list[SheetMappingRow] = []

        for row in mapping_rows:
            row_record = NameNormalizer.normalize(
                row.cobol_record_idms,
            )
            row_record_no_suffix = NameNormalizer.remove_record_suffix(
                row_record,
            )

            if row_record in {normalized_record, no_suffix}:
                output.append(row)
                continue

            if row_record_no_suffix in {normalized_record, no_suffix}:
                output.append(row)

        return output

    def _best_dclgen_table_for_rows(
        self,
        rows: list[SheetMappingRow],
        dclgen_columns: list[DclgenColumn],
    ) -> str:
        mapping_columns = {
            NameNormalizer.normalize(
                row.new_db2_field_name,
            )
            for row in rows
            if row.new_db2_field_name
        }

        if not mapping_columns:
            return ""

        score_by_table: dict[str, int] = {}

        for column in dclgen_columns:
            table = NameNormalizer.normalize(
                column.table_name,
            )
            db2_column = NameNormalizer.normalize(
                column.column_name,
            )

            if not table or not db2_column:
                continue

            if db2_column in mapping_columns:
                score_by_table[table] = score_by_table.get(
                    table,
                    0,
                ) + 1

        if not score_by_table:
            return ""

        return max(
            score_by_table.items(),
            key=lambda item: item[1],
        )[0]

    def _first_mapping_table(
        self,
        rows: list[SheetMappingRow],
    ) -> str:
        for row in rows:
            table = NameNormalizer.normalize(
                row.new_db2_record,
            )

            if table:
                return table

        return ""

    def _select_columns(
        self,
        rows: list[SheetMappingRow],
        table_name: str,
        dclgen_columns: list[DclgenColumn],
    ) -> list[str]:
        mapping_columns: list[str] = []
        seen: set[str] = set()

        dclgen_column_set = {
            NameNormalizer.normalize(column.column_name)
            for column in dclgen_columns
            if NameNormalizer.normalize(column.table_name) == NameNormalizer.normalize(table_name)
        }

        for row in rows:
            column = NameNormalizer.normalize(
                row.new_db2_field_name,
            )

            if not column:
                continue

            if column in seen:
                continue

            if dclgen_column_set and column not in dclgen_column_set:
                continue

            seen.add(
                column,
            )
            mapping_columns.append(
                column,
            )

        return mapping_columns

    def _where_conditions(
        self,
        set_name: str,
        record_name: str,
        mapping_rows: list[SheetMappingRow],
        dclgen_columns: list[DclgenColumn],
    ) -> list[str]:
        relation_rows: list[SheetMappingRow] = []

        for row in mapping_rows:
            relation = NameNormalizer.normalize(
                row.relation,
            )

            if relation != set_name:
                continue

            db2_key = NameNormalizer.normalize(
                row.db2_key,
            )

            idms_key = NameNormalizer.normalize(
                row.idms_key,
            )

            if "FK" in db2_key or "SET" in idms_key:
                relation_rows.append(
                    row,
                )

        conditions: list[str] = []
        seen: set[str] = set()

        for row in relation_rows:
            child_column = NameNormalizer.normalize(
                row.new_db2_field_name,
            )

            if not child_column:
                continue

            parent_column = NameNormalizer.normalize(
                row.cross_application_db2_field_name
                or row.reference_field_name_copybook
                or row.new_db2_field_name
            )

            parent_host = self._host_for_db2_column(
                db2_column=parent_column,
                dclgen_columns=dclgen_columns,
            )

            if not parent_host:
                parent_host = ":" + NameNormalizer.to_cobol(
                    parent_column,
                )

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

    def _host_for_db2_column(
        self,
        db2_column: str,
        dclgen_columns: list[DclgenColumn],
    ) -> str:
        normalized_column = NameNormalizer.normalize(
            db2_column,
        )

        if not normalized_column:
            return ""

        for column in dclgen_columns:
            if NameNormalizer.normalize(column.column_name) != normalized_column:
                continue

            table_name = NameNormalizer.normalize(
                column.table_name,
            )

            host_name = NameNormalizer.to_cobol(
                column.cobol_host_name or column.column_name,
            )

            if table_name and host_name:
                return f":DCL{table_name}.{host_name}"

        return ""

    def _order_by_columns(
        self,
        rows: list[SheetMappingRow],
        where_conditions: list[str],
    ) -> list[str]:
        order_columns: list[str] = []
        seen: set[str] = set()

        for condition in where_conditions:
            column = condition.split(
                "=",
                1,
            )[0].strip()

            if column and column not in seen:
                seen.add(
                    column,
                )
                order_columns.append(
                    column,
                )

        if order_columns:
            return order_columns

        for row in rows:
            db2_key = NameNormalizer.normalize(
                row.db2_key,
            )

            column = NameNormalizer.normalize(
                row.new_db2_field_name,
            )

            if not column:
                continue

            if not db2_key:
                continue

            if column in seen:
                continue

            seen.add(
                column,
            )
            order_columns.append(
                column,
            )

        return order_columns

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

    def _insert_after_working_storage(
        self,
        text: str,
        block: str,
    ) -> tuple[str, bool]:
        pattern = re.compile(
            r"(^\s*WORKING-STORAGE\s+SECTION\.\s*$)",
            flags=re.IGNORECASE | re.MULTILINE,
        )
        match = pattern.search(text)

        if not match:
            return text, False

        insert_position = match.end()

        return (
            text[:insert_position]
            + "\n"
            + block
            + text[insert_position:],
            True,
        )

    def _insert_before_procedure_division(
        self,
        text: str,
        block: str,
    ) -> tuple[str, bool]:
        pattern = re.compile(
            r"(^\s*PROCEDURE\s+DIVISION\.\s*$)",
            flags=re.IGNORECASE | re.MULTILINE,
        )
        match = pattern.search(text)

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