from __future__ import annotations

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context


def set_default_persona(
    *,
    context: Context,
    scope: str,
    unified_msg_origin: str,
    persona_id: str,
) -> None:
    if scope == "session":
        config = context.astrbot_config_mgr.get_conf(unified_msg_origin)
        if config:
            provider_settings = config.setdefault("provider_settings", {})
            provider_settings["default_personality"] = persona_id
            config.save_config()
    elif scope == "global":
        config = context.astrbot_config_mgr.default_conf
        provider_settings = config.setdefault("provider_settings", {})
        provider_settings["default_personality"] = persona_id
        config.save_config()


async def update_current_conversation(
    *,
    context: Context,
    unified_msg_origin: str,
    persona_id: str,
    history: list[dict] | None,
) -> None:
    cid = await context.conversation_manager.get_curr_conversation_id(
        unified_msg_origin
    )
    if not cid:
        return
    await context.conversation_manager.update_conversation(
        unified_msg_origin=unified_msg_origin,
        conversation_id=cid,
        persona_id=persona_id,
        history=history,
    )


async def switch_persona(
    *,
    context: Context,
    persona_mgr,
    qq_sync,
    event: AstrMessageEvent,
    persona_id: str,
    scope: str,
    clear_context_on_switch: bool,
    announce: str | None = None,
) -> MessageEventResult | None:
    await persona_mgr.get_persona(persona_id)

    umo = event.unified_msg_origin
    history_reset = [] if clear_context_on_switch else None

    set_default_persona(
        context=context,
        scope=scope,
        unified_msg_origin=umo,
        persona_id=persona_id,
    )

    await update_current_conversation(
        context=context,
        unified_msg_origin=umo,
        persona_id=persona_id,
        history=history_reset,
    )

    # 如果清除了上下文，同时清除长期记忆（LTM）
    if clear_context_on_switch:
        event.set_extra("_clean_ltm_session", True)

    if scope == "conversation":
        logger.info("Persona+ 已切换会话人格至 %s (scope=%s)", persona_id, scope)
    elif scope in {"session", "global"}:
        logger.info(
            "Persona+ 已更新%s配置默认人格至 %s (scope=%s)",
            "会话" if scope == "session" else "全局",
            persona_id,
            scope,
        )

    await qq_sync.maybe_sync_profile(event, persona_id)

    if announce:
        return event.plain_result(announce)
    return None
