import re

from idms_db2_phase2.domain.models import DclgenColumn, IdmsOperation
from idms_db2_phase2.services.name_normalizer import NameNormalizer


class Db2InfrastructureGenerator:
    """
    Adds production DB2 infrastructure to converted COBOL.

    This generator intentionally preserves:
        - Original comments
        - Existing paragraph names
        - Existing program name unless Target PROGRAM-ID was explicitly provided earlier
        - Existing business paragraph structure

    It only inserts missing DB2 infrastructure:
        - SQLERRRWS include
        - SQLCA include
        - DCLGEN includes based on uploaded DCLGEN table names
        - SQL-LOCATION working-storage
        - Cursor end-of-cursor flags based on detected set navigation

    It does not generate business-specific timestamp/date/write routines.
    Those remain explicit business-template work.
    """

    DB2_BLOCK_MARKER = "* DB2 SQLCA, SQL ERROR WORKING STORAGE, DCLGEN INCLUDES, AND CURSOR FLAGS"

    def apply(
        self,
        cobol_text: str,
        dclgen_columns: list[DclgenColumn],
        operations: list[IdmsOperation],
    ) -> tuple[str, list[str]]:
        messages: list[str] = []

        if not cobol_text:
            return cobol_text, messages

        text = cobol_text

        dclgen_include_names = self._dclgen_include_names(
            dclgen_columns=dclgen_columns,
        )

        cursor_set_names = self._cursor_set_names(
            operations=operations,
        )

        infrastructure_block = self._build_infrastructure_block(
            dclgen_include_names=dclgen_include_names,
            cursor_set_names=cursor_set_names,
        )

        if not infrastructure_block.strip():
            messages.append(
                "DB2 infrastructure: no infrastructure block generated because no DCLGEN includes or cursor sets were detected."
            )
            return text, messages

        if self.DB2_BLOCK_MARKER in text:
            messages.append(
                "DB2 infrastructure: existing generated DB2 infrastructure block detected; not inserted again."
            )
            return text, messages

        text, inserted = self._insert_after_working_storage(
            text=text,
            block=infrastructure_block,
        )

        if inserted:
            messages.append(
                "DB2 infrastructure: inserted SQLERRRWS, SQLCA, DCLGEN includes, SQL-LOCATION, and cursor flags."
            )
        else:
            text, inserted = self._insert_before_procedure_division(
                text=text,
                block=infrastructure_block,
            )

            if inserted:
                messages.append(
                    "DB2 infrastructure: WORKING-STORAGE SECTION not found; inserted block before PROCEDURE DIVISION."
                )
            else:
                text = infrastructure_block + "\n\n" + text
                messages.append(
                    "DB2 infrastructure: WORKING-STORAGE and PROCEDURE DIVISION not found; inserted block at top of file."
                )

        return text, messages

    def _build_infrastructure_block(
        self,
        dclgen_include_names: list[str],
        cursor_set_names: list[str],
    ) -> str:
        lines: list[str] = [
            "",
            "      ******************************************************************",
            f"      * {self.DB2_BLOCK_MARKER}",
            "      ******************************************************************",
        ]

        lines.extend(
            self._build_include_lines(
                dclgen_include_names=dclgen_include_names,
            )
        )

        lines.append("")
        lines.extend(
            self._build_sql_location_lines(),
        )

        cursor_flag_lines = self._build_cursor_flag_lines(
            cursor_set_names=cursor_set_names,
        )

        if cursor_flag_lines:
            lines.append("")
            lines.extend(
                cursor_flag_lines,
            )

        lines.append("")

        return "\n".join(lines)

    def _build_include_lines(
        self,
        dclgen_include_names: list[str],
    ) -> list[str]:
        lines: list[str] = []

        include_names = [
            "SQLERRRWS",
            "SQLCA",
        ]

        include_names.extend(
            dclgen_include_names,
        )

        seen: set[str] = set()

        for include_name in include_names:
            normalized_name = NameNormalizer.normalize(
                include_name,
            )

            if not normalized_name:
                continue

            if normalized_name in seen:
                continue

            seen.add(
                normalized_name,
            )

            lines.extend(
                [
                    "           EXEC SQL",
                    f"                INCLUDE {normalized_name}",
                    "           END-EXEC.",
                ]
            )

        return lines

    def _build_sql_location_lines(
        self,
    ) -> list[str]:
        return [
            "      ******************************************************************",
            "      * DB2 SQL ERROR LOCATION",
            "      ******************************************************************",
            "       01 SQL-LOCATION                 PIC X(40) VALUE SPACES.",
        ]

    def _build_cursor_flag_lines(
        self,
        cursor_set_names: list[str],
    ) -> list[str]:
        if not cursor_set_names:
            return []

        lines: list[str] = [
            "      ******************************************************************",
            "      * DB2 CURSOR END-OF-CURSOR FLAGS",
            "      ******************************************************************",
        ]

        for set_name in cursor_set_names:
            cursor_name = self._cursor_name(
                set_name,
            )

            flag_name = f"WS-{cursor_name}-FLAG"
            not_eoc_name = f"{cursor_name}-NOT-EOC"
            eoc_name = f"{cursor_name}-EOC"

            lines.extend(
                [
                    f"       01 {flag_name:<30} PIC X VALUE 'N'.",
                    f"          88 {not_eoc_name:<27} VALUE 'N'.",
                    f"          88 {eoc_name:<31} VALUE 'Y'.",
                    "",
                ]
            )

        return lines

    def _dclgen_include_names(
        self,
        dclgen_columns: list[DclgenColumn],
    ) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()

        for column in dclgen_columns:
            table_name = NameNormalizer.normalize(
                column.table_name,
            )

            if not table_name:
                continue

            if table_name in seen:
                continue

            seen.add(
                table_name,
            )

            names.append(
                table_name,
            )

        return names

    def _cursor_set_names(
        self,
        operations: list[IdmsOperation],
    ) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()

        for operation in operations:
            operation_name = str(
                operation.operation or "",
            ).upper()

            if operation_name not in {
                "OBTAIN_FIRST",
                "OBTAIN_NEXT",
                "FIND_FIRST",
            }:
                continue

            set_name = NameNormalizer.normalize(
                operation.set_name,
            )

            if not set_name:
                continue

            if set_name in seen:
                continue

            seen.add(
                set_name,
            )

            names.append(
                set_name,
            )

        return names

    def _cursor_name(
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

    def _insert_after_working_storage(
        self,
        text: str,
        block: str,
    ) -> tuple[str, bool]:
        pattern = re.compile(
            r"(^\s*WORKING-STORAGE\s+SECTION\.\s*$)",
            flags=re.IGNORECASE | re.MULTILINE,
        )

        match = pattern.search(
            text,
        )

        if not match:
            return text, False

        insert_position = match.end()

        updated_text = (
            text[:insert_position]
            + "\n"
            + block
            + text[insert_position:]
        )

        return updated_text, True

    def _insert_before_procedure_division(
        self,
        text: str,
        block: str,
    ) -> tuple[str, bool]:
        pattern = re.compile(
            r"(^\s*PROCEDURE\s+DIVISION\.\s*$)",
            flags=re.IGNORECASE | re.MULTILINE,
        )

        match = pattern.search(
            text,
        )

        if not match:
            return text, False

        insert_position = match.start()

        updated_text = (
            text[:insert_position]
            + block
            + "\n"
            + text[insert_position:]
        )

        return updated_text, True