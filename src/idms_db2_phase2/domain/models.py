from dataclasses import dataclass, field


@dataclass
class SheetMappingRow:
    cobol_record_idms: str = ""
    cobol_zone: str = ""
    idms_key: str = ""
    idms_pic_clause: str = ""
    length_of_field_bytes: str = ""
    field_end_position: str = ""
    db2_key: str = ""
    new_db2_record: str = ""
    new_db2_field_name: str = ""
    new_db2_data_type: str = ""
    hopex_expression_type_remark: str = ""
    relation: str = ""
    reference_field_name_copybook: str = ""
    reference_field_pic_clause: str = ""
    cross_application_db2_field_name: str = ""
    cross_application_db2_data_type: str = ""
    basetype: str = ""


@dataclass
class DclgenColumn:
    table_name: str = ""
    column_name: str = ""
    db2_type: str = ""
    cobol_host_name: str = ""
    cobol_picture: str = ""
    nullable: bool = True


@dataclass
class CopybookField:
    level: str = ""
    name: str = ""
    picture: str = ""
    usage: str = ""
    occurs: str = ""


@dataclass
class IdmsOperation:
    operation: str
    record_name: str = ""
    set_name: str = ""
    line_number: int = 0
    raw_line: str = ""


@dataclass
class RelationshipSummary:
    relation: str = ""
    parent_record: str = ""
    child_record: str = ""
    parent_key: str = ""
    child_key: str = ""


@dataclass
class RecordSummary:
    record_name: str
    db2_table: str
    column_count: int
    key_columns: list[str] = field(default_factory=list)


@dataclass
class ConversionInput:
    sheet_mapping_rows: list[SheetMappingRow] = field(default_factory=list)
    dclgen_columns: list[DclgenColumn] = field(default_factory=list)
    copybook_fields: list[CopybookField] = field(default_factory=list)
    idms_cobol_text: str = ""
    target_program_id: str = ""


@dataclass
class ConversionResult:
    converted_cobol: str = ""
    validation_messages: list[str] = field(default_factory=list)
    operations: list[IdmsOperation] = field(default_factory=list)