from __future__ import annotations

from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context


def is_admin(context: Context, event: AstrMessageEvent) -> bool:
    sender_id = str(event.get_sender_id())
    default_conf = context.get_config()
    admin_ids = {str(admin) for admin in default_conf.get("admins_id", [])}
    return sender_id in admin_ids


def check_permission(
    *,
    context: Context,
    event: AstrMessageEvent,
    command: str,
    admin_commands: set[str],
) -> tuple[bool, str]:
    if command in admin_commands and not is_admin(context, event):
        return False, "此操作需要管理员权限。"
    return True, ""
