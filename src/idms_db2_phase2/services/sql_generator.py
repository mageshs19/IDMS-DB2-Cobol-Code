from __future__ import annotations

from collections import defaultdict
import re

from idms_db2_phase2.domain.models import DclgenColumn, SheetMappingRow
from idms_db2_phase2.services.name_normalizer import NameNormalizer


class SqlGenerator:
    """
    Generates DB2 embedded SQL snippets for converted COBOL.

    Core rules:
    - Sheet Mapping is the authority for DB2 record/table names.
    - Sheet Mapping is the authority for DB2 column names.
    - DCLGEN is the authority for COBOL host variable spelling and group names.
    - If Sheet Mapping uses TB but uploaded DCLGEN uses TV, generated SQL uses TV.
    - UPDATE generation is conservative/manual-style, not broad all-column update.
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

    UPDATE_AUDIT_COLUMN_PREFIXES = (
        "TS_UPDATE",
        "ID_USERID",
        "NR_USERID",
        "ID_USER",
        "NR_USER",
    )

    INSERT_EXCLUDE_AUDIT_PREFIXES = (
        "TS_UPDATE",
    )

    IGNORE_FIELD_TOKENS = {
        "PIC",
        "COMP",
        "COMP_3",
        "COMP-3",
        "REDEFINES",
        "OCCURS",
        "VALUE",
        "GROUP",
        "FILLER",
        "CALC",
    }

    DATE_COLUMN_PREFIXES = (
        "DA_",
        "DT_",
    )

    def __init__(
        self,
        rows: list[SheetMappingRow],
        dclgen_columns: list[DclgenColumn] | None = None,
    ) -> None:
        self.rows = rows or []
        self.dclgen_columns = dclgen_columns or []

        self.rows_by_record = self._group_rows_by_record(self.rows)
        self.dclgen_by_table = self._group_dclgen_by_table(self.dclgen_columns)
        self.dclgen_host_lookup = self._build_dclgen_host_lookup(self.dclgen_columns)
        self.dclgen_group_lookup = self._build_dclgen_group_lookup(self.dclgen_columns)
        self.table_catalog = self._build_table_catalog()

        self.cursor_set_record_cache: dict[str, str] = {}
        self.cursor_order_by_set: dict[str, int] = {}
        self.changed_fields_by_record: dict[str, set[str]] = defaultdict(set)

    def remember_changed_field(
        self,
        record_name: str,
        source_field: str,
    ) -> None:
        record = NameNormalizer.normalize(record_name)
        field = NameNormalizer.normalize(source_field)

        if record and field:
            self.changed_fields_by_record[record].add(field)

    def select_by_key(
        self,
        record_name: str,
    ) -> list[str]:
        rows = self._record_rows(record_name)

        table = self._best_table_for_rows(
            rows=rows,
            record_name=record_name,
        )

        table = self._resolve_dclgen_table(table)

        if not table:
            return self._missing_mapping(
                record_name=record_name,
                reason="Missing DB2 table mapping",
            )

        columns = self._mapped_columns(
            rows=rows,
            table_name=table,
        )

        if not columns:
            columns = self._dclgen_columns_for_table(table)

        columns = self._filter_select_columns(columns)

        if not columns:
            return self._missing_mapping(
                record_name=record_name,
                reason="Missing DB2 column mapping",
            )

        hosts = self._host_variables(
            table_name=table,
            columns=columns,
        )

        if not hosts:
            return self._missing_mapping(
                record_name=record_name,
                reason="Missing host variable mapping",
            )

        key_rows = self._key_rows(rows)

        if not key_rows:
            key_rows = self._infer_key_rows_from_columns(
                rows=rows,
                columns=columns,
            )

        where_conditions = self._where_conditions(
            rows=key_rows,
            table_name=table,
        )

        if not where_conditions:
            where_conditions = self._fallback_where_conditions(
                columns=columns,
                table_name=table,
            )

        lines: list[str] = [
            f"MOVE 'SELECT-{NameNormalizer.to_cobol(record_name)}' TO SQL-LOCATION.",
            "EXEC SQL",
            "    SELECT",
        ]

        lines.extend(
            self._comma_lines(
                items=columns,
                indent="        ",
            )
        )

        lines.append("    INTO")

        lines.extend(
            self._comma_lines(
                items=hosts,
                indent="        ",
            )
        )

        lines.append(f"    FROM {table}")

        if where_conditions:
            lines.append("    WHERE")
            lines.extend(
                self._and_lines(
                    items=where_conditions,
                    indent="        ",
                )
            )

        lines.append("END-EXEC.")

        return lines

    def insert(
        self,
        record_name: str,
    ) -> list[str]:
        rows = self._record_rows(record_name)

        table = self._best_table_for_rows(
            rows=rows,
            record_name=record_name,
        )

        table = self._resolve_dclgen_table(table)

        if not table:
            return self._missing_mapping(
                record_name=record_name,
                reason="Missing INSERT table mapping",
            )

        columns = self._mapped_columns(
            rows=rows,
            table_name=table,
        )

        if not columns:
            columns = self._dclgen_columns_for_table(table)

        columns = [
            column
            for column in columns
            if column
            and self._column_exists_in_table(
                table_name=table,
                column_name=column,
            )
            and not self._is_insert_excluded_audit_column(column)
        ]

        if not columns:
            return self._missing_mapping(
                record_name=record_name,
                reason="Missing INSERT column mapping",
            )

        hosts = self._host_variables(
            table_name=table,
            columns=columns,
        )

        if not hosts:
            return self._missing_mapping(
                record_name=record_name,
                reason="Missing INSERT host mapping",
            )

        lines: list[str] = [
            f"MOVE 'INSERT-{NameNormalizer.to_cobol(record_name)}' TO SQL-LOCATION.",
            "EXEC SQL",
            f"    INSERT INTO {table}",
            "    (",
        ]

        lines.extend(
            self._comma_lines(
                items=columns,
                indent="        ",
            )
        )

        lines.extend(
            [
                "    )",
                "    VALUES",
                "    (",
            ]
        )

        lines.extend(
            self._comma_lines(
                items=hosts,
                indent="        ",
            )
        )

        lines.extend(
            [
                "    )",
                "END-EXEC.",
            ]
        )

        return lines

    def update(
        self,
        record_name: str,
        changed_fields: list[str] | None = None,
    ) -> list[str]:
        rows = self._record_rows(record_name)

        table = self._best_table_for_rows(
            rows=rows,
            record_name=record_name,
        )

        table = self._resolve_dclgen_table(table)

        if not table:
            return self._missing_mapping(
                record_name=record_name,
                reason="Missing UPDATE table mapping",
            )

        columns = self._mapped_columns(
            rows=rows,
            table_name=table,
        )

        if not columns:
            columns = self._dclgen_columns_for_table(table)

        if not columns:
            return self._missing_mapping(
                record_name=record_name,
                reason="Missing UPDATE column mapping",
            )

        key_rows = self._key_rows(rows)

        if not key_rows:
            key_rows = self._infer_key_rows_from_columns(
                rows=rows,
                columns=columns,
            )

        key_columns = {
            NameNormalizer.normalize(
                self._first_non_empty(
                    row.new_db2_field_name,
                    row.cross_application_db2_field_name,
                )
            )
            for row in key_rows
        }

        set_columns = self._update_set_columns(
            record_name=record_name,
            rows=rows,
            table_name=table,
            all_columns=columns,
            key_columns=key_columns,
            changed_fields=changed_fields,
        )

        set_lines: list[str] = []

        for column in set_columns:
            host = self._host_for_column(
                table_name=table,
                column_name=column,
            )

            if not host:
                continue

            set_lines.append(f"{column} = {host}")

        where_conditions = self._where_conditions(
            rows=key_rows,
            table_name=table,
        )

        if not where_conditions:
            where_conditions = self._fallback_where_conditions(
                columns=columns,
                table_name=table,
            )

        if not set_lines:
            return self._missing_mapping(
                record_name=record_name,
                reason="Missing UPDATE SET mapping",
            )

        if not where_conditions:
            return self._missing_mapping(
                record_name=record_name,
                reason="Missing UPDATE key mapping",
            )

        lines: list[str] = [
            f"MOVE 'UPDATE-{NameNormalizer.to_cobol(record_name)}' TO SQL-LOCATION.",
            "EXEC SQL",
            f"    UPDATE {table}",
            "        SET",
        ]

        lines.extend(
            self._comma_lines(
                items=set_lines,
                indent="        ",
            )
        )

        lines.append("    WHERE")

        lines.extend(
            self._and_lines(
                items=where_conditions,
                indent="        ",
            )
        )

        lines.append("END-EXEC.")

        return lines

    def delete(
        self,
        record_name: str,
    ) -> list[str]:
        rows = self._record_rows(record_name)

        table = self._best_table_for_rows(
            rows=rows,
            record_name=record_name,
        )

        table = self._resolve_dclgen_table(table)

        if not table:
            return self._missing_mapping(
                record_name=record_name,
                reason="Missing DELETE table mapping",
            )

        key_rows = self._key_rows(rows)

        if not key_rows:
            columns = self._mapped_columns(
                rows=rows,
                table_name=table,
            )
            key_rows = self._infer_key_rows_from_columns(
                rows=rows,
                columns=columns,
            )

        where_conditions = self._where_conditions(
            rows=key_rows,
            table_name=table,
        )

        if not where_conditions:
            columns = self._mapped_columns(
                rows=rows,
                table_name=table,
            )
            where_conditions = self._fallback_where_conditions(
                columns=columns,
                table_name=table,
            )

        if not where_conditions:
            return self._missing_mapping(
                record_name=record_name,
                reason="Missing DELETE key mapping",
            )

        lines: list[str] = [
            f"MOVE 'DELETE-{NameNormalizer.to_cobol(record_name)}' TO SQL-LOCATION.",
            "EXEC SQL",
            f"    DELETE FROM {table}",
            "    WHERE",
        ]

        lines.extend(
            self._and_lines(
                items=where_conditions,
                indent="        ",
            )
        )

        lines.append("END-EXEC.")

        return lines

    def open_cursor(
        self,
        set_name: str,
    ) -> list[str]:
        return [
            f"PERFORM {self.open_paragraph_name(set_name)}.",
        ]

    def fetch_cursor(
        self,
        record_name: str,
        set_name: str,
    ) -> list[str]:
        self._remember_cursor_record(
            set_name=set_name,
            record_name=record_name,
        )

        return [
            f"PERFORM {self.fetch_paragraph_name(set_name)}.",
        ]

    def close_cursor(
        self,
        set_name: str,
    ) -> list[str]:
        return [
            f"PERFORM {self.close_paragraph_name(set_name)}.",
        ]

    def has_cursor_relationship_condition(
        self,
        record_name: str,
        set_name: str,
    ) -> bool:
        self._remember_cursor_record(
            set_name=set_name,
            record_name=record_name,
        )

        if not self._looks_like_child_set(set_name):
            return True

        rows = self._record_rows(record_name)

        table = self._best_table_for_rows(
            rows=rows,
            record_name=record_name,
        )

        table = self._resolve_dclgen_table(table)

        if not table:
            return False

        conditions = self.cursor_where_conditions(
            record_name=record_name,
            set_name=set_name,
            child_table=table,
        )

        return bool(conditions)

    def cursor_name(
        self,
        set_name: str,
    ) -> str:
        record_name = self.cursor_set_record_cache.get(
            NameNormalizer.normalize(set_name),
            "",
        )

        if record_name:
            rows = self._record_rows(record_name)
            table = self._best_table_for_rows(
                rows=rows,
                record_name=record_name,
            )

            table = self._resolve_dclgen_table(table)

            if table:
                base = NameNormalizer.to_cobol(table)
                base = re.sub(r"(TB|TV)$", "", base, flags=re.IGNORECASE)
                return f"{base}C1"

        normalized = NameNormalizer.to_cobol(set_name)

        if not normalized:
            return "DBC1"

        return normalized[:24] + "C1"

    def open_paragraph_name(
        self,
        set_name: str,
    ) -> str:
        return f"710-OPEN-{self.cursor_name(set_name)}"

    def fetch_paragraph_name(
        self,
        set_name: str,
    ) -> str:
        return f"720-FETCH-{self.cursor_name(set_name)}"

    def close_paragraph_name(
        self,
        set_name: str,
    ) -> str:
        return f"730-CLOSE-{self.cursor_name(set_name)}"

    def declare_cursor(
        self,
        set_name: str,
    ) -> list[str]:
        record_name = self.cursor_set_record_cache.get(
            NameNormalizer.normalize(set_name),
            "",
        )

        if not record_name:
            record_name = self._record_from_set_name(set_name)

        rows = self._record_rows(record_name)

        table = self._best_table_for_rows(
            rows=rows,
            record_name=record_name,
        )

        table = self._resolve_dclgen_table(table)

        if not table:
            return [
                f"* DB2 WARNING: Unable to declare cursor {self.cursor_name(set_name)}; missing DB2 table mapping."
            ]

        columns = self._mapped_columns(
            rows=rows,
            table_name=table,
        )

        if not columns:
            columns = self._dclgen_columns_for_table(table)

        columns = self._filter_select_columns(columns)

        if not columns:
            columns = ["*"]

        where_conditions: list[str] = []

        if self._looks_like_child_set(set_name):
            where_conditions = self.cursor_where_conditions(
                record_name=record_name,
                set_name=set_name,
                child_table=table,
            )

        order_by_columns = self._order_by_columns(
            rows=rows,
            fallback_columns=columns,
        )

        cursor_name = self.cursor_name(set_name)

        lines: list[str] = [
            "EXEC SQL",
            f"    DECLARE {cursor_name} CURSOR FOR",
            "    SELECT",
        ]

        lines.extend(
            self._comma_lines(
                items=columns,
                indent="        ",
            )
        )

        lines.append(f"    FROM {table}")

        if where_conditions:
            lines.append("    WHERE")
            lines.extend(
                self._and_lines(
                    items=where_conditions,
                    indent="        ",
                )
            )

        if order_by_columns:
            lines.append("    ORDER BY")
            lines.extend(
                self._comma_lines(
                    items=order_by_columns,
                    indent="        ",
                )
            )

        lines.extend(
            [
                "    FOR READ ONLY",
                "END-EXEC.",
            ]
        )

        return lines

    def cursor_where_conditions(
        self,
        record_name: str,
        set_name: str,
        child_table: str,
    ) -> list[str]:
        rows = self._record_rows(record_name)

        relation = NameNormalizer.normalize(set_name)
        child_table_normalized = NameNormalizer.normalize(child_table)

        relationship_rows = [
            row
            for row in rows
            if NameNormalizer.normalize(row.relation) == relation
            or NameNormalizer.normalize(row.new_db2_record) == child_table_normalized
        ]

        conditions = self._relationship_where_conditions(
            rows=relationship_rows,
            child_table=child_table,
        )

        if conditions:
            return conditions

        key_rows = self._key_rows(rows)

        return self._where_conditions(
            rows=key_rows,
            table_name=child_table,
        )

    def _update_set_columns(
        self,
        record_name: str,
        rows: list[SheetMappingRow],
        table_name: str,
        all_columns: list[str],
        key_columns: set[str],
        changed_fields: list[str] | None = None,
    ) -> list[str]:
        table = NameNormalizer.normalize(table_name)

        explicit_changed = {
            NameNormalizer.normalize(value)
            for value in changed_fields or []
            if NameNormalizer.normalize(value)
        }

        remembered_changed = self.changed_fields_by_record.get(
            NameNormalizer.normalize(record_name),
            set(),
        )

        effective_changed = explicit_changed.union(remembered_changed)

        if effective_changed:
            columns = self._columns_for_changed_fields(
                rows=rows,
                table_name=table,
                changed_fields=effective_changed,
            )

            columns = [
                column
                for column in columns
                if column
                and column not in key_columns
                and self._column_exists_in_table(
                    table_name=table,
                    column_name=column,
                )
            ]

            columns.extend(
                self._update_audit_columns_from_mapping(
                    rows=rows,
                    table_name=table,
                    key_columns=key_columns,
                )
            )

            return self._unique_non_empty(columns)

        columns: list[str] = []

        columns.extend(
            self._date_update_columns_from_mapping(
                rows=rows,
                table_name=table,
                key_columns=key_columns,
            )
        )

        columns.extend(
            self._update_audit_columns_from_mapping(
                rows=rows,
                table_name=table,
                key_columns=key_columns,
            )
        )

        columns = [
            column
            for column in columns
            if column
            and column not in key_columns
            and self._column_exists_in_table(
                table_name=table,
                column_name=column,
            )
        ]

        if columns:
            return self._unique_non_empty(columns)

        safe_columns: list[str] = []

        for column in all_columns:
            normalized = NameNormalizer.normalize(column)

            if not normalized:
                continue

            if normalized in key_columns:
                continue

            if self._is_audit_column(normalized):
                continue

            if not self._column_exists_in_table(
                table_name=table,
                column_name=normalized,
            ):
                continue

            safe_columns.append(normalized)
            break

        return self._unique_non_empty(safe_columns)

    def _columns_for_changed_fields(
        self,
        rows: list[SheetMappingRow],
        table_name: str,
        changed_fields: set[str],
    ) -> list[str]:
        table = NameNormalizer.normalize(table_name)
        table_candidates = set(self._table_candidates(table))
        output: list[str] = []

        for row in rows:
            row_table = NameNormalizer.normalize(
                self._first_non_empty(
                    row.new_db2_record,
                    row.cross_application_db2_table,
                )
            )

            if row_table not in table_candidates:
                continue

            source_candidates = {
                NameNormalizer.normalize(row.cobol_zone),
                NameNormalizer.normalize(row.reference_field_name_copybook),
                NameNormalizer.normalize(self._extract_source_field(row.cobol_zone)),
                NameNormalizer.normalize(
                    self._extract_source_field(row.reference_field_name_copybook)
                ),
            }

            source_candidates = {
                candidate
                for candidate in source_candidates
                if candidate
            }

            if not source_candidates.intersection(changed_fields):
                continue

            column = NameNormalizer.normalize(
                self._first_non_empty(
                    row.new_db2_field_name,
                    row.cross_application_db2_field_name,
                )
            )

            if column:
                output.append(column)

        return self._unique_non_empty(output)

    def _date_update_columns_from_mapping(
        self,
        rows: list[SheetMappingRow],
        table_name: str,
        key_columns: set[str],
    ) -> list[str]:
        table = NameNormalizer.normalize(table_name)
        table_candidates = set(self._table_candidates(table))
        output: list[str] = []

        for row in rows:
            row_table = NameNormalizer.normalize(
                self._first_non_empty(
                    row.new_db2_record,
                    row.cross_application_db2_table,
                )
            )

            if row_table not in table_candidates:
                continue

            column = NameNormalizer.normalize(
                self._first_non_empty(
                    row.new_db2_field_name,
                    row.cross_application_db2_field_name,
                )
            )

            if not column:
                continue

            if column in key_columns:
                continue

            if self._is_audit_column(column):
                continue

            db2_type = NameNormalizer.normalize(
                self._first_non_empty(
                    row.new_db2_data_type,
                    row.cross_application_db2_data_type,
                )
            )

            source_field = NameNormalizer.normalize(
                self._extract_source_field(
                    self._first_non_empty(
                        row.cobol_zone,
                        row.reference_field_name_copybook,
                    )
                )
            )

            is_date = (
                "DATE" in db2_type
                or column.startswith(self.DATE_COLUMN_PREFIXES)
                or source_field.startswith(self.DATE_COLUMN_PREFIXES)
            )

            if not is_date:
                continue

            if not self._column_exists_in_table(
                table_name=table,
                column_name=column,
            ):
                continue

            output.append(column)

        preferred = [
            column
            for column in output
            if "INFSD" in column
            or "INFO" in column
        ]

        if preferred:
            return self._unique_non_empty(preferred)

        return self._unique_non_empty(output[:1])

    def _update_audit_columns_from_mapping(
        self,
        rows: list[SheetMappingRow],
        table_name: str,
        key_columns: set[str],
    ) -> list[str]:
        table = NameNormalizer.normalize(table_name)
        table_candidates = set(self._table_candidates(table))
        output: list[str] = []

        for row in rows:
            row_table = NameNormalizer.normalize(
                self._first_non_empty(
                    row.new_db2_record,
                    row.cross_application_db2_table,
                )
            )

            if row_table not in table_candidates:
                continue

            column = NameNormalizer.normalize(
                self._first_non_empty(
                    row.new_db2_field_name,
                    row.cross_application_db2_field_name,
                )
            )

            if not column:
                continue

            if column in key_columns:
                continue

            if not column.startswith(self.UPDATE_AUDIT_COLUMN_PREFIXES):
                continue

            if not self._column_exists_in_table(
                table_name=table,
                column_name=column,
            ):
                continue

            output.append(column)

        return self._unique_non_empty(output)

    def _record_rows(
        self,
        record_name: str,
    ) -> list[SheetMappingRow]:
        record = NameNormalizer.normalize(record_name)

        if not record:
            return []

        rows = list(self.rows_by_record.get(record, []))
        no_suffix = NameNormalizer.remove_record_suffix(record)

        if no_suffix and no_suffix != record:
            rows.extend(self.rows_by_record.get(no_suffix, []))

        if rows:
            return rows

        return self._record_rows_by_semantic_match(record)

    def _record_rows_by_semantic_match(
        self,
        record_name: str,
    ) -> list[SheetMappingRow]:
        record_aliases = self._semantic_record_aliases(record_name)
        output: list[SheetMappingRow] = []

        for row in self.rows:
            row_record = NameNormalizer.normalize(row.cobol_record_idms)

            if not row_record:
                continue

            row_aliases = self._semantic_record_aliases(row_record)

            if self._alias_match_score(record_aliases, row_aliases) >= 90:
                output.append(row)

        return output

    def _best_table_for_rows(
        self,
        rows: list[SheetMappingRow],
        record_name: str,
    ) -> str:
        explicit_table_scores: dict[str, int] = {}

        for row in rows:
            table = NameNormalizer.normalize(
                self._first_non_empty(
                    row.new_db2_record,
                    row.cross_application_db2_table,
                )
            )

            column = NameNormalizer.normalize(
                self._first_non_empty(
                    row.new_db2_field_name,
                    row.cross_application_db2_field_name,
                )
            )

            if table and column:
                resolved_table = self._resolve_dclgen_table(table)
                explicit_table_scores[resolved_table] = (
                    explicit_table_scores.get(resolved_table, 0) + 1
                )

        if explicit_table_scores:
            return max(
                explicit_table_scores.items(),
                key=lambda item: item[1],
            )[0]

        dynamic_table = self._best_table_for_record_by_table_suffix(record_name)

        if dynamic_table:
            return self._resolve_dclgen_table(dynamic_table)

        return ""

    def _best_table_for_record_by_table_suffix(
        self,
        record_name: str,
    ) -> str:
        record_aliases = self._semantic_record_aliases(record_name)
        best_table = ""
        best_score = 0

        for table in self.table_catalog:
            table_aliases = self._semantic_table_aliases(table)
            score = self._alias_match_score(record_aliases, table_aliases)

            if score > best_score:
                best_score = score
                best_table = table

        if best_score >= 70:
            return NameNormalizer.to_cobol(best_table)

        return ""

    def _mapped_columns(
        self,
        rows: list[SheetMappingRow],
        table_name: str,
    ) -> list[str]:
        table = self._resolve_dclgen_table(table_name)
        table_candidates = set(self._table_candidates(table))
        output: list[str] = []
        seen: set[str] = set()

        for row in rows:
            row_table = NameNormalizer.normalize(
                self._first_non_empty(
                    row.new_db2_record,
                    row.cross_application_db2_table,
                )
            )

            row_table = self._resolve_dclgen_table(row_table)

            if row_table not in table_candidates:
                continue

            column = NameNormalizer.normalize(
                self._first_non_empty(
                    row.new_db2_field_name,
                    row.cross_application_db2_field_name,
                )
            )

            if not column:
                continue

            if column in seen:
                continue

            if not self._column_exists_in_table(
                table_name=table,
                column_name=column,
            ):
                continue

            seen.add(column)
            output.append(column)

        return output

    def _key_rows(
        self,
        rows: list[SheetMappingRow],
    ) -> list[SheetMappingRow]:
        output: list[SheetMappingRow] = []

        for row in rows:
            db2_key = NameNormalizer.normalize(row.db2_key)
            idms_key = NameNormalizer.normalize(row.idms_key)

            if "PRIMARY" in db2_key or "KEY" in db2_key:
                output.append(row)
                continue

            if "CALC" in idms_key:
                output.append(row)
                continue

        return output

    def _infer_key_rows_from_columns(
        self,
        rows: list[SheetMappingRow],
        columns: list[str],
    ) -> list[SheetMappingRow]:
        if not rows:
            return []

        key_like_prefixes = (
            "CT_",
            "NR_",
            "DA_CR",
            "NS_",
        )

        column_set = {
            NameNormalizer.normalize(column)
            for column in columns
            if column
        }

        output: list[SheetMappingRow] = []

        for row in rows:
            column = NameNormalizer.normalize(
                self._first_non_empty(
                    row.new_db2_field_name,
                    row.cross_application_db2_field_name,
                )
            )

            if not column:
                continue

            if column not in column_set:
                continue

            if column.startswith(key_like_prefixes):
                output.append(row)

            if len(output) >= 8:
                break

        return output

    def _where_conditions(
        self,
        rows: list[SheetMappingRow],
        table_name: str,
    ) -> list[str]:
        table = self._resolve_dclgen_table(table_name)
        output: list[str] = []

        for row in rows:
            column = NameNormalizer.normalize(
                self._first_non_empty(
                    row.new_db2_field_name,
                    row.cross_application_db2_field_name,
                )
            )

            if not column:
                continue

            if not self._column_exists_in_table(
                table_name=table,
                column_name=column,
            ):
                continue

            host = self._host_for_column(
                table_name=table,
                column_name=column,
            )

            if not host:
                continue

            output.append(f"{column} = {host}")

        return self._unique_non_empty(output)

    def _fallback_where_conditions(
        self,
        columns: list[str],
        table_name: str,
    ) -> list[str]:
        table = self._resolve_dclgen_table(table_name)
        output: list[str] = []

        key_like_prefixes = (
            "CT_",
            "NR_CIO",
            "DA_CR",
            "NR_ID",
            "NS_",
        )

        for column in columns:
            normalized = NameNormalizer.normalize(column)

            if not normalized:
                continue

            if not normalized.startswith(key_like_prefixes):
                continue

            if self._is_audit_column(normalized):
                continue

            if not self._column_exists_in_table(
                table_name=table,
                column_name=normalized,
            ):
                continue

            host = self._host_for_column(
                table_name=table,
                column_name=normalized,
            )

            if not host:
                continue

            output.append(f"{normalized} = {host}")

            if len(output) >= 8:
                break

        return self._unique_non_empty(output)

    def _relationship_where_conditions(
        self,
        rows: list[SheetMappingRow],
        child_table: str,
    ) -> list[str]:
        child = self._resolve_dclgen_table(child_table)
        output: list[str] = []

        for row in rows:
            child_column = NameNormalizer.normalize(
                self._first_non_empty(
                    row.new_db2_field_name,
                    row.cross_application_db2_field_name,
                )
            )

            parent_table = self._resolve_dclgen_table(row.cross_application_db2_table)
            parent_column = NameNormalizer.normalize(row.cross_application_db2_field_name)

            if not child_column or not parent_column:
                continue

            if not self._column_exists_in_table(
                table_name=child,
                column_name=child_column,
            ):
                continue

            host = self._host_for_column(
                table_name=parent_table or child,
                column_name=parent_column,
            )

            if not host:
                continue

            output.append(f"{child_column} = {host}")

        return self._unique_non_empty(output)

    def _order_by_columns(
        self,
        rows: list[SheetMappingRow],
        fallback_columns: list[str],
    ) -> list[str]:
        key_rows = self._key_rows(rows)
        output: list[str] = []

        for row in key_rows:
            column = NameNormalizer.normalize(
                self._first_non_empty(
                    row.new_db2_field_name,
                    row.cross_application_db2_field_name,
                )
            )

            if column and column not in output:
                output.append(column)

        if output:
            return output

        return [
            column
            for column in fallback_columns[:4]
            if column and column != "*"
        ]

    def _dclgen_columns_for_table(
        self,
        table_name: str,
    ) -> list[str]:
        table = self._resolve_dclgen_table(table_name)
        candidates = self._table_candidates(table)
        output: list[str] = []

        for candidate in candidates:
            output.extend(self.dclgen_by_table.get(candidate, []))

        return self._unique_non_empty(output)

    def _host_variables(
        self,
        table_name: str,
        columns: list[str],
    ) -> list[str]:
        hosts: list[str] = []

        for column in columns:
            host = self._host_for_column(
                table_name=table_name,
                column_name=column,
            )

            if host:
                hosts.append(host)

        return hosts

    def _host_for_column(
        self,
        table_name: str,
        column_name: str,
    ) -> str:
        table = self._resolve_dclgen_table(table_name)
        column = NameNormalizer.normalize(column_name)

        if not column:
            return ""

        for table_candidate in self._table_candidates(table):
            host = self.dclgen_host_lookup.get(
                (
                    table_candidate,
                    column,
                )
            )

            if host:
                group = self._dclgen_group_for_table(table_candidate)
                return f":{group}.{host}"

        return ""

    def _dclgen_group_for_table(
        self,
        table_name: str,
    ) -> str:
        table = self._resolve_dclgen_table(table_name)

        for table_candidate in self._table_candidates(table):
            group = self.dclgen_group_lookup.get(table_candidate)

            if group:
                return group

        return "DCL" + NameNormalizer.to_cobol(table)

    def _column_exists_in_table(
        self,
        table_name: str,
        column_name: str,
    ) -> bool:
        table = self._resolve_dclgen_table(table_name)
        column = NameNormalizer.normalize(column_name)

        if not table or not column:
            return False

        return column in {
            NameNormalizer.normalize(item)
            for item in self._dclgen_columns_for_table_no_resolve(table)
        }

    def _dclgen_columns_for_table_no_resolve(
        self,
        table_name: str,
    ) -> list[str]:
        table = NameNormalizer.normalize(table_name)
        output: list[str] = []

        for candidate in self._table_candidates(table):
            output.extend(self.dclgen_by_table.get(candidate, []))

        return self._unique_non_empty(output)

    def _resolve_dclgen_table(
        self,
        table_name: str,
    ) -> str:
        table = NameNormalizer.normalize(table_name)

        if not table:
            return ""

        candidates = self._table_candidates(table)

        for candidate in candidates:
            if candidate in self.dclgen_by_table:
                return candidate

        return table

    def _group_rows_by_record(
        self,
        rows: list[SheetMappingRow],
    ) -> dict[str, list[SheetMappingRow]]:
        output: dict[str, list[SheetMappingRow]] = defaultdict(list)

        for row in rows:
            record = NameNormalizer.normalize(row.cobol_record_idms)

            if not record:
                continue

            output[record].append(row)
            no_suffix = NameNormalizer.remove_record_suffix(record)

            if no_suffix and no_suffix != record:
                output[no_suffix].append(row)

        return output

    def _group_dclgen_by_table(
        self,
        columns: list[DclgenColumn],
    ) -> dict[str, list[str]]:
        output: dict[str, list[str]] = defaultdict(list)

        for item in columns:
            table = NameNormalizer.normalize(item.table_name)
            column = NameNormalizer.normalize(item.column_name)

            if not table or not column:
                continue

            for candidate in self._table_candidates(table):
                output[candidate].append(column)

        return {
            table: self._unique_non_empty(values)
            for table, values in output.items()
        }

    def _build_dclgen_host_lookup(
        self,
        columns: list[DclgenColumn],
    ) -> dict[tuple[str, str], str]:
        output: dict[tuple[str, str], str] = {}

        for item in columns:
            table = NameNormalizer.normalize(item.table_name)
            column = NameNormalizer.normalize(item.column_name)
            host = NameNormalizer.to_cobol(item.cobol_host_name or item.column_name)

            if not table or not column or not host:
                continue

            for candidate in self._table_candidates(table):
                output[(candidate, column)] = host

        return output

    def _build_dclgen_group_lookup(
        self,
        columns: list[DclgenColumn],
    ) -> dict[str, str]:
        output: dict[str, str] = {}

        for item in columns:
            table = NameNormalizer.normalize(item.table_name)

            if not table:
                continue

            group = "DCL" + NameNormalizer.to_cobol(table)

            for candidate in self._table_candidates(table):
                output[candidate] = group

        return output

    def _build_table_catalog(self) -> list[str]:
        output: list[str] = []

        for row in self.rows:
            table = NameNormalizer.normalize(
                self._first_non_empty(
                    row.new_db2_record,
                    row.cross_application_db2_table,
                )
            )

            resolved = self._resolve_dclgen_table(table)

            if resolved and resolved not in output:
                output.append(resolved)

        for column in self.dclgen_columns:
            table = NameNormalizer.normalize(column.table_name)
            resolved = self._resolve_dclgen_table(table)

            if resolved and resolved not in output:
                output.append(resolved)

        return output

    def _filter_select_columns(
        self,
        columns: list[str],
    ) -> list[str]:
        return [
            column
            for column in self._unique_non_empty(columns)
            if column and not self._is_select_excluded_column(column)
        ]

    def _is_select_excluded_column(
        self,
        column_name: str,
    ) -> bool:
        normalized = NameNormalizer.normalize(column_name)

        if normalized.startswith("TS_CREATE"):
            return True

        if normalized.startswith("TS_UPDATE"):
            return True

        if normalized.startswith("ID_USERID"):
            return True

        if normalized.startswith("NR_USERID"):
            return True

        return False

    def _is_audit_column(
        self,
        column_name: str,
    ) -> bool:
        normalized = NameNormalizer.normalize(column_name)
        return normalized.startswith(self.AUDIT_COLUMN_PREFIXES)

    def _is_insert_excluded_audit_column(
        self,
        column_name: str,
    ) -> bool:
        normalized = NameNormalizer.normalize(column_name)
        return normalized.startswith(self.INSERT_EXCLUDE_AUDIT_PREFIXES)

    def _looks_like_child_set(
        self,
        set_name: str,
    ) -> bool:
        normalized = NameNormalizer.normalize(set_name)

        if not normalized:
            return False

        parts = normalized.split("_")

        if len(parts) >= 2 and parts[0] != parts[-1]:
            return True

        return "-" in str(set_name or "")

    def _record_from_set_name(
        self,
        set_name: str,
    ) -> str:
        normalized = NameNormalizer.normalize(set_name)

        if not normalized:
            return ""

        parts = normalized.split("_")

        if len(parts) >= 2:
            return parts[-1]

        return normalized

    def _remember_cursor_record(
        self,
        set_name: str,
        record_name: str,
    ) -> None:
        set_key = NameNormalizer.normalize(set_name)
        record = NameNormalizer.normalize(record_name)

        if set_key and record:
            self.cursor_set_record_cache[set_key] = record

    def _table_candidates(
        self,
        table_name: str,
    ) -> list[str]:
        table = NameNormalizer.normalize(table_name)

        if not table:
            return []

        output = [table]

        if table.endswith("_TB"):
            output.append(table[:-3] + "_TV")

        if table.endswith("_TV"):
            output.append(table[:-3] + "_TB")

        if table.endswith("TB"):
            output.append(table[:-2] + "TV")

        if table.endswith("TV"):
            output.append(table[:-2] + "TB")

        output.append(NameNormalizer.to_cobol(table))

        compact = NameNormalizer.compact(table)

        if compact:
            output.append(compact)

        return self._unique_non_empty(output)

    def _semantic_record_aliases(
        self,
        record_name: str,
    ) -> list[str]:
        normalized = NameNormalizer.normalize(record_name)
        compact = NameNormalizer.compact(normalized)
        no_suffix = NameNormalizer.remove_record_suffix(normalized)

        aliases = [
            normalized,
            compact,
            no_suffix,
            NameNormalizer.compact(no_suffix),
        ]

        if compact.startswith("VM"):
            aliases.append(compact[2:])

        if compact.startswith("VMB"):
            aliases.append(compact[3:])

        return self._unique_non_empty(aliases)

    def _semantic_table_aliases(
        self,
        table_name: str,
    ) -> list[str]:
        normalized = NameNormalizer.normalize(table_name)
        compact = NameNormalizer.compact(normalized)

        aliases = [
            normalized,
            compact,
        ]

        table_core = compact

        for prefix in ["DCL", "DZ", "NK"]:
            if table_core.startswith(prefix):
                aliases.append(table_core[len(prefix):])

        for suffix in ["TB", "TV"]:
            if table_core.endswith(suffix):
                aliases.append(table_core[:-len(suffix)])

        if table_core.startswith("DZ") and table_core.endswith(("TB", "TV")):
            aliases.append(table_core[2:-2])

        return self._unique_non_empty(aliases)

    def _alias_match_score(
        self,
        left_aliases: list[str],
        right_aliases: list[str],
    ) -> int:
        left_set = {
            NameNormalizer.compact(value)
            for value in left_aliases
            if value
        }

        right_set = {
            NameNormalizer.compact(value)
            for value in right_aliases
            if value
        }

        if not left_set or not right_set:
            return 0

        best = 0

        for left in left_set:
            for right in right_set:
                if not left or not right:
                    continue

                if left == right:
                    best = max(best, 100)
                    continue

                if left in right or right in left:
                    best = max(best, 85)
                    continue

                score = self._simple_ratio(left, right)
                best = max(best, score)

        return best

    def _simple_ratio(
        self,
        left: str,
        right: str,
    ) -> int:
        if not left or not right:
            return 0

        left_set = set(left)
        right_set = set(right)
        intersection = len(left_set.intersection(right_set))
        denominator = max(len(left_set), len(right_set), 1)

        return int((intersection / denominator) * 100)

    def _extract_source_field(
        self,
        value: str,
    ) -> str:
        text = str(value or "").strip()

        if not text:
            return ""

        text = text.replace(".", " ")
        text = re.sub(r"\s+", " ", text)

        level_match = re.match(
            r"^\s*(?:0[1-9]|[1-4][0-9]|66|77|88)\s+([A-Z][A-Z0-9-]*)\b",
            text,
            flags=re.IGNORECASE,
        )

        if level_match:
            return level_match.group(1)

        tokens = re.findall(
            r"[A-Z][A-Z0-9-]*",
            text,
            flags=re.IGNORECASE,
        )

        for token in tokens:
            if token.upper() not in self.IGNORE_FIELD_TOKENS:
                return token

        return tokens[0] if tokens else ""

    def _first_non_empty(
        self,
        *values: str,
    ) -> str:
        for value in values:
            text = str(value or "").strip()

            if text:
                return text

        return ""

    def _unique_non_empty(
        self,
        values: list[str],
    ) -> list[str]:
        output: list[str] = []

        for value in values:
            text = str(value or "").strip()

            if not text:
                continue

            if text not in output:
                output.append(text)

        return output

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
            output.append(f"{indent}{item}{suffix}")

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
            output.append(f"{indent}{prefix}{item}")

        return output

    def _missing_mapping(
        self,
        record_name: str,
        reason: str,
    ) -> list[str]:
        record = NameNormalizer.to_cobol(record_name)

        if record:
            return [
                "* DB2: Conversion skipped because Sheet Mapping entry does not exist.",
                f"* DB2: Missing Sheet Mapping metadata for record {record}.",
                f"* DB2: Reason: {reason}.",
                "CONTINUE.",
            ]

        return [
            "* DB2: Conversion skipped because required Sheet Mapping metadata does not exist.",
            f"* DB2: Reason: {reason}.",
            "CONTINUE.",
        ]

    def _todo(
        self,
        message: str,
    ) -> list[str]:
        clean_message = str(message or "").strip()
        record_name = ""

        match = re.search(
            r"\bfor\s+([A-Z][A-Z0-9-]*)\b",
            clean_message,
            flags=re.IGNORECASE,
        )

        if match:
            record_name = match.group(1).upper()

        return self._missing_mapping(
            record_name=record_name,
            reason=clean_message or "Missing mapping",
        )