import re


class IdmsResidualConverter:
    """
    Converts additional residual IDMS statements that are validated as forbidden.

    Existing uploaded snippets show conversion support for:
    - READY
    - FINISH
    - COMMIT
    - OBTAIN CALC
    - FIND FIRST WITHIN
    - STORE
    - MODIFY
    - ERASE
    - DB-REC-NOT-FOUND
    - DB-END-OF-SET

    This helper adds safe fallback handling for other residual IDMS constructs
    that should not remain executable in DB2 COBOL.
    """

    def convert_line(
        self,
        line: str,
    ) -> list[str] | None:
        stripped = line.strip()
        upper = stripped.upper()

        if not stripped:
            return [line]

        if self._is_bind_statement(upper):
            return [
                f"* DB2: Removed IDMS BIND statement: {stripped}",
                "CONTINUE.",
            ]

        if self._is_usage_mode_update(upper):
            return [
                f"* DB2: Removed IDMS usage mode statement: {stripped}",
                "CONTINUE.",
            ]

        if self._is_find_current(upper):
            return [
                f"* DB2: FIND CURRENT requires DB2 cursor current-row handling: {stripped}",
                "CONTINUE.",
            ]

        if self._is_connect(upper):
            return [
                f"* DB2: Removed IDMS CONNECT statement. Relationship is handled by DB2 keys: {stripped}",
                "CONTINUE.",
            ]

        if self._is_disconnect(upper):
            return [
                f"* DB2: Removed IDMS DISCONNECT statement. Relationship is handled by DB2 keys: {stripped}",
                "CONTINUE.",
            ]

        if self._is_idms_status_perform(upper):
            return [
                f"* DB2: Removed IDMS status paragraph call: {stripped}",
                "CONTINUE.",
            ]

        if self._is_idms_abort_perform(upper):
            return [
                f"* DB2: Removed IDMS abort paragraph call: {stripped}",
                "CONTINUE.",
            ]

        return None

    def _is_bind_statement(
        self,
        upper: str,
    ) -> bool:
        return bool(re.search(r"^\s*BIND\b", upper))

    def _is_usage_mode_update(
        self,
        upper: str,
    ) -> bool:
        return bool(re.search(r"\bUSAGE-MODE\s+IS\s+UPDATE\b", upper))

    def _is_find_current(
        self,
        upper: str,
    ) -> bool:
        return bool(re.search(r"\bFIND\s+CURRENT\b", upper))

    def _is_connect(
        self,
        upper: str,
    ) -> bool:
        return bool(re.search(r"^\s*CONNECT\b", upper))

    def _is_disconnect(
        self,
        upper: str,
    ) -> bool:
        return bool(re.search(r"^\s*DISCONNECT\b", upper))

    def _is_idms_status_perform(
        self,
        upper: str,
    ) -> bool:
        return bool(re.search(r"\bPERFORM\b.*\bIDMS-STATUS\b", upper))

    def _is_idms_abort_perform(
        self,
        upper: str,
    ) -> bool:
        return bool(re.search(r"\bPERFORM\b.*\bIDMS-ABORT\b", upper))