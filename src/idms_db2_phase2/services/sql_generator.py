from collections import defaultdict

from idms_db2_phase2.domain.models import SheetMappingRow
from idms_db2_phase2.services.name_normalizer import NameNormalizer


class SqlGenerator:
    def __init__(
        self,
        rows: list[SheetMappingRow],
    ) -> None:
        self.rows = rows
        self.rows_by_record = self._group_rows_by_record(
            rows,
        )

    def select_by_key(
        self,
        record_name: str,
    ) -> list[str]:
        rows = self._record_rows(
            record_name,
        )

        table = self._table_name(
            rows,
        )

        if not table:
            return self._todo(
                f"Missing DB2 table mapping for {record_name}"
            )

        columns = self._mapped_columns(
            rows,
        )

        keys = self._key_rows(
            rows,
        )

        if not columns:
            return self._todo(
                f"Missing DB2 column mapping for {record_name}"
            )

        if not keys:
            keys = rows[:1]

        return [
            "           EXEC SQL",
            "                SELECT",
            *self._comma_lines(
                columns,
                "                    ",
            ),
            "                INTO",
            *self._comma_lines(
                self._host_variables(
                    rows,
                ),
                "                    ",
            ),
            f"                FROM {table}",
            "                WHERE",
            *self._and_lines(
                self._where_conditions(
                    keys,
                ),
                "                    ",
            ),
            "           END-EXEC.",
        ]

    def cursor_declare(
        self,
        record_name: str,
        set_name: str,
    ) -> list[str]:
        rows = self._record_rows(
            record_name,
        )

        table = self._table_name(
            rows,
        )

        columns = self._mapped_columns(
            rows,
        )

        if not table or not columns:
            return self._todo(
                f"Unable to declare cursor for set {set_name} and record {record_name}"
            )

        cursor_name = self.cursor_name(
            set_name,
        )

        where_conditions = self.cursor_where_conditions(
            record_name=record_name,
            set_name=set_name,
        )

        order_by_columns = self.cursor_order_by_columns(
            record_name=record_name,
            set_name=set_name,
            fallback_columns=columns,
        )

        return [
            "           EXEC SQL",
            f"                DECLARE {cursor_name} CURSOR FOR",
            "                SELECT",
            *self._comma_lines(
                columns,
                "                    ",
            ),
            f"                FROM {table}",
            "                WHERE",
            *self._and_lines(
                where_conditions,
                "                    ",
            ),
            "                ORDER BY",
            *self._comma_lines(
                order_by_columns,
                "                    ",
            ),
            "           END-EXEC.",
        ]

    def open_cursor(
        self,
        set_name: str,
    ) -> list[str]:
        cursor_name = self.cursor_name(
            set_name,
        )

        return [
            "           EXEC SQL",
            f"                OPEN {cursor_name}",
            "           END-EXEC.",
        ]

    def fetch_cursor(
        self,
        record_name: str,
        set_name: str,
    ) -> list[str]:
        rows = self._record_rows(
            record_name,
        )

        cursor_name = self.cursor_name(
            set_name,
        )

        hosts = self._host_variables(
            rows,
        )

        if not hosts:
            return [
                f"           * TODO DB2: FETCH for {cursor_name} was not generated because no host variables were mapped.",
                f"           * TODO DB2: Check Sheet Mapping rows for record {record_name}.",
                "           CONTINUE.",
            ]

        return [
            "           EXEC SQL",
            f"                FETCH {cursor_name}",
            "                INTO",
            *self._comma_lines(
                hosts,
                "                    ",
            ),
            "           END-EXEC.",
        ]

    def close_cursor(
        self,
        set_name: str,
    ) -> list[str]:
        cursor_name = self.cursor_name(
            set_name,
        )

        return [
            "           EXEC SQL",
            f"                CLOSE {cursor_name}",
            "           END-EXEC.",
        ]

    def insert(
        self,
        record_name: str,
    ) -> list[str]:
        rows = self._record_rows(
            record_name,
        )

        table = self._table_name(
            rows,
        )

        columns = self._mapped_columns(
            rows,
        )

        hosts = self._host_variables(
            rows,
        )

        if not table or not columns or not hosts:
            return self._todo(
                f"Missing INSERT mapping for {record_name}"
            )

        return [
            "           EXEC SQL",
            f"                INSERT INTO {table}",
            "                (",
            *self._comma_lines(
                columns,
                "                    ",
            ),
            "                )",
            "                VALUES",
            "                (",
            *self._comma_lines(
                hosts,
                "                    ",
            ),
            "                )",
            "           END-EXEC.",
        ]

    def update(
        self,
        record_name: str,
    ) -> list[str]:
        rows = self._record_rows(
            record_name,
        )

        table = self._table_name(
            rows,
        )

        key_rows = self._key_rows(
            rows,
        )

        if not table:
            return self._todo(
                f"Missing UPDATE mapping for {record_name}"
            )

        key_columns = {
            NameNormalizer.normalize(
                row.new_db2_field_name,
            )
            for row in key_rows
        }

        set_lines: list[str] = []

        for row in rows:
            column = NameNormalizer.normalize(
                row.new_db2_field_name,
            )

            if not column:
                continue

            if column in key_columns:
                continue

            host = self._host_for_row(
                row,
            )

            set_lines.append(
                f"{column} = {host}"
            )

        if not set_lines:
            set_lines.append(
                "/* TODO: Add update columns */"
            )

        if not key_rows:
            key_rows = rows[:1]

        return [
            "           EXEC SQL",
            f"                UPDATE {table}",
            "                SET",
            *self._comma_lines(
                set_lines,
                "                    ",
            ),
            "                WHERE",
            *self._and_lines(
                self._where_conditions(
                    key_rows,
                ),
                "                    ",
            ),
            "           END-EXEC.",
        ]

    def delete(
        self,
        record_name: str,
    ) -> list[str]:
        rows = self._record_rows(
            record_name,
        )

        table = self._table_name(
            rows,
        )

        key_rows = self._key_rows(
            rows,
        )

        if not table:
            return self._todo(
                f"Missing DELETE mapping for {record_name}"
            )

        if not key_rows:
            key_rows = rows[:1]

        return [
            "           EXEC SQL",
            f"                DELETE FROM {table}",
            "                WHERE",
            *self._and_lines(
                self._where_conditions(
                    key_rows,
                ),
                "                    ",
            ),
            "           END-EXEC.",
        ]

    def cursor_name(
        self,
        set_name: str,
    ) -> str:
        name = NameNormalizer.normalize(
            set_name,
        )

        if not name:
            return "C-IDMS-SET"

        return f"C-{NameNormalizer.to_cobol(name)}"

    def has_cursor_relationship_condition(
        self,
        record_name: str,
        set_name: str,
    ) -> bool:
        conditions = self.cursor_where_conditions(
            record_name=record_name,
            set_name=set_name,
        )

        for condition in conditions:
            if "TODO" not in condition.upper():
                return True

        return False

    def cursor_where_conditions(
        self,
        record_name: str,
        set_name: str,
    ) -> list[str]:
        relationship_rows = self._relationship_rows_for_cursor(
            record_name=record_name,
            set_name=set_name,
        )

        conditions: list[str] = []
        seen: set[str] = set()

        for row in relationship_rows:
            child_column = NameNormalizer.normalize(
                row.new_db2_field_name,
            )

            if not child_column:
                continue

            parent_host = self._parent_host_for_relationship_row(
                row,
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

        if conditions:
            return conditions

        return [
            "/* TODO: Add parent-child key condition from Sheet Mapping Relation/FK rows */ 1 = 1",
        ]

    def cursor_order_by_columns(
        self,
        record_name: str,
        set_name: str,
        fallback_columns: list[str],
    ) -> list[str]:
        relationship_rows = self._relationship_rows_for_cursor(
            record_name=record_name,
            set_name=set_name,
        )

        order_columns: list[str] = []
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

            order_columns.append(
                column,
            )

        if order_columns:
            return order_columns

        rows = self._record_rows(
            record_name,
        )

        key_rows = self._key_rows(
            rows,
        )

        for row in key_rows:
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

            order_columns.append(
                column,
            )

        if order_columns:
            return order_columns

        if fallback_columns:
            return [
                fallback_columns[0],
            ]

        return [
            "1",
        ]

    def _relationship_rows_for_cursor(
        self,
        record_name: str,
        set_name: str,
    ) -> list[SheetMappingRow]:
        normalized_set_name = NameNormalizer.normalize(
            set_name,
        )

        normalized_record_name = NameNormalizer.normalize(
            record_name,
        )

        normalized_record_no_suffix = NameNormalizer.remove_record_suffix(
            normalized_record_name,
        )

        record_rows = self._record_rows(
            record_name,
        )

        normalized_table_name = self._table_name(
            record_rows,
        )

        output: list[SheetMappingRow] = []

        for row in self.rows:
            relation = NameNormalizer.normalize(
                row.relation,
            )

            if relation != normalized_set_name:
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
                normalized_record_name,
                normalized_record_no_suffix,
            }

            record_matches = record_matches or row_record_no_suffix in {
                normalized_record_name,
                normalized_record_no_suffix,
            }

            table_matches = bool(
                normalized_table_name
                and row_table
                and row_table == normalized_table_name
            )

            if not record_matches and not table_matches:
                continue

            if not self._is_relationship_key_row(
                row,
            ):
                continue

            column = NameNormalizer.normalize(
                row.new_db2_field_name,
            )

            if not column:
                continue

            output.append(
                row,
            )

        return output

    def _is_relationship_key_row(
        self,
        row: SheetMappingRow,
    ) -> bool:
        db2_key = NameNormalizer.normalize(
            row.db2_key,
        )

        idms_key = NameNormalizer.normalize(
            row.idms_key,
        )

        relation = NameNormalizer.normalize(
            row.relation,
        )

        if "FK" in db2_key:
            return True

        if "SET" in idms_key:
            return True

        if relation and row.new_db2_field_name:
            return True

        return False

    def _parent_host_for_relationship_row(
        self,
        row: SheetMappingRow,
    ) -> str:
        parent_reference = (
            row.reference_field_name_copybook
            or row.cross_application_db2_field_name
            or row.new_db2_field_name
        )

        return ":" + NameNormalizer.to_cobol(
            parent_reference,
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

            if record:
                grouped[record].append(
                    row,
                )

                record_no_suffix = NameNormalizer.remove_record_suffix(
                    record,
                )

                if record_no_suffix and record_no_suffix != record:
                    grouped[record_no_suffix].append(
                        row,
                    )

        return grouped

    def _record_rows(
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

    def _table_name(
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

    def _mapped_columns(
        self,
        rows: list[SheetMappingRow],
    ) -> list[str]:
        columns: list[str] = []
        seen: set[str] = set()

        for row in rows:
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

            columns.append(
                column,
            )

        return columns

    def _host_variables(
        self,
        rows: list[SheetMappingRow],
    ) -> list[str]:
        hosts: list[str] = []
        seen: set[str] = set()

        for row in rows:
            column = NameNormalizer.normalize(
                row.new_db2_field_name,
            )

            if not column:
                continue

            host = self._host_for_row(
                row,
            )

            if host in seen:
                continue

            seen.add(
                host,
            )

            hosts.append(
                host,
            )

        return hosts

    def _host_for_row(
        self,
        row: SheetMappingRow,
    ) -> str:
        host = (
            row.reference_field_name_copybook
            or row.new_db2_field_name
        )

        return ":" + NameNormalizer.to_cobol(
            host,
        )

    def _key_rows(
        self,
        rows: list[SheetMappingRow],
    ) -> list[SheetMappingRow]:
        output: list[SheetMappingRow] = []

        for row in rows:
            if not NameNormalizer.normalize(
                row.new_db2_field_name,
            ):
                continue

            idms_key = NameNormalizer.normalize(
                row.idms_key,
            )

            db2_key = NameNormalizer.normalize(
                row.db2_key,
            )

            if idms_key or db2_key:
                output.append(
                    row,
                )

        return output

    def _where_conditions(
        self,
        rows: list[SheetMappingRow],
    ) -> list[str]:
        output: list[str] = []

        for row in rows:
            column = NameNormalizer.normalize(
                row.new_db2_field_name,
            )

            if not column:
                continue

            output.append(
                f"{column} = {self._host_for_row(row)}"
            )

        if not output:
            output.append(
                "/* TODO: Add key condition */ 1 = 1"
            )

        return output

    def _comma_lines(
        self,
        items: list[str],
        indent: str,
    ) -> list[str]:
        output: list[str] = []

        for index, item in enumerate(
            items,
        ):
            suffix = "," if index < len(items) - 1 else ""

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

        for index, item in enumerate(
            items,
        ):
            prefix = "AND " if index > 0 else ""

            output.append(
                f"{indent}{prefix}{item}"
            )

        return output

    def _todo(
        self,
        message: str,
    ) -> list[str]:
        return [
            f"           * TODO DB2: {message}.",
            "           CONTINUE.",
        ]