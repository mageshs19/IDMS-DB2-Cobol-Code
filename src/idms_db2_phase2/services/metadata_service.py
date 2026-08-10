from collections import defaultdict

from idms_db2_phase2.domain.models import (
    RelationshipSummary,
    RecordSummary,
    SheetMappingRow,
)
from idms_db2_phase2.services.name_normalizer import NameNormalizer


class MetadataService:
    def record_summaries(
        self,
        rows: list[SheetMappingRow],
    ) -> list[RecordSummary]:
        grouped: dict[str, list[SheetMappingRow]] = defaultdict(list)

        for row in rows:
            record = NameNormalizer.normalize(row.cobol_record_idms)

            if not record:
                continue

            grouped[record].append(row)

        output: list[RecordSummary] = []

        for record_name, record_rows in sorted(grouped.items()):
            db2_table = self._first_db2_table(record_rows)

            columns = [
                NameNormalizer.normalize(row.new_db2_field_name)
                for row in record_rows
                if NameNormalizer.normalize(row.new_db2_field_name)
            ]

            key_columns = [
                NameNormalizer.normalize(row.new_db2_field_name)
                for row in record_rows
                if NameNormalizer.normalize(row.new_db2_field_name)
                and (
                    NameNormalizer.normalize(row.idms_key)
                    or NameNormalizer.normalize(row.db2_key)
                )
            ]

            output.append(
                RecordSummary(
                    record_name=record_name,
                    db2_table=db2_table,
                    column_count=len(set(columns)),
                    key_columns=sorted(set(key_columns)),
                )
            )

        return output

    def column_rows(
        self,
        rows: list[SheetMappingRow],
    ) -> list[dict[str, str]]:
        output: list[dict[str, str]] = []

        for row in rows:
            record = NameNormalizer.normalize(row.cobol_record_idms)
            table = NameNormalizer.normalize(row.new_db2_record)
            column = NameNormalizer.normalize(row.new_db2_field_name)

            if not record and not table and not column:
                continue

            output.append(
                {
                    "IDMS Record": record,
                    "DB2 Table": table,
                    "DB2 Column": column,
                    "DB2 Type": row.new_db2_data_type,
                    "Copybook Field": row.reference_field_name_copybook,
                    "IDMS PIC": row.idms_pic_clause,
                    "Relation": row.relation,
                }
            )

        return output

    def relationship_summaries(
        self,
        rows: list[SheetMappingRow],
    ) -> list[RelationshipSummary]:
        output: list[RelationshipSummary] = []
        seen: set[tuple[str, str, str, str]] = set()

        for row in rows:
            relation = NameNormalizer.normalize(row.relation)

            if not relation:
                continue

            child_record = NameNormalizer.normalize(row.cobol_record_idms)
            child_key = NameNormalizer.normalize(row.new_db2_field_name)
            parent_key = NameNormalizer.normalize(
                row.cross_application_db2_field_name
                or row.new_db2_field_name
            )

            parent_record = self._infer_parent_record_from_reference(row)

            key = (
                relation,
                parent_record,
                child_record,
                child_key,
            )

            if key in seen:
                continue

            seen.add(key)

            output.append(
                RelationshipSummary(
                    relation=relation,
                    parent_record=parent_record,
                    child_record=child_record,
                    parent_key=parent_key,
                    child_key=child_key,
                )
            )

        return output

    def mapping_preview_rows(
        self,
        rows: list[SheetMappingRow],
    ) -> list[dict[str, str]]:
        output: list[dict[str, str]] = []

        for row in rows:
            output.append(
                {
                    "Cobol Record IDMS": row.cobol_record_idms,
                    "IDMS Key": row.idms_key,
                    "IDMS PIC Clause": row.idms_pic_clause,
                    "DB2 Key": row.db2_key,
                    "New DB2 Record": row.new_db2_record,
                    "New DB2 Field name": row.new_db2_field_name,
                    "New DB2 Data Type": row.new_db2_data_type,
                    "Relation": row.relation,
                    "Reference Field Name": row.reference_field_name_copybook,
                    "Cross Application DB2 Field Name": row.cross_application_db2_field_name,
                    "Basetype": row.basetype,
                }
            )

        return output

    def _first_db2_table(self, rows: list[SheetMappingRow]) -> str:
        for row in rows:
            table = NameNormalizer.normalize(row.new_db2_record)

            if table:
                return table

        return ""

    def _infer_parent_record_from_reference(self, row: SheetMappingRow) -> str:
        value = row.cross_application_db2_field_name

        if not value:
            return ""

        normalized = NameNormalizer.normalize(value)
        parts = normalized.split("_")

        if len(parts) >= 2:
            return parts[0]

        return ""