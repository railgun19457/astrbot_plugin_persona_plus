from __future__ import annotations

from .models import KeywordMapping


def match_keyword(mappings: list[KeywordMapping], text: str) -> KeywordMapping | None:
    for mapping in mappings:
        if mapping.matches(text):
            return mapping
    return None
