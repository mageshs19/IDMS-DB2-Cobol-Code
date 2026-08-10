from idms_db2_phase2.domain.models import ConversionInput, ConversionResult
from idms_db2_phase2.services.cobol_transformer import CobolTransformer
from idms_db2_phase2.services.sql_generator import SqlGenerator
from idms_db2_phase2.services.validation_service import ValidationService


class ConversionService:
    def __init__(self) -> None:
        self.validation_service = ValidationService()

    def convert(self, conversion_input: ConversionInput) -> ConversionResult:
        validation_messages = self.validation_service.validate(conversion_input)

        if validation_messages:
            return ConversionResult(
                converted_cobol="",
                validation_messages=validation_messages,
                operations=[],
            )

        sql_generator = SqlGenerator(conversion_input.sheet_mapping_rows)
        transformer = CobolTransformer(sql_generator)

        converted_cobol, transform_messages, operations = transformer.transform(
            cobol_text=conversion_input.idms_cobol_text,
            target_program_id=conversion_input.target_program_id,
        )

        validation_messages.extend(transform_messages)

        return ConversionResult(
            converted_cobol=converted_cobol,
            validation_messages=validation_messages,
            operations=operations,
        )