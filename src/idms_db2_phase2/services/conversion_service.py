from idms_db2_phase2.domain.models import ConversionInput, ConversionResult
from idms_db2_phase2.services.cobol_transformer import CobolTransformer
from idms_db2_phase2.services.db2_cursor_paragraph_generator import (
    Db2CursorParagraphGenerator,
)
from idms_db2_phase2.services.db2_infrastructure_generator import (
    Db2InfrastructureGenerator,
)
from idms_db2_phase2.services.field_reference_rewriter import FieldReferenceRewriter
from idms_db2_phase2.services.pic_length_auto_fixer import PicLengthAutoFixer
from idms_db2_phase2.services.production_validator import ProductionValidator
from idms_db2_phase2.services.sql_generator import SqlGenerator
from idms_db2_phase2.services.validation_service import ValidationService


class ConversionService:
    def __init__(
        self,
    ) -> None:
        self.validation_service = ValidationService()

    def convert(
        self,
        conversion_input: ConversionInput,
    ) -> ConversionResult:
        validation_messages = self.validation_service.validate(
            conversion_input,
        )

        if validation_messages:
            return ConversionResult(
                converted_cobol="",
                validation_messages=validation_messages,
                operations=[],
            )

        sql_generator = SqlGenerator(
            conversion_input.sheet_mapping_rows,
        )

        transformer = CobolTransformer(
            sql_generator,
        )

        converted_cobol, transform_messages, operations = transformer.transform(
            cobol_text=conversion_input.idms_cobol_text,
            target_program_id=conversion_input.target_program_id,
        )

        validation_messages.extend(
            transform_messages,
        )

        field_rewriter = FieldReferenceRewriter(
            mapping_rows=conversion_input.sheet_mapping_rows,
            dclgen_columns=conversion_input.dclgen_columns,
        )

        converted_cobol = field_rewriter.rewrite(
            converted_cobol,
        )

        validation_messages.extend(
            field_rewriter.rewrite_messages,
        )

        db2_infrastructure_generator = Db2InfrastructureGenerator()

        converted_cobol, infrastructure_messages = db2_infrastructure_generator.apply(
            cobol_text=converted_cobol,
            dclgen_columns=conversion_input.dclgen_columns,
            operations=operations,
        )

        validation_messages.extend(
            infrastructure_messages,
        )

        cursor_paragraph_generator = Db2CursorParagraphGenerator(
            mapping_rows=conversion_input.sheet_mapping_rows,
            dclgen_columns=conversion_input.dclgen_columns,
            operations=operations,
        )

        converted_cobol, cursor_paragraph_messages = cursor_paragraph_generator.apply(
            converted_cobol,
        )

        validation_messages.extend(
            cursor_paragraph_messages,
        )

        if conversion_input.auto_fix_pic_length_mismatches:
            pic_length_auto_fixer = PicLengthAutoFixer()

            converted_cobol = pic_length_auto_fixer.fix(
                source_cobol_text=conversion_input.idms_cobol_text,
                converted_cobol_text=converted_cobol,
            )

            validation_messages.extend(
                pic_length_auto_fixer.messages,
            )

        production_validator = ProductionValidator()

        production_messages = production_validator.validate(
            source_cobol_text=conversion_input.idms_cobol_text,
            converted_cobol_text=converted_cobol,
            mapping_rows=conversion_input.sheet_mapping_rows,
        )

        validation_messages.extend(
            production_messages,
        )

        return ConversionResult(
            converted_cobol=converted_cobol,
            validation_messages=validation_messages,
            operations=operations,
        )