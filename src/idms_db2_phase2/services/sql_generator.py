from collections import defaultdict

from idms_db2_phase2.domain.models import DclgenColumn, SheetMappingRow
from idms_db2_phase2.services.name_normalizer import NameNormalizer


class SqlGenerator:
    def __init__(
        self,
        rows: list[SheetMappingRow],
        dclgen_columns: list[DclgenColumn] | None = None,
    ) -> None:
        self.rows = rows
        self.dclgen_columns = dclgen_columns or []
        self.rows_by_record = self._group_rows_by_record(
            rows,
        )
        self.dclgen_by_table = self._group_dclgen_by_table(
            self.dclgen_columns,
        )
        self.dclgen_host_lookup = self._build_dclgen_host_lookup(
            self.dclgen_columns,
        )

    def select_by_key(
        self,
        record_name: str,
    ) -> list[str]:
        rows = self._record_rows(
            record_name,
        )

        table = self._best_table_for_rows(
            rows,
        )

        if not table:
            return self._todo(
                f"Missing DB2 table mapping for {record_name}",
            )

        columns = self._mapped_columns(
            rows=rows,
            table_name=table,
        )

        if not columns:
            return self._todo(
                f"Missing DB2 column mapping for {record_name}",
            )

        hosts = self._host_variables(
            rows=rows,
            table_name=table,
        )

        if not hosts:
            return self._todo(
                f"Missing host variable mapping for {record_name}",
            )

        key_rows = self._key_rows(
            rows,
        )

        if not key_rows:
            key_rows = rows[:1]

        return [
            f"MOVE 'SELECT-{NameNormalizer.to_cobol(record_name)}' TO SQL-LOCATION.",
            "EXEC SQL",
            "SELECT",
            *self._comma_lines(
                columns,
                "    ",
            ),
            "INTO",
            *self._comma_lines(
                hosts,
                "    ",
            ),
            f"FROM {table}",
            "WHERE",
            *self._and_lines(
                self._where_conditions(
                    rows=key_rows,
                    table_name=table,
                ),
                "    ",
            ),
            "END-EXEC.",
        ]

    def insert(
        self,
        record_name: str,
    ) -> list[str]:
        rows = self._record_rows(
            record_name,
        )

        table = self._best_table_for_rows(
            rows,
        )

        columns = self._mapped_columns(
            rows=rows,
            table_name=table,
        )

        hosts = self._host_variables(
            rows=rows,
            table_name=table,
        )

        if not table or not columns or not hosts:
            return self._todo(
                f"Missing INSERT mapping for {record_name}",
            )

        return [
            f"MOVE 'INSERT-{NameNormalizer.to_cobol(record_name)}' TO SQL-LOCATION.",
            "EXEC SQL",
            f"INSERT INTO {table}",
            "(",
            *self._comma_lines(
                columns,
                "    ",
            ),
            ")",
            "VALUES",
            "(",
            *self._comma_lines(
                hosts,
                "    ",
            ),
            ")",
            "END-EXEC.",
        ]

    def update(
        self,
        record_name: str,
    ) -> list[str]:
        rows = self._record_rows(
            record_name,
        )

        table = self._best_table_for_rows(
            rows,
        )

        if not table:
            return self._todo(
                f"Missing UPDATE mapping for {record_name}",
            )

        key_rows = self._key_rows(
            rows,
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
                row=row,
                table_name=table,
            )

            if not host:
                continue

            set_lines.append(
                f"{column} = {host}",
            )

        if not set_lines:
            set_lines.append(
                "/* TODO: Add update columns */",
            )

        if not key_rows:
            key_rows = rows[:1]

        return [
            f"MOVE 'UPDATE-{NameNormalizer.to_cobol(record_name)}' TO SQL-LOCATION.",
            "EXEC SQL",
            f"UPDATE {table}",
            "SET",
            *self._comma_lines(
                set_lines,
                "    ",
            ),
            "WHERE",
            *self._and_lines(
                self._where_conditions(
                    rows=key_rows,
                    table_name=table,
                ),
                "    ",
            ),
            "END-EXEC.",
        ]

    def delete(
        self,
        record_name: str,
    ) -> list[str]:
        rows = self._record_rows(
            record_name,
        )

        table = self._best_table_for_rows(
            rows,
        )

        if not table:
            return self._todo(
                f"Missing DELETE mapping for {record_name}",
            )

        key_rows = self._key_rows(
            rows,
        )

        if not key_rows:
            key_rows = rows[:1]

        return [
            f"MOVE 'DELETE-{NameNormalizer.to_cobol(record_name)}' TO SQL-LOCATION.",
            "EXEC SQL",
            f"DELETE FROM {table}",
            "WHERE",
            *self._and_lines(
                self._where_conditions(
                    rows=key_rows,
                    table_name=table,
                ),
                "    ",
            ),
            "END-EXEC.",
        ]

    def open_cursor(
        self,
        set_name: str,
    ) -> list[str]:
        return [
            f"PERFORM {self.open_paragraph_name(set_name)}",
        ]

    def fetch_cursor(
        self,
        record_name: str,
        set_name: str,
    ) -> list[str]:
        return [
            f"PERFORM {self.fetch_paragraph_name(set_name)}",
        ]

    def close_cursor(
        self,
        set_name: str,
    ) -> list[str]:
        return [
            f"PERFORM {self.close_paragraph_name(set_name)}",
        ]

    def has_cursor_relationship_condition(
        self,
        record_name: str,
        set_name: str,
    ) -> bool:
        return bool(
            self.cursor_where_conditions(
                record_name=record_name,
                set_name=set_name,
            )
        )

    def cursor_declare(
        self,
        record_name: str,
        set_name: str,
    ) -> list[str]:
        rows = self._record_rows(
            record_name,
        )

        table = self._best_table_for_rows(
            rows,
        )

        columns = self._mapped_columns(
            rows=rows,
            table_name=table,
        )

        if not table or not columns:
            return [
                f"* TODO DB2: Unable to declare cursor for set {set_name}; missing DB2 table or columns for child record {record_name}.",
            ]

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

        lines: list[str] = [
            "EXEC SQL",
            f"DECLARE {cursor_name} CURSOR WITH HOLD FOR",
            "SELECT",
        ]

        lines.extend(
            self._comma_lines(
                columns,
                "    ",
            )
        )

        lines.append(
            f"FROM {table}",
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

    def cursor_where_conditions(
        self,
        record_name: str,
        set_name: str,
    ) -> list[str]:
        child_rows = self._record_rows(
            record_name,
        )

        child_table = self._best_table_for_rows(
            child_rows,
        )

        relationship_rows = self._relationship_rows_for_cursor(
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

            parent_host = self._parent_host_for_relation_row(
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
        fallback_columns: list[str],
    ) -> list[str]:
        relationship_rows = self._relationship_rows_for_cursor(
            record_name=record_name,
            set_name=set_name,
            child_table=self._best_table_for_rows(
                self._record_rows(
                    record_name,
                )
            ),
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

    def db2_infrastructure_block(
        self,
        used_cursor_records: dict[str, str],
    ) -> str:
        include_names = self._dclgen_include_names()

        cursor_names = [
            self.cursor_name(
                set_name,
            )
            for set_name in used_cursor_records.keys()
            if set_name
        ]

        lines: list[str] = [
            "******************************************************************",
            "* DB2 SQLCA, SQL ERROR WORKING STORAGE, DCLGEN INCLUDES, AND CURSOR FLAGS",
            "******************************************************************",
            "EXEC SQL",
            "INCLUDE SQLERRWS",
            "END-EXEC.",
            "EXEC SQL",
            "INCLUDE SQLCA",
            "END-EXEC.",
        ]

        for include_name in include_names:
            lines.extend(
                [
                    "EXEC SQL",
                    f"INCLUDE {include_name}",
                    "END-EXEC.",
                ]
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

        if cursor_names:
            lines.extend(
                [
                    "",
                    "******************************************************************",
                    "* DB2 CURSOR END-OF-CURSOR FLAGS",
                    "******************************************************************",
                ]
            )

        for cursor_name in cursor_names:
            lines.extend(
                [
                    f"01 WS-{cursor_name}-FLAG          PIC X VALUE 'N'.",
                    f"88 {cursor_name}-NOT-EOC       VALUE 'N'.",
                    f"88 {cursor_name}-EOC           VALUE 'Y'.",
                ]
            )

        if used_cursor_records:
            lines.extend(
                [
                    "",
                    "******************************************************************",
                    "* DB2 CURSOR DECLARATIONS",
                    "******************************************************************",
                ]
            )

        for set_name, record_name in used_cursor_records.items():
            lines.extend(
                self.cursor_declare(
                    record_name=record_name,
                    set_name=set_name,
                )
            )
            lines.append(
                "",
            )

        return "\n".join(
            lines,
        ).rstrip() + "\n"

    def cursor_paragraph_block(
        self,
        used_cursor_records: dict[str, str],
        sql_error_paragraph: str,
    ) -> str:
        lines: list[str] = [
            "",
            "******************************************************************",
            "* DB2 GENERATED CURSOR OPEN FETCH CLOSE PARAGRAPHS",
            "******************************************************************",
            "",
        ]

        for set_name, record_name in used_cursor_records.items():
            cursor_name = self.cursor_name(
                set_name,
            )

            open_paragraph = self.open_paragraph_name(
                set_name,
            )

            fetch_paragraph = self.fetch_paragraph_name(
                set_name,
            )

            close_paragraph = self.close_paragraph_name(
                set_name,
            )

            rows = self._record_rows(
                record_name,
            )

            table = self._best_table_for_rows(
                rows,
            )

            hosts = self._host_variables(
                rows=rows,
                table_name=table,
            )

            lines.extend(
                self._open_paragraph_lines(
                    cursor_name=cursor_name,
                    paragraph_name=open_paragraph,
                    sql_error_paragraph=sql_error_paragraph,
                )
            )

            lines.append(
                "",
            )

            lines.extend(
                self._fetch_paragraph_lines(
                    cursor_name=cursor_name,
                    paragraph_name=fetch_paragraph,
                    hosts=hosts,
                    sql_error_paragraph=sql_error_paragraph,
                )
            )

            lines.append(
                "",
            )

            lines.extend(
                self._close_paragraph_lines(
                    cursor_name=cursor_name,
                    paragraph_name=close_paragraph,
                    sql_error_paragraph=sql_error_paragraph,
                )
            )

            lines.append(
                "",
            )

        return "\n".join(
            lines,
        ).rstrip() + "\n"

    def cursor_name(
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

    def open_paragraph_name(
        self,
        set_name: str,
    ) -> str:
        return "OPEN-" + self.cursor_name(
            set_name,
        )

    def fetch_paragraph_name(
        self,
        set_name: str,
    ) -> str:
        return "FETCH-" + self.cursor_name(
            set_name,
        )

    def close_paragraph_name(
        self,
        set_name: str,
    ) -> str:
        return "CLOSE-" + self.cursor_name(
            set_name,
        )

    def _open_paragraph_lines(
        self,
        cursor_name: str,
        paragraph_name: str,
        sql_error_paragraph: str,
    ) -> list[str]:
        return [
            f"{paragraph_name}.",
            f"MOVE '{paragraph_name}' TO SQL-LOCATION.",
            "EXEC SQL",
            f"OPEN {cursor_name}",
            "END-EXEC.",
            "EVALUATE SQLCODE",
            "WHEN ZERO",
            f"SET {cursor_name}-NOT-EOC TO TRUE.",
            "WHEN OTHER",
            f"DISPLAY 'ERROR WHILE OPENING CURSOR {cursor_name}'.",
            f"PERFORM {sql_error_paragraph}.",
            "END-EVALUATE.",
        ]

    def _fetch_paragraph_lines(
        self,
        cursor_name: str,
        paragraph_name: str,
        hosts: list[str],
        sql_error_paragraph: str,
    ) -> list[str]:
        lines: list[str] = [
            f"{paragraph_name}.",
            f"MOVE '{paragraph_name}' TO SQL-LOCATION.",
        ]

        if not hosts:
            lines.extend(
                [
                    f"* TODO DB2: No FETCH host variables mapped for {cursor_name}.",
                    "CONTINUE.",
                ]
            )

            return lines

        lines.extend(
            [
                "EXEC SQL",
                f"FETCH {cursor_name}",
                "INTO",
            ]
        )

        lines.extend(
            self._comma_lines(
                hosts,
                "    ",
            )
        )

        lines.extend(
            [
                "END-EXEC.",
                "EVALUATE SQLCODE",
                "WHEN ZERO",
                "CONTINUE.",
                "WHEN 100",
                f"SET {cursor_name}-EOC TO TRUE.",
                "WHEN OTHER",
                f"DISPLAY 'ERROR WHILE FETCHING CURSOR {cursor_name}'.",
                f"PERFORM {sql_error_paragraph}.",
                "END-EVALUATE.",
            ]
        )

        return lines

    def _close_paragraph_lines(
        self,
        cursor_name: str,
        paragraph_name: str,
        sql_error_paragraph: str,
    ) -> list[str]:
        return [
            f"{paragraph_name}.",
            f"MOVE '{paragraph_name}' TO SQL-LOCATION.",
            "EXEC SQL",
            f"CLOSE {cursor_name}",
            "END-EXEC.",
            "EVALUATE SQLCODE",
            "WHEN ZERO",
            "CONTINUE.",
            "WHEN OTHER",
            f"DISPLAY 'ERROR WHILE CLOSING CURSOR {cursor_name}'.",
            f"PERFORM {sql_error_paragraph}.",
            "END-EVALUATE.",
        ]

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

            host_reference = (
                f"DCL{table}.{host}"
                if table
                else host
            )

            lookup[
                (
                    table,
                    db2_column,
                )
            ] = host_reference

            lookup[
                (
                    "",
                    db2_column,
                )
            ] = host_reference

        return lookup

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

    def _best_table_for_rows(
        self,
        rows: list[SheetMappingRow],
    ) -> str:
        explicit_table_scores: dict[str, int] = {}

        for row in rows:
            table = NameNormalizer.normalize(
                row.new_db2_record,
            )

            column = NameNormalizer.normalize(
                row.new_db2_field_name,
            )

            if not table:
                continue

            if column:
                explicit_table_scores[table] = explicit_table_scores.get(
                    table,
                    0,
                ) + 1

        if explicit_table_scores:
            return max(
                explicit_table_scores.items(),
                key=lambda item: item[1],
            )[0]

        mapping_columns = {
            NameNormalizer.normalize(
                row.new_db2_field_name,
            )
            for row in rows
            if row.new_db2_field_name
        }

        dclgen_table_scores: dict[str, int] = {}

        for column in self.dclgen_columns:
            table = NameNormalizer.normalize(
                column.table_name,
            )

            db2_column = NameNormalizer.normalize(
                column.column_name,
            )

            if not table or not db2_column:
                continue

            if db2_column in mapping_columns:
                dclgen_table_scores[table] = dclgen_table_scores.get(
                    table,
                    0,
                ) + 1

        if dclgen_table_scores:
            return max(
                dclgen_table_scores.items(),
                key=lambda item: item[1],
            )[0]

        return ""

    def _mapped_columns(
        self,
        rows: list[SheetMappingRow],
        table_name: str,
    ) -> list[str]:
        output: list[str] = []
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

            output.append(
                column,
            )

        return output

    def _host_variables(
        self,
        rows: list[SheetMappingRow],
        table_name: str,
    ) -> list[str]:
        hosts: list[str] = []
        seen: set[str] = set()

        normalized_table = NameNormalizer.normalize(
            table_name,
        )

        for row in rows:
            column = NameNormalizer.normalize(
                row.new_db2_field_name,
            )

            if not column:
                continue

            host = self._host_for_row(
                row=row,
                table_name=normalized_table,
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

    def _host_for_row(
        self,
        row: SheetMappingRow,
        table_name: str,
    ) -> str:
        column = NameNormalizer.normalize(
            row.new_db2_field_name,
        )

        if not column:
            return ""

        normalized_table = NameNormalizer.normalize(
            table_name,
        )

        host = self.dclgen_host_lookup.get(
            (
                normalized_table,
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

        if normalized_table:
            return f":DCL{normalized_table}.{NameNormalizer.to_cobol(column)}"

        return ":" + NameNormalizer.to_cobol(
            column,
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

            if (
                NameNormalizer.normalize(
                    row.idms_key,
                )
                or NameNormalizer.normalize(
                    row.db2_key,
                )
            ):
                output.append(
                    row,
                )

        return output

    def _where_conditions(
        self,
        rows: list[SheetMappingRow],
        table_name: str,
    ) -> list[str]:
        output: list[str] = []

        for row in rows:
            column = NameNormalizer.normalize(
                row.new_db2_field_name,
            )

            if not column:
                continue

            host = self._host_for_row(
                row=row,
                table_name=table_name,
            )

            if not host:
                continue

            output.append(
                f"{column} = {host}",
            )

        if not output:
            output.append(
                "/* TODO: Add key condition */ 1 = 1",
            )

        return output

    def _relationship_rows_for_cursor(
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

        for row in self.rows:
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

            if not record_matches and not table_matches:
                continue

            db2_key = NameNormalizer.normalize(
                row.db2_key,
            )

            idms_key = NameNormalizer.normalize(
                row.idms_key,
            )

            if "FK" in db2_key or "SET" in idms_key or relation:
                output.append(
                    row,
                )

        return output

    def _parent_host_for_relation_row(
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
            host = self.dclgen_host_lookup.get(
                (
                    parent_table,
                    parent_column,
                )
            )

            if host:
                return ":" + host

            return f":DCL{parent_table}.{NameNormalizer.to_cobol(parent_column)}"

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

                host = self.dclgen_host_lookup.get(
                    (
                        mapped_table,
                        mapped_column,
                    )
                )

                if host:
                    return ":" + host

                if mapped_table and mapped_column:
                    return f":DCL{mapped_table}.{NameNormalizer.to_cobol(mapped_column)}"

        fallback_column = NameNormalizer.normalize(
            row.new_db2_field_name,
        )

        host = self.dclgen_host_lookup.get(
            (
                "",
                fallback_column,
            )
        )

        if host:
            return ":" + host

        return ""

    def _find_mapping_by_source_field(
        self,
        source_field: str,
    ) -> SheetMappingRow | None:
        normalized_source = NameNormalizer.normalize(
            source_field,
        )

        for row in self.rows:
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

    def _dclgen_include_names(
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

    def _todo(
        self,
        message: str,
    ) -> list[str]:
        return [
            f"* TODO DB2: {message}.",
            "CONTINUE.",
        ]