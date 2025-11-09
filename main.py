from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import aiofiles

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.persona_mgr import PersonaManager
from astrbot.core.star.star_tools import StarTools
from astrbot.core.utils.session_waiter import SessionController, session_waiter

from .qq_profile_sync import QQProfileSync


@dataclass
class KeywordMapping:
    keyword: str
    persona_id: str
    reply_template: str = ""

    def matches(self, text: str) -> bool:
        return self.keyword.lower() in text.lower()


@register(
    "persona_plus",
    "Railgun",
    "提供指令管理人格(支持切换、创建、查看、更新、删除)、关键词自动切换、QQ头像/昵称的同步修改",
    "1.3",
    "https://github.com/railgun19457/astrbot_plugin_persona_plus",
)
class PersonaPlus(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.context: Context = context
        self.config: AstrBotConfig | None = config
        self.persona_mgr: PersonaManager = context.persona_manager
        self.keyword_mappings: list[KeywordMapping] = []
        self.auto_switch_scope: str = "conversation"
        self.keyword_switch_enabled: bool = True
        self.manage_wait_timeout: int = 120
        self.admin_commands: set[str] = {"create", "update", "delete"}
        self.auto_switch_announce: bool = False
        self.clear_context_on_switch: bool = False
        self.qq_sync = QQProfileSync(context)
        # 初始化人格数据目录
        self.persona_data_dir: Path = (
            StarTools.get_data_dir("astrbot_plugin_persona_plus") / "persona_files"
        )
        self.persona_data_dir.mkdir(parents=True, exist_ok=True)
        self._load_config()

    # ==================== 通用小工具 ====================
    @staticmethod
    def _has_component_of_types(
        event: AstrMessageEvent, types: tuple[type, ...]
    ) -> bool:
        """检查消息链以及引用回复链中是否包含指定类型的组件。

        Args:
            event: 消息事件
            types: 组件类型元组，例如 (Comp.File,) 或 (Comp.Image, Comp.File)
        """
        for component in event.get_messages():
            if isinstance(component, types):
                return True
            if isinstance(component, Comp.Reply) and component.chain:
                for reply_component in component.chain:
                    if isinstance(reply_component, types):
                        return True
        return False

    def _load_config(self) -> None:
        if not self.config:
            self.qq_sync.load_config(None)
            logger.warning("Persona+ 未载入专用配置，将使用默认值。")
            return

        mappings_raw = self.config.get("keyword_mappings", "")
        loaded: list[KeywordMapping] = []

        if mappings_raw is None:
            entries: list[str] = []
        elif isinstance(mappings_raw, str):
            entries = mappings_raw.splitlines()
        else:
            logger.warning(
                "Persona+ 关键词配置应为文本，实际收到 %r (类型 %s)",
                mappings_raw,
                type(mappings_raw).__name__,
            )
            entries = str(mappings_raw).splitlines()

        for raw_entry in entries:
            entry = raw_entry.strip()
            if not entry or entry.startswith("#"):
                continue
            try:
                loaded.append(self._parse_mapping_entry(entry))
            except Exception as exc:  # noqa: BLE001
                logger.error("Persona+ 解析关键词配置失败: %s", exc)

        self.keyword_mappings = [m for m in loaded if m.keyword and m.persona_id]
        self.auto_switch_scope = self.config.get("auto_switch_scope", "conversation")
        self.keyword_switch_enabled = bool(
            self.config.get("enable_keyword_switching", True)
        )
        admin_commands_raw = self.config.get(
            "admin_commands", ["create", "update", "delete"]
        )
        if isinstance(admin_commands_raw, list):
            self.admin_commands = {cmd.lower().strip() for cmd in admin_commands_raw}
        else:
            logger.warning(
                "Persona+ admin_commands 配置应为列表，实际收到 %r，已使用默认值",
                admin_commands_raw,
            )
            self.admin_commands = {"create", "update", "delete"}
        self.auto_switch_announce = bool(
            self.config.get("enable_auto_switch_announce", False)
        )
        self.clear_context_on_switch = bool(
            self.config.get("clear_context_on_switch", False)
        )
        raw_timeout = self.config.get("manage_wait_timeout_seconds", 60)
        try:
            timeout = int(raw_timeout)
        except (TypeError, ValueError):
            logger.warning(
                "Persona+ manage_wait_timeout_seconds=%r 非法，使用默认值 60",
                raw_timeout,
            )
            timeout = 60
        if timeout <= 0:
            logger.warning(
                "Persona+ manage_wait_timeout_seconds=%r 必须为正数，已重置为 60",
                raw_timeout,
            )
            timeout = 60
        self.manage_wait_timeout = timeout
        self.qq_sync.load_config(self.config)

        logger.info(
            "Persona+ 配置加载完成：关键词 %d 项，自动切换范围=%s，关键词自动切换=%s，QQ同步=%s",
            len(self.keyword_mappings),
            self.auto_switch_scope,
            self.keyword_switch_enabled,
            self.qq_sync.describe_settings(),
        )
        logger.info(
            "Persona+ 权限配置：admin_commands=%s",
            sorted(self.admin_commands),
        )
        logger.info(
            "Persona+ 管理操作等待超时：manage_wait_timeout=%ss",
            self.manage_wait_timeout,
        )
        logger.info(
            "Persona+ 自动切换提示：enable_auto_switch_announce=%s",
            self.auto_switch_announce,
        )
        logger.info(
            "Persona+ 切换后清空上下文：clear_context_on_switch=%s",
            self.clear_context_on_switch,
        )

    # ==================== 工具函数 ====================
    def _is_admin(self, event: AstrMessageEvent) -> bool:
        """检查用户是否是管理员。

        检查发送者ID是否在配置文件的 admins_id 列表中
        """
        sender_id = str(event.get_sender_id())
        default_conf = self.context.get_config()
        admin_ids = {str(admin) for admin in default_conf.get("admins_id", [])}

        return sender_id in admin_ids

    def check_permission(
        self, event: AstrMessageEvent, command: str
    ) -> tuple[bool, str]:
        """统一的权限检查函数。

        Args:
            event: 消息事件
            command: 指令名称 (help, list, view, create, update, delete, avatar)

        Returns:
            (是否有权限, 错误提示信息)
            - (True, "") - 有权限
            - (False, "错误信息") - 无权限
        """
        # 检查指令是否需要管理员权限
        if command in self.admin_commands and not self._is_admin(event):
            return False, "此操作需要管理员权限。"

        return True, ""

    @staticmethod
    def _parse_mapping_entry(entry: str) -> KeywordMapping:
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

    @staticmethod
    def _parse_persona_payload(raw_text: str) -> tuple[str, list]:
        """将用户传入的全部文本作为 system_prompt"""
        return raw_text, []

    async def _download_and_parse_persona_file(
        self, event: AstrMessageEvent, persona_id: str
    ) -> str:
        """从消息中提取文本文件组件，下载并解析为文本内容（仅支持 File）。

        Args:
            event: 消息事件
            persona_id: 人格 ID，用于命名保存的文件

        Returns:
            解析出的文本内容

        Raises:
            ValueError: 未找到文件/无法解析
        """
        # 统一使用 QQ 同步工具内的提取逻辑，避免重复实现
        file_component = self.qq_sync.extract_image_component(event)
        if not isinstance(file_component, Comp.File):
            raise ValueError("未检测到文本文件，请附带或引用一个 .txt/.md 文件。")

        # 保存路径: persona_files/persona_id.txt
        save_path = self.persona_data_dir / f"{persona_id}.txt"

        temp_path = await file_component.get_file()
        if not temp_path:
            raise ValueError("文件获取失败，请重新发送。")

        src = Path(temp_path)

        # 第一步：读取原始二进制数据
        async with aiofiles.open(src, "rb") as src_fp:
            raw_data = await src_fp.read()

        if not raw_data:
            raise ValueError("文件为空，无法创建人格。")

        # 第二步：保存原始文件（二进制模式，保证不丢失数据）
        async with aiofiles.open(save_path, "wb") as dest_fp:
            await dest_fp.write(raw_data)

        logger.info(
            "Persona+ 已保存人格文件 %s 至 %s (大小: %d 字节)",
            persona_id,
            save_path,
            len(raw_data),
        )

        # 第三步：尝试用多种编码解析二进制数据
        content = None
        encodings = ["utf-8", "gbk"]
        errors = []

        for encoding in encodings:
            try:
                content = raw_data.decode(encoding)
                logger.debug("Persona+ 使用 %s 编码成功解析文件", encoding)
                break
            except (UnicodeDecodeError, LookupError) as e:
                errors.append(f"{encoding}: {str(e)}")
                continue

        if content is None:
            error_detail = "; ".join(errors)
            raise ValueError(
                f"文件编码不支持（尝试了 UTF-8 和 GBK）。"
                f"文件已保存至 {save_path}，请检查文件编码。错误详情: {error_detail}"
            )

        return content.strip()

    async def _extract_persona_from_event(
        self, event: AstrMessageEvent, persona_id: str
    ) -> str:
        """从消息链中提取文本内容或文件内容。

        支持两种方式:
        1. 直接发送文本消息
        2. 发送文本文件 (推荐用于长人格)

        Args:
            event: 消息事件
            persona_id: 人格 ID，用于文件命名

        Returns:
            人格文本内容
        """
        # 先检查是否有文件组件
        has_file = self._has_component_of_types(event, (Comp.File,))

        # 如果有文件，优先使用文件
        if has_file:
            return await self._download_and_parse_persona_file(event, persona_id)

        # 否则使用文本内容
        text = event.message_str.strip()
        if text:
            return text

        raise ValueError("未检测到可解析的文本内容。请直接发送人格文本或上传文本文件。")

    def _schedule_persona_wait(
        self, event: AstrMessageEvent, persona_id: str, mode: str
    ) -> None:
        """统一的后续消息等待与处理逻辑调度器。

        支持的 mode: "create" | "update" | "avatar"。
        """

        # 定义每种模式的参数
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

        @session_waiter(timeout=self.manage_wait_timeout)
        async def waiter(
            controller: SessionController, next_event: AstrMessageEvent
        ) -> None:
            # 过滤掉空消息事件（如 QQ input_status 通知）
            if not next_event.message_str.strip() and not self._has_component_of_types(
                next_event, accepted
            ):
                logger.debug("Persona+ 已过滤空消息事件（可能是 input_status 通知）")
                controller.keep(timeout=self.manage_wait_timeout, reset_timeout=True)
                return

            try:
                if mode == "avatar":
                    await self.qq_sync.save_avatar_from_event(next_event, persona_id)
                    self.qq_sync.reset_persona_cache(persona_id)
                else:
                    raw_text = await self._extract_persona_from_event(
                        next_event, persona_id
                    )
                    system_prompt, begin_dialogs = self._parse_persona_payload(raw_text)
                    if mode == "create":
                        await self._create_persona(
                            persona_id=persona_id,
                            system_prompt=system_prompt,
                            begin_dialogs=begin_dialogs,
                            tools=None,
                        )
                    else:  # update
                        await self.persona_mgr.update_persona(
                            persona_id=persona_id,
                            system_prompt=system_prompt,
                            begin_dialogs=begin_dialogs if begin_dialogs else None,
                        )
            except ValueError as exc:
                await next_event.send(
                    next_event.plain_result(f"{action_zh}失败：{exc}")
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("%s人格时出现异常", action_zh)
                await next_event.send(
                    next_event.plain_result(f"{action_zh}失败：{exc}")
                )
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

        asyncio.create_task(run_wait())
        event.stop_event()
        return

    async def _create_persona(
        self,
        persona_id: str,
        system_prompt: str,
        begin_dialogs: list | None,
        tools: list | None = None,
    ):
        """创建新人格"""
        try:
            await self.persona_mgr.get_persona(persona_id)
            # 如果代码执行到这里，说明人格已存在
            raise ValueError(
                f"人格 {persona_id} 已存在，请使用 /persona_plus update {persona_id}。"
            )
        except ValueError:
            # 只有在 get_persona 抛出 ValueError (不存在) 时才创建
            await self.persona_mgr.create_persona(
                persona_id=persona_id,
                system_prompt=system_prompt,
                begin_dialogs=begin_dialogs if begin_dialogs else None,
                tools=tools,
            )
            logger.info("Persona+ 已创建人格 %s", persona_id)

    async def _switch_persona(
        self,
        event: AstrMessageEvent,
        persona_id: str,
        announce: str | None = None,
    ) -> MessageEventResult | None:
        """切换对话或配置中的默认人格。"""

        await self.persona_mgr.get_persona(persona_id)
        umo = event.unified_msg_origin
        scope = self.auto_switch_scope
        history_reset = [] if self.clear_context_on_switch else None

        # 根据范围应用默认人格设置
        self._set_default_persona(scope, umo, persona_id)

        # 始终尝试更新当前对话的人格（若有当前会话）
        await self._update_current_conversation(umo, persona_id, history_reset)

        # 记录日志
        if scope == "conversation":
            logger.info("Persona+ 已切换会话人格至 %s (scope=%s)", persona_id, scope)
        elif scope in {"session", "global"}:
            logger.info(
                "Persona+ 已更新%s配置默认人格至 %s (scope=%s)",
                "会话" if scope == "session" else "全局",
                persona_id,
                scope,
            )

        await self.qq_sync.maybe_sync_profile(event, persona_id)

        if announce:
            return event.plain_result(announce)
        return None

    def _set_default_persona(self, scope: str, umo: str, persona_id: str) -> None:
        """在指定作用域内设置默认人格。

        - conversation: 不修改配置，仅在当前会话生效（由上层逻辑处理）。
        - session: 修改当前会话配置默认人格。
        - global: 修改全局配置默认人格。
        """
        if scope == "session":
            config = self.context.astrbot_config_mgr.get_conf(umo)
            if config:
                provider_settings = config.setdefault("provider_settings", {})
                provider_settings["default_personality"] = persona_id
                config.save_config()
        elif scope == "global":
            config = self.context.astrbot_config_mgr.default_conf
            provider_settings = config.setdefault("provider_settings", {})
            provider_settings["default_personality"] = persona_id
            config.save_config()

    async def _update_current_conversation(
        self,
        unified_msg_origin: str,
        persona_id: str,
        history: list[dict] | None,
    ) -> None:
        cid = await self.context.conversation_manager.get_curr_conversation_id(
            unified_msg_origin
        )
        if not cid:
            return
        await self.context.conversation_manager.update_conversation(
            unified_msg_origin=unified_msg_origin,
            conversation_id=cid,
            persona_id=persona_id,
            history=history,
        )

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_quick_switch_command(self, event: AstrMessageEvent):
        """支持 `/pp <persona_id>` 的快捷切换"""

        if not event.is_at_or_wake_command:
            return

        text = event.get_message_str().strip()
        if not text:
            return

        parts = text.split()
        if not parts:
            return

        cmd = parts[0].lower()
        aliases = {"pp", "persona_plus", "persona+"}
        if cmd not in aliases:
            return

        # 形如: pp <persona_id>
        if len(parts) != 2:
            return

        persona_id = parts[1].strip()
        if not persona_id:
            return

        # 如果是已定义的子命令，则忽略，交由指令组处理
        known_subcommands = {
            "help",
            "list",
            "view",
            "delete",
            "create",
            "avatar",
            "update",
        }
        if persona_id.lower() in known_subcommands:
            return

        # 验证权限与存在性
        # 快速切换使用默认权限要求（不在 admin_commands 中）
        has_perm, err_msg = self.check_permission(event, "switch")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        try:
            await self.persona_mgr.get_persona(persona_id)
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return

        announce = None
        if self.auto_switch_announce:
            announce = f"已切换人格为 {persona_id}"

        result = await self._switch_persona(
            event, persona_id=persona_id, announce=announce
        )
        if result is not None:
            yield result
            event.stop_event()

    # ==================== 指令：人格管理 ====================
    @filter.command_group("persona_plus", alias={"pp", "persona+"})
    def persona_plus(self):
        """Persona+ 插件命令入口。"""
        # 指令组不需要实现

    @persona_plus.command("help")
    async def cmd_help(self, event: AstrMessageEvent):
        """展示 Persona+ 指令列表。"""

        has_perm, err_msg = self.check_permission(event, "help")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        sections = [
            "Persona+ 扩展指令(/persona_plus /pp /persona+ 可用)：",
            "- /persona_plus 人格ID — 切换到指定人格",
            "- /persona_plus help — 查看帮助与配置说明",
            "- /persona_plus list — 列出所有人格",
            "- /persona_plus view <persona_id> — 查看人格详情",
            "- /persona_plus create <persona_id> — 创建新人格，随后发送文本内容或上传文本文件",
            "- /persona_plus update <persona_id> — 更新人格，随后发送文本内容或上传文本文件",
            "- /persona_plus avatar <persona_id> — 上传人格头像，随后发送图片",
            "- /persona_plus delete <persona_id> — 删除人格 (管理员)",
            "",
            "提示：创建/更新人格时，可以直接发送文本，或上传 .txt/.md 等文本文件。",
        ]
        yield event.plain_result("\n".join(sections))

    @persona_plus.command("list")
    async def cmd_list(self, event: AstrMessageEvent):
        """列出所有已注册人格。"""

        has_perm, err_msg = self.check_permission(event, "list")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        personas = await self.persona_mgr.get_all_personas()
        if not personas:
            yield event.plain_result("当前没有人格，请先在控制台或通过指令创建。")
            return

        lines = ["已载入人格："]
        for persona in personas:
            begin_cnt = len(persona.begin_dialogs or [])
            tool_cnt = len(persona.tools or []) if persona.tools is not None else "ALL"
            lines.append(
                f"- {persona.persona_id} | 预设对话: {begin_cnt} | 工具: {tool_cnt}"
            )
        yield event.plain_result("\n".join(lines))

    @persona_plus.command("view")
    async def cmd_view(self, event: AstrMessageEvent, persona_id: str):
        """查看指定人格详情。"""

        has_perm, err_msg = self.check_permission(event, "view")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        try:
            persona = await self.persona_mgr.get_persona(persona_id)
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return

        begin_dialogs = persona.begin_dialogs or []
        tools = persona.tools

        lines = [
            f"人格 {persona.persona_id}",
            "----------------",
            "System Prompt:",
            persona.system_prompt,
        ]

        if begin_dialogs:
            lines.append("\n预设对话：")
            for idx, dialog in enumerate(begin_dialogs, start=1):
                role = "用户" if idx % 2 == 1 else "助手"
                lines.append(f"[{role}] {dialog}")

        if tools is None:
            lines.append("\n工具：使用全部可用工具")
        elif len(tools) == 0:
            lines.append("\n工具：已禁用所有工具")
        else:
            lines.append("\n工具：" + ", ".join(tools))

        yield event.plain_result("\n".join(lines))

    @persona_plus.command("delete")
    async def cmd_delete(self, event: AstrMessageEvent, persona_id: str):
        """删除指定人格。"""

        has_perm, err_msg = self.check_permission(event, "delete")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        try:
            await self.persona_mgr.delete_persona(persona_id)
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return

        self.qq_sync.delete_avatar(persona_id)
        yield event.plain_result(f"人格 {persona_id} 已删除。")

    @persona_plus.command("create")
    async def cmd_create(self, event: AstrMessageEvent, persona_id: str):
        """从文本或文件创建新人格。"""

        has_perm, err_msg = self.check_permission(event, "create")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        try:
            await self.persona_mgr.get_persona(persona_id)
        except ValueError:
            pass
        else:
            yield event.plain_result(
                f"人格 {persona_id} 已存在，请使用 /persona_plus update {persona_id}。"
            )
            return

        yield event.plain_result("请发送人格内容(文本消息或文本文件)")
        self._schedule_persona_wait(event, persona_id, "create")
        return

    @persona_plus.command("avatar")
    async def cmd_avatar(self, event: AstrMessageEvent, persona_id: str):
        """上传或更新人格头像。"""

        has_perm, err_msg = self.check_permission(event, "avatar")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        try:
            await self.persona_mgr.get_persona(persona_id)
        except ValueError:
            yield event.plain_result(f"未找到人格 {persona_id}，请先创建该人格。")
            return

        yield event.plain_result("请发送人格头像图片")
        self._schedule_persona_wait(event, persona_id, "avatar")
        return

    @persona_plus.command("update")
    async def cmd_update(self, event: AstrMessageEvent, persona_id: str):
        """更新现有人格，使用下一条消息提供内容。"""

        has_perm, err_msg = self.check_permission(event, "update")
        if not has_perm:
            yield event.plain_result(err_msg)
            return

        try:
            await self.persona_mgr.get_persona(persona_id)
        except ValueError:
            yield event.plain_result(f"未找到人格 {persona_id}，请先创建该人格。")
            return

        yield event.plain_result("请发送新的人格内容(文本消息或文本文件)")
        self._schedule_persona_wait(event, persona_id, "update")
        return

    # ==================== 自动切换监听 ====================
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        text = event.get_message_str()
        if not text or not self.keyword_switch_enabled or not self.keyword_mappings:
            return

        for mapping in self.keyword_mappings:
            if mapping.matches(text):
                announce = None
                if mapping.reply_template:
                    announce = mapping.reply_template.format(
                        persona_id=mapping.persona_id
                    )
                elif self.auto_switch_announce:
                    announce = f"已切换人格为 {mapping.persona_id}"
                result = await self._switch_persona(
                    event,
                    persona_id=mapping.persona_id,
                    announce=announce,
                )
                if result is not None:
                    yield result
                break

    async def terminate(self):
        """插件卸载时的清理逻辑。"""

        logger.info("Persona+ 插件卸载，已清理状态。")
