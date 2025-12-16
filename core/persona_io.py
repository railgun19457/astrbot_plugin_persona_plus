from __future__ import annotations

from pathlib import Path

import aiofiles

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .message_utils import first_component_of_types, has_component_of_types


async def download_and_parse_persona_file(
    *,
    event: AstrMessageEvent,
    persona_id: str,
    persona_data_dir: Path,
) -> str:
    file_component = first_component_of_types(event, (Comp.File,))
    if not isinstance(file_component, Comp.File):
        raise ValueError("未检测到文本文件，请附带或引用一个 .txt/.md 文件。")

    save_path = persona_data_dir / f"{persona_id}.txt"

    temp_path = await file_component.get_file()
    if not temp_path:
        raise ValueError("文件获取失败，请重新发送。")

    src = Path(temp_path)

    async with aiofiles.open(src, "rb") as src_fp:
        raw_data = await src_fp.read()

    if not raw_data:
        raise ValueError("文件为空，无法创建人格。")

    async with aiofiles.open(save_path, "wb") as dest_fp:
        await dest_fp.write(raw_data)

    logger.info(
        "Persona+ 已保存人格文件 %s 至 %s (大小: %d 字节)",
        persona_id,
        save_path,
        len(raw_data),
    )

    content: str | None = None
    encodings = ["utf-8", "gbk"]
    errors: list[str] = []

    for encoding in encodings:
        try:
            content = raw_data.decode(encoding)
            logger.debug("Persona+ 使用 %s 编码成功解析文件", encoding)
            break
        except (UnicodeDecodeError, LookupError) as exc:
            errors.append(f"{encoding}: {exc}")

    if content is None:
        error_detail = "; ".join(errors)
        raise ValueError(
            f"文件编码不支持（尝试了 UTF-8 和 GBK）。"
            f"文件已保存至 {save_path}，请检查文件编码。错误详情: {error_detail}"
        )

    return content.strip()


async def extract_persona_from_event(
    *,
    event: AstrMessageEvent,
    persona_id: str,
    persona_data_dir: Path,
) -> str:
    if has_component_of_types(event, (Comp.File,)):
        return await download_and_parse_persona_file(
            event=event, persona_id=persona_id, persona_data_dir=persona_data_dir
        )

    text = event.message_str.strip()
    if text:
        return text

    raise ValueError("未检测到可解析的文本内容。请直接发送人格文本或上传文本文件。")
