from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.utils.session_waiter import SessionController, session_waiter

from .message_utils import has_component_of_types
from .persona_io import extract_persona_from_event


def schedule_persona_wait(
    *,
    event: AstrMessageEvent,
    persona_id: str,
    mode: str,
    manage_wait_timeout: int,
    persona_data_dir: Path,
    qq_sync,
    create_persona: Callable[[str, str, list | None, list | None], asyncio.Future],
    update_persona: Callable[[str, str, list | None], asyncio.Future],
    register_task: Callable[[asyncio.Task], None],
) -> None:
    if mode == "avatar":
        accepted = (Comp.Image, Comp.File)
        action_zh = "头像上传"
    elif mode == "create":
        accepted = (Comp.File,)
        action_zh = "创建"
    elif mode == "update":
        accepted = (Comp.File,)
        action_zh = "更新"
    else:
        logger.warning("Persona+ 未知的等待模式: %s", mode)
        return

    @session_waiter(timeout=manage_wait_timeout)
    async def waiter(
        controller: SessionController, next_event: AstrMessageEvent
    ) -> None:
        if not next_event.message_str.strip() and not has_component_of_types(
            next_event, accepted
        ):
            logger.debug("Persona+ 已过滤空消息事件（可能是 input_status 通知）")
            controller.keep(timeout=manage_wait_timeout, reset_timeout=True)
            return

        try:
            if mode == "avatar":
                await qq_sync.save_avatar_from_event(next_event, persona_id)
                qq_sync.reset_persona_cache(persona_id)
            else:
                raw_text = await extract_persona_from_event(
                    event=next_event,
                    persona_id=persona_id,
                    persona_data_dir=persona_data_dir,
                )
                system_prompt = raw_text
                begin_dialogs: list = []

                if mode == "create":
                    await create_persona(persona_id, system_prompt, begin_dialogs, None)
                else:
                    await update_persona(persona_id, system_prompt, begin_dialogs)
        except ValueError as exc:
            await next_event.send(next_event.plain_result(f"{action_zh}失败：{exc}"))
        except Exception as exc:  # noqa: BLE001
            logger.exception("%s人格时出现异常", action_zh)
            await next_event.send(next_event.plain_result(f"{action_zh}失败：{exc}"))
        else:
            suffix = "头像上传成功。" if mode == "avatar" else f"{action_zh}成功。"
            await next_event.send(
                next_event.plain_result(f"人格 {persona_id} {suffix}")
            )
        finally:
            controller.stop()

    async def run_wait() -> None:
        try:
            await waiter(event)
        except TimeoutError:
            msg = (
                "等待头像图片超时，操作已取消。"
                if mode == "avatar"
                else "等待人格内容超时，操作已取消。"
            )
            await event.send(event.plain_result(msg))
        except Exception as exc:  # noqa: BLE001
            logger.exception("%s等待流程异常", action_zh)
            await event.send(event.plain_result(f"{action_zh}流程异常：{exc}"))

    task = asyncio.create_task(run_wait())
    register_task(task)
    event.stop_event()
