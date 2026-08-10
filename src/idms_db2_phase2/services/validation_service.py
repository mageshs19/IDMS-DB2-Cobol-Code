from idms_db2_phase2.domain.models import ConversionInput


class ValidationService:
    def validate(
        self,
        conversion_input: ConversionInput,
    ) -> list[str]:
        messages: list[str] = []

        if not conversion_input.sheet_mapping_rows:
            messages.append(
                "Sheet Mapping is required and must contain rows."
            )

        if not conversion_input.dclgen_columns:
            messages.append(
                "At least one DCLGEN file is required."
            )

        if not conversion_input.idms_cobol_text.strip():
            messages.append(
                "IDMS COBOL PDF is required and must contain extractable text."
            )

        return messages