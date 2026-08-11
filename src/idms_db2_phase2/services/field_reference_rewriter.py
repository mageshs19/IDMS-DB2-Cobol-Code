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

    This rewriter is conservative:
    - it rewrites qualified references FIELD OF RECORD and FIELD IN RECORD
    - it avoids unqualified field replacement
    """

    def __init__(
        self,
        mapping_rows: list[SheetMappingRow],
        dclgen_columns: list[DclgenColumn],
    ) -> None:
        self.mapping_rows = mapping_rows
        self.dclgen_columns = dclgen_columns
        self.dclgen_host_lookup = self._build_dclgen_host_lookup()
        self.source_reference_map = self._build_source_reference_map()
        self.rewrite_messages: list[str] = []

    def rewrite(
        self,
        text: str,
    ) -> str:
        self.rewrite_messages = []

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

            if not source_field or not source_record or not target:
                continue

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

    def _build_source_reference_map(
        self,
    ) -> dict[tuple[str, str], str]:
        output: dict[tuple[str, str], str] = {}

        for row in self.mapping_rows:
            source_record = NameNormalizer.to_cobol(
                row.cobol_record_idms,
            )

            source_field_candidates = self._source_field_candidates(
                row,
            )

            target = self._target_host_reference(
                row,
            )

            if not source_record or not target:
                continue

            for source_field in source_field_candidates:
                if not source_field:
                    continue

                output[
                    (
                        source_field,
                        source_record,
                    )
                ] = target

                no_suffix_record = NameNormalizer.to_cobol(
                    NameNormalizer.remove_record_suffix(
                        source_record,
                    )
                )

                if no_suffix_record and no_suffix_record != source_record:
                    output[
                        (
                            source_field,
                            no_suffix_record,
                        )
                    ] = target

        return output

    def _source_field_candidates(
        self,
        row: SheetMappingRow,
    ) -> list[str]:
        candidates: list[str] = []

        for value in [
            row.cobol_zone,
            row.reference_field_name_copybook,
        ]:
            source = self._source_field_from_value(
                value,
            )

            if source and source not in candidates:
                candidates.append(
                    source,
                )

        return candidates

    def _source_field_from_value(
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

    def _target_host_reference(
        self,
        row: SheetMappingRow,
    ) -> str:
        table = NameNormalizer.normalize(
            row.new_db2_record,
        )

        column = NameNormalizer.normalize(
            row.new_db2_field_name,
        )

        if not column:
            return ""

        if table:
            host = self.dclgen_host_lookup.get(
                (
                    table,
                    column,
                )
            )

            if host:
                return host

            return f"DCL{table}.{NameNormalizer.to_cobol(column)}"

        host = self.dclgen_host_lookup.get(
            (
                "",
                column,
            )
        )

        if host:
            return host

        return NameNormalizer.to_cobol(
            column,
        )

    def _build_dclgen_host_lookup(
        self,
    ) -> dict[tuple[str, str], str]:
        lookup: dict[tuple[str, str], str] = {}

        for column in self.dclgen_columns:
            table = NameNormalizer.normalize(
                column.table_name,
            )

            db2_column = NameNormalizer.normalize(
                column.column_name,
            )

            host_name = NameNormalizer.to_cobol(
                column.cobol_host_name or column.column_name,
            )

            if not db2_column or not host_name:
                continue

            host_reference = (
                f"DCL{table}.{host_name}"
                if table
                else host_name
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