from __future__ import annotations

import asyncio
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.persona_mgr import PersonaManager
from astrbot.core.star.star_tools import StarTools

from .core.config import PersonaPlusSettings, load_settings
from .core.keyword_switch import match_keyword
from .core.permissions import check_permission
from .core.session_flows import schedule_persona_wait
from .core.switching import switch_persona
from .integrations.qq_profile_sync import QQProfileSync


class PersonaPlus(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.context: Context = context
        self.config: AstrBotConfig | None = config
        self.persona_mgr: PersonaManager = context.persona_manager

        self.settings: PersonaPlusSettings
        self.keyword_mappings = []
        self.auto_switch_scope = "conversation"
        self.keyword_switch_enabled = True
        self.manage_wait_timeout = 60
        self.admin_commands: set[str] = set()
        self.auto_switch_announce = True
        self.clear_context_on_switch = False

        self.qq_sync = QQProfileSync(context)

        self._tasks: set[asyncio.Task] = set()

        # 初始化人格数据目录
        self.persona_data_dir: Path = (
            StarTools.get_data_dir("astrbot_plugin_persona_plus") / "persona_files"
        )
        self.persona_data_dir.mkdir(parents=True, exist_ok=True)
        self._load_config()

    def _load_config(self) -> None:
        self.settings = load_settings(self.config)

        self.keyword_mappings = self.settings.keyword_mappings
        self.auto_switch_scope = self.settings.auto_switch_scope
        self.keyword_switch_enabled = self.settings.keyword_switch_enabled
        self.manage_wait_timeout = self.settings.manage_wait_timeout
        self.admin_commands = self.settings.admin_commands
        self.auto_switch_announce = self.settings.auto_switch_announce
        self.clear_context_on_switch = self.settings.clear_context_on_switch

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
        return check_permission(
            context=self.context,
            event=event,
            command=command,
            admin_commands=self.admin_commands,
        )

    @staticmethod
    def _parse_persona_payload(raw_text: str) -> tuple[str, list]:
        """将用户传入的全部文本作为 system_prompt"""
        return raw_text, []

    def _schedule_persona_wait(
        self, event: AstrMessageEvent, persona_id: str, mode: str
    ) -> None:
        def register_task(task: asyncio.Task) -> None:
            self._tasks.add(task)
            task.add_done_callback(lambda t: self._tasks.discard(t))

        async def create_persona(
            persona_id_: str,
            system_prompt: str,
            begin_dialogs: list | None,
            tools: list | None,
        ) -> None:
            await self._create_persona(
                persona_id=persona_id_,
                system_prompt=system_prompt,
                begin_dialogs=begin_dialogs,
                tools=tools,
            )

        async def update_persona(
            persona_id_: str,
            system_prompt: str,
            begin_dialogs: list | None,
        ) -> None:
            await self.persona_mgr.update_persona(
                persona_id=persona_id_,
                system_prompt=system_prompt,
                begin_dialogs=begin_dialogs if begin_dialogs else None,
            )

        schedule_persona_wait(
            event=event,
            persona_id=persona_id,
            mode=mode,
            manage_wait_timeout=self.manage_wait_timeout,
            persona_data_dir=self.persona_data_dir,
            qq_sync=self.qq_sync,
            create_persona=create_persona,
            update_persona=update_persona,
            register_task=register_task,
        )
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
        except ValueError:
            await self.persona_mgr.create_persona(
                persona_id=persona_id,
                system_prompt=system_prompt,
                begin_dialogs=begin_dialogs if begin_dialogs else None,
                tools=tools,
            )
            logger.info("Persona+ 已创建人格 %s", persona_id)
        else:
            raise ValueError(
                f"人格 {persona_id} 已存在，请使用 /persona_plus update {persona_id}。"
            )

    async def _switch_persona(
        self,
        event: AstrMessageEvent,
        persona_id: str,
        announce: str | None = None,
    ) -> MessageEventResult | None:
        """切换对话或配置中的默认人格。"""

        return await switch_persona(
            context=self.context,
            persona_mgr=self.persona_mgr,
            qq_sync=self.qq_sync,
            event=event,
            persona_id=persona_id,
            scope=self.auto_switch_scope,
            clear_context_on_switch=self.clear_context_on_switch,
            announce=announce,
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

        mapping = match_keyword(self.keyword_mappings, text)
        if not mapping:
            return

        announce = None
        if mapping.reply_template:
            announce = mapping.reply_template.format(persona_id=mapping.persona_id)
        elif self.auto_switch_announce:
            announce = f"已切换人格为 {mapping.persona_id}"

        result = await self._switch_persona(
            event,
            persona_id=mapping.persona_id,
            announce=announce,
        )
        if result is not None:
            yield result

    async def terminate(self):
        """插件卸载时的清理逻辑。"""

        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()
        self.qq_sync.clear_cache()

        logger.info("Persona+ 插件卸载，已清理状态。")
