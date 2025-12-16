from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class KeywordMapping:
    keyword: str
    persona_id: str
    reply_template: str = ""

    def matches(self, text: str) -> bool:
        return self.keyword.lower() in text.lower()


def parse_mapping_entry(entry: str) -> KeywordMapping:
    left, sep, right = entry.partition(":")
    if sep == "":
        raise ValueError(f"无效的关键词映射格式：{entry!r}，应为 关键词:人格ID。")

    persona_id = right.strip()
    if not persona_id:
        raise ValueError(f"无效的人格 ID：{entry!r}。")

    keyword = left.strip()
    if not keyword:
        raise ValueError(f"无效的关键词内容：{entry!r}。")

    return KeywordMapping(keyword=keyword, persona_id=persona_id)
