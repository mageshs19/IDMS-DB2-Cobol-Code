import re


class NameNormalizer:
    @staticmethod
    def normalize(value: str | None) -> str:
        if value is None:
            return ""

        text = str(value).strip().upper()

        if not text:
            return ""

        text = text.replace("-", "_")
        text = text.replace(" ", "_")
        text = re.sub(r"[^A-Z0-9_]", "_", text)
        text = re.sub(r"_+", "_", text)

        return text.strip("_")

    @staticmethod
    def to_cobol(value: str | None) -> str:
        return NameNormalizer.normalize(value).replace("_", "-")

    @staticmethod
    def compact(value: str | None) -> str:
        text = NameNormalizer.normalize(value)
        return re.sub(r"[^A-Z0-9]", "", text)

    @staticmethod
    def remove_record_suffix(value: str | None) -> str:
        text = NameNormalizer.normalize(value)

        if not text:
            return ""

        parts = text.rsplit("_", 1)

        if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) == 4:
            return parts[0]

        return text