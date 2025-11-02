from __future__ import annotations

from pathlib import Path
from typing import Optional

import aiofiles
import aiohttp

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.core.star.star_tools import StarTools


class QQProfileSync:
    def __init__(self, context):
        self.context = context
        self.enabled: bool = False
        self.sync_nickname: bool = True
        self.sync_avatar: bool = False
        self.nickname_sync_mode: str = "profile"  # profile, group_card, hybrid
        self.nickname_template: str = "{persona_id}"
        self._last_synced_persona: dict[str, str] = {}
        self.avatar_dir: Path = (
            StarTools.get_data_dir("astrbot_plugin_persona_plus") / "avatars"
        )
        self.avatar_dir.mkdir(parents=True, exist_ok=True)

    def load_config(self, config: Optional[AstrBotConfig]) -> None:
        if not config:
            self.sync_nickname = True
            self.sync_avatar = False
            self.nickname_sync_mode = "profile"
            self.enabled = self.sync_nickname or self.sync_avatar
            self.nickname_template = "{persona_id}"
            return

        self.sync_nickname = config.get("sync_nickname_on_switch", True)
        self.sync_avatar = config.get("sync_avatar_on_switch", False)
        self.nickname_sync_mode = config.get("nickname_sync_mode", "profile")
        if self.nickname_sync_mode not in {"profile", "group_card", "hybrid"}:
            logger.warning(
                "Persona+ 昵称同步模式 %s 无效，将使用默认值 profile",
                self.nickname_sync_mode,
            )
            self.nickname_sync_mode = "profile"
        self.enabled = self.sync_nickname or self.sync_avatar
        self.nickname_template = config.get("nickname_template", "{persona_id}")

    def describe_settings(self) -> str:
        return (
            f"enabled={self.enabled}, nickname={self.sync_nickname}, "
            f"avatar={self.sync_avatar}, mode={self.nickname_sync_mode}"
        )

    def format_nickname(self, persona_id: str) -> str:
        try:
            nickname = self.nickname_template.format(persona_id=persona_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Persona+ 昵称模板解析失败：%s，使用人格 ID", exc)
            nickname = persona_id
        return nickname[:60] if nickname else persona_id[:60]

    def get_avatar_path(self, persona_id: str) -> Path:
        return self.avatar_dir / f"{persona_id}.jpg"

    async def download_and_save_avatar(self, url: str, save_path: Path) -> None:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"下载头像失败，状态码 {resp.status}")
                async with aiofiles.open(save_path, "wb") as f:
                    await f.write(await resp.read())
        logger.debug("Persona+ 已下载头像至 %s", save_path)

    def extract_image_component(
        self, event: AstrMessageEvent
    ) -> Optional[Comp.BaseMessageComponent]:
        for component in event.get_messages():
            if isinstance(component, (Comp.Image, Comp.File)):
                return component
            if isinstance(component, Comp.Reply) and component.chain:
                for reply_component in component.chain:
                    if isinstance(reply_component, (Comp.Image, Comp.File)):
                        return reply_component
        return None

    async def save_avatar_from_event(
        self, event: AstrMessageEvent, persona_id: str
    ) -> Path:
        image_component = self.extract_image_component(event)
        if not image_component:
            raise ValueError("未检测到图片或文件，请附带或引用一张图片。")

        avatar_path = self.get_avatar_path(persona_id)

        if isinstance(image_component, Comp.Image) and getattr(
            image_component, "url", None
        ):
            await self.download_and_save_avatar(image_component.url, avatar_path)
            return avatar_path

        if isinstance(image_component, Comp.File):
            temp_path = await image_component.get_file()
            if not temp_path:
                raise ValueError("文件获取失败，请重新发送。")
            src = Path(temp_path)
            if src.suffix.lower() not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
                raise ValueError("仅支持 jpg/jpeg/png/gif/webp 图片文件。")
            async with aiofiles.open(src, "rb") as src_fp:
                data = await src_fp.read()
            async with aiofiles.open(avatar_path, "wb") as dest_fp:
                await dest_fp.write(data)
            return avatar_path

        raise ValueError("暂不支持此类消息，请发送图片或图片文件。")

    def reset_persona_cache(self, persona_id: str) -> None:
        to_remove = [
            key
            for key, value in self._last_synced_persona.items()
            if value == persona_id
        ]
        for key in to_remove:
            self._last_synced_persona.pop(key, None)

    def delete_avatar(self, persona_id: str) -> None:
        avatar_path = self.get_avatar_path(persona_id)
        if avatar_path.exists():
            avatar_path.unlink()
            logger.info("Persona+ 已删除人格 %s 的头像", persona_id)
        self.reset_persona_cache(persona_id)

    async def maybe_sync_profile(
        self,
        event: AstrMessageEvent,
        persona_id: str,
        *,
        force: bool = False,
    ) -> None:
        if not isinstance(event, AiocqhttpMessageEvent):
            return

        if not (self.enabled or force):
            return

        sync_nickname = force or self.sync_nickname
        sync_avatar = force or self.sync_avatar
        if not (sync_nickname or sync_avatar):
            return

        bot_key = f"{event.get_platform_id()}:{event.get_self_id()}"
        if not force and self._last_synced_persona.get(bot_key) == persona_id:
            return

        nickname_applied = False
        avatar_synced = False
        is_group = bool(event.get_group_id())

        if sync_nickname:
            nickname = self.format_nickname(persona_id)

            # 根据模式决定同步策略
            if self.nickname_sync_mode == "profile":
                # 只修改 QQ 昵称
                nickname_applied = await self._sync_qq_profile(event, nickname)
            elif self.nickname_sync_mode == "group_card":
                # 群聊中只修改群名片，私聊不修改
                if is_group:
                    nickname_applied = await self._sync_group_card(event, nickname)
            elif self.nickname_sync_mode == "hybrid":
                # 群聊中修改群名片，私聊中修改 QQ 昵称
                if is_group:
                    nickname_applied = await self._sync_group_card(event, nickname)
                else:
                    nickname_applied = await self._sync_qq_profile(event, nickname)

        if sync_avatar:
            avatar_path = self.get_avatar_path(persona_id)
            if avatar_path.exists():
                if hasattr(event.bot, "set_qq_avatar"):
                    try:
                        await event.bot.set_qq_avatar(file=str(avatar_path))
                        logger.debug("Persona+ 已同步头像 %s", avatar_path.as_posix())
                        avatar_synced = True
                    except Exception as exc:  # noqa: BLE001
                        logger.error("Persona+ 同步头像失败：%s", exc)
                else:
                    logger.warning(
                        "Persona+ 当前适配器未实现 set_qq_avatar 接口，跳过头像同步。"
                    )
            else:
                logger.debug(
                    "Persona+ 未找到人格 %s 的头像缓存，跳过头像同步", persona_id
                )

        if nickname_applied or avatar_synced:
            self._last_synced_persona[bot_key] = persona_id

    async def _sync_qq_profile(
        self, event: AiocqhttpMessageEvent, nickname: str
    ) -> bool:
        """同步 QQ 昵称"""
        if hasattr(event.bot, "set_qq_profile"):
            try:
                await event.bot.set_qq_profile(nickname=nickname)
                logger.debug("Persona+ 已同步 QQ 昵称为 %s", nickname)
                return True
            except Exception as exc:  # noqa: BLE001
                logger.error("Persona+ 同步 QQ 昵称失败：%s", exc)
        else:
            logger.warning(
                "Persona+ 当前适配器未实现 set_qq_profile 接口，跳过 QQ 昵称同步。"
            )
        return False

    async def _sync_group_card(self, event: AiocqhttpMessageEvent, card: str) -> bool:
        """同步群名片"""
        group_id = event.get_group_id()
        if not group_id:
            return False

        user_id = event.get_self_id()
        if hasattr(event.bot, "call_action"):
            try:
                await event.bot.call_action(
                    "set_group_card",
                    group_id=int(group_id),
                    user_id=int(user_id),
                    card=card,
                )
                logger.debug("Persona+ 已同步群名片为 %s (群 %s)", card, group_id)
                return True
            except Exception as exc:  # noqa: BLE001
                logger.error("Persona+ 同步群名片失败：%s", exc)
        else:
            logger.warning(
                "Persona+ 当前适配器未实现 call_action 接口，跳过群名片同步。"
            )
        return False

    def clear_cache(self) -> None:
        self._last_synced_persona.clear()
