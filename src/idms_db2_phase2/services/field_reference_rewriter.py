import re

from idms_db2_phase2.domain.models import DclgenColumn, SheetMappingRow
from idms_db2_phase2.services.name_normalizer import NameNormalizer


class FieldReferenceRewriter:
    """
    Rewrites residual IDMS qualified field references into DB2 DCLGEN host references.

    Example:
        AM-CN-STOCK OF VMBEFF

    Becomes:
        DCLDZBEFFTV.AM-CNSTK-479BEFF

    This works only when:
        - Sheet Mapping contains source COBOL field -> DB2 column mapping.
        - DCLGEN contains DB2 column -> COBOL host field mapping.

    The rewriter is intentionally conservative:
        - It rewrites only qualified references: FIELD OF RECORD or FIELD IN RECORD.
        - It does not rewrite every unqualified field name because that can break report/file fields.
    """

    def __init__(
        self,
        mapping_rows: list[SheetMappingRow],
        dclgen_columns: list[DclgenColumn],
    ) -> None:
        self.mapping_rows = mapping_rows
        self.dclgen_columns = dclgen_columns
        self.record_names = self._build_record_names()
        self.column_to_dclgen_host = self._build_column_to_dclgen_host()
        self.source_reference_map = self._build_source_reference_map()
        self.rewrite_messages: list[str] = []

    def rewrite(
        self,
        text: str,
    ) -> str:
        if not text:
            return ""

        if not self.source_reference_map:
            return text

        rewritten = text

        for key, target in sorted(
            self.source_reference_map.items(),
            key=lambda item: len(item[0][0]),
            reverse=True,
        ):
            source_field, source_record = key

            patterns = [
                rf"\b{re.escape(source_field)}\s+OF\s+{re.escape(source_record)}\b",
                rf"\b{re.escape(source_field)}\s+IN\s+{re.escape(source_record)}\b",
            ]

            for pattern in patterns:
                before = rewritten

                rewritten = re.sub(
                    pattern,
                    target,
                    rewritten,
                    flags=re.IGNORECASE,
                )

                if before != rewritten:
                    self.rewrite_messages.append(
                        f"Rewritten IDMS reference {source_field} OF {source_record} -> {target}"
                    )

        return rewritten

    def _build_record_names(
        self,
    ) -> set[str]:
        names: set[str] = set()

        for row in self.mapping_rows:
            record = NameNormalizer.to_cobol(
                row.cobol_record_idms,
            )

            if record:
                names.add(
                    record,
                )

        return names

    def _build_column_to_dclgen_host(
        self,
    ) -> dict[tuple[str, str], str]:
        lookup: dict[tuple[str, str], str] = {}

        for column in self.dclgen_columns:
            table_name = NameNormalizer.normalize(
                column.table_name,
            )

            column_name = NameNormalizer.normalize(
                column.column_name,
            )

            host_name = NameNormalizer.to_cobol(
                column.cobol_host_name or column.column_name,
            )

            if not table_name or not column_name:
                continue

            if not host_name:
                continue

            group_name = self._dclgen_group_name(
                table_name,
            )

            lookup[
                (
                    table_name,
                    column_name,
                )
            ] = f"{group_name}.{host_name}"

            lookup[
                (
                    "",
                    column_name,
                )
            ] = f"{group_name}.{host_name}"

        return lookup

    def _build_source_reference_map(
        self,
    ) -> dict[tuple[str, str], str]:
        mapping: dict[tuple[str, str], str] = {}

        for row in self.mapping_rows:
            source_record = NameNormalizer.to_cobol(
                row.cobol_record_idms,
            )

            source_field = self._source_field_from_cobol_zone(
                row.cobol_zone,
            )

            db2_table = NameNormalizer.normalize(
                row.new_db2_record,
            )

            db2_column = NameNormalizer.normalize(
                row.new_db2_field_name,
            )

            if not source_record or not source_field:
                continue

            if not db2_column:
                continue

            target = self.column_to_dclgen_host.get(
                (
                    db2_table,
                    db2_column,
                )
            )

            if not target:
                target = self.column_to_dclgen_host.get(
                    (
                        "",
                        db2_column,
                    )
                )

            if not target:
                if db2_table:
                    target = (
                        f"{self._dclgen_group_name(db2_table)}."
                        f"{NameNormalizer.to_cobol(db2_column)}"
                    )
                else:
                    target = NameNormalizer.to_cobol(
                        db2_column,
                    )

            mapping[
                (
                    source_field,
                    source_record,
                )
            ] = target

        return mapping

    def _source_field_from_cobol_zone(
        self,
        value: str,
    ) -> str:
        text = str(
            value or "",
        ).strip()

        if not text:
            return ""

        text = re.sub(
            r"^\s*\d{2}\s+",
            "",
            text,
        )

        text = text.strip()

        if not text:
            return ""

        return NameNormalizer.to_cobol(
            text,
        )

    def _dclgen_group_name(
        self,
        table_name: str,
    ) -> str:
        normalized = NameNormalizer.normalize(
            table_name,
        )

        return f"DCL{normalized}"