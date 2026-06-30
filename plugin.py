from __future__ import annotations
import asyncio
import json
import os
import shutil
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Optional

import tomllib

from maibot_sdk import MaiBotPlugin, PluginConfigBase

from .command_parser import CommandParser, ParseResult
from .queue import ApplicationQueue
from .ws_client import NapCatWsClient


from .config_models import CommandsConfig, PluginSettings, AdminConfig, ReplyConfig, PluginSectionConfig


# ── Main plugin ────────────────────────────────────────────────


class AiChatQueryPlugin(MaiBotPlugin):
    config_model: ClassVar[type[PluginConfigBase] | None] = PluginSettings

    def __init__(self) -> None:
        super().__init__()
        self._ws_client: Optional[NapCatWsClient] = None
        self._command_parser: Optional[CommandParser] = None
        self._app_queue: ApplicationQueue = ApplicationQueue()
        self._self_id: str = ""
        self._msg_lock: asyncio.Lock = asyncio.Lock()

    async def on_load(self) -> None:
        settings = self._get_settings()
        if not settings.plugin.enabled:
            self.ctx.logger.info("AI 对话查询插件已禁用")
            return
        self._restore_queue()
        await self._init_bot_nickname()
        self._command_parser = CommandParser(settings.commands)
        self._ws_client = NapCatWsClient(
            host=settings.napcat.ws_host,
            port=settings.napcat.ws_port,
            token=settings.napcat.token,
            logger=self.ctx.logger,
            on_message=self._on_ws_message,
        )
        await self._ws_client.start()
        self.ctx.logger.info("AI 对话查询插件已加载")

    async def on_unload(self) -> None:
        if self._ws_client is not None:
            await self._ws_client.stop()
            self._ws_client = None
        self._persist_queue()
        self.ctx.logger.info("AI 对话查询插件已卸载")

    async def on_config_update(self, scope: str, config_data: Dict[str, Any], version: str) -> None:
        if scope != "self":
            return
        self.set_plugin_config(config_data)
        settings = self._get_settings()
        self._command_parser = CommandParser(settings.commands)
        self.ctx.logger.info("AI 对话查询插件配置已更新")

    # ── WS message handler ────────────────────────────────────

    async def _on_ws_message(self, payload: Dict[str, Any]) -> None:
        async with self._msg_lock:
            parser = self._command_parser
            if parser is None:
                return
            result = parser.parse(payload)
            if result is None:
                return
            try:
                await self._route_command(result)
            except Exception as exc:
                self.ctx.logger.error(f"处理命令时出错: {exc}")

    async def _route_command(self, r: ParseResult) -> None:
        if r.command_type == "query":
            await self._handle_query(r)
        elif r.command_type == "apply":
            await self._handle_apply(r)
        elif r.command_type in ("approve", "reject", "list_all"):
            await self._handle_admin_command(r)

    # ── T7: Query handler ────────────────────────────────────

    async def _handle_query(self, r: ParseResult) -> None:
        napcat_config = await self._read_napcat_config()
        if napcat_config is None:
            await self._reply(r, f"无法读取 {self._bot_name()} 对话配置，请稍后重试")
            return
        settings = self._get_settings()
        chat = napcat_config.get("chat", {})
        if r.message_type == "group":
            group_list = [str(g) for g in chat.get("group_list", [])]
            if r.group_id in group_list:
                await self._reply(r, f"本群已开启 {self._bot_name()} 对话")
            else:
                apply_cmd = self._get_cmd(settings.commands.apply_commands, "/开启ai聊天")
                await self._reply(r, f"本群未开启 {self._bot_name()} 对话\n可使用 {apply_cmd} 申请")
        else:
            private_list = [str(u) for u in chat.get("private_list", [])]
            if r.user_id in private_list:
                await self._reply(r, f"您已开启 {self._bot_name()} 对话")
            else:
                apply_cmd = self._get_cmd(settings.commands.apply_commands, "/开启ai聊天")
                await self._reply(r, f"您未开启 {self._bot_name()} 对话\n可使用 {apply_cmd} 申请")

    # ── T8: Application handler ──────────────────────────────

    async def _handle_apply(self, r: ParseResult) -> None:
        napcat_config = await self._read_napcat_config()
        if napcat_config:
            chat = napcat_config.get("chat", {})
            if r.message_type == "group":
                group_list = [str(g) for g in chat.get("group_list", [])]
                if r.group_id in group_list:
                    await self._reply(r, f"本群已开启 {self._bot_name()} 对话，无需重复申请")
                    return
            else:
                private_list = [str(u) for u in chat.get("private_list", [])]
                if r.user_id in private_list:
                    await self._reply(r, f"您已开启 {self._bot_name()} 对话，无需重复申请")
                    return
        group_for_check = r.group_id if r.message_type == "group" else None
        app_id = self._app_queue.add(group_for_check, r.user_id)
        if app_id is None:
            await self._reply(r, "您已提交过申请，请耐心等待管理员审核")
            return
        await self._reply(
            r, f"申请已提交，申请编号 #{app_id}，请等待管理员审核"
        )
        settings = self._get_settings()
        admin_qq = settings.admin.super_admin_qq
        if admin_qq:
            if r.message_type == "group":
                detail = f"群号 {r.group_id}（申请人 QQ: {r.user_id}）"
            else:
                detail = f"用户 QQ {r.user_id}"
            cmds = settings.commands
            approve_cmd = self._get_cmd(cmds.approve_commands, "/通过申请")
            reject_cmd = self._get_cmd(cmds.reject_commands, "/拒绝申请")
            admin_msg = (
                f"新申请 #{app_id}：{detail} 申请开启 {self._bot_name()} 对话\n"
                f"使用 {approve_cmd} {app_id} 或 {reject_cmd} {app_id} 处理"
            )
            await self._send_private_msg(admin_qq, admin_msg)

    # ── T9: Admin approval handler ──────────────────────────

    async def _handle_admin_command(self, r: ParseResult) -> None:
        settings = self._get_settings()
        if r.user_id != settings.admin.super_admin_qq:
            await self._reply(r, "无权限")
            return
        if r.message_type != "private":
            await self._reply(r, "审批仅限私聊操作")
            return
        parser = self._command_parser
        if parser is None:
            return
        matched_cmd = self._find_matched_command(r.raw_message, r.command_type, settings.commands)
        arg = CommandParser.extract_arg(r.raw_message, matched_cmd) if matched_cmd else ""

        if r.command_type == "approve":
            await self._handle_approve(r, arg)
        elif r.command_type == "reject":
            await self._handle_reject(r, arg)
        elif r.command_type == "list_all":
            await self._handle_list_all(r)

    def _find_matched_command(self, text: str, cmd_type: str, cfg: CommandsConfig) -> str:
        cmd_lists = {
            "approve": cfg.approve_commands,
            "reject": cfg.reject_commands,
            "list_all": cfg.list_all_commands,
        }
        for cmd in cmd_lists.get(cmd_type, []):
            enable_slash = cfg.enable_slash_prefix
            if enable_slash:
                if not cmd.startswith("/"):
                    cmd = "/" + cmd
            if text == cmd or text.startswith(cmd + " "):
                return cmd
        return ""

    async def _handle_approve(self, r: ParseResult, arg: str) -> None:
        app_id = self._parse_app_id_arg(arg)
        if app_id is not None:
            app = self._app_queue.get_by_id(app_id)
        else:
            app = self._app_queue.get_first_pending()
            app_id = app["id"] if app else None
        if app is None or not self._app_queue.approve(app_id):
            await self._reply(r, "未找到对应的待审批申请")
            return
        success = await self._write_napcat_whitelist(app)
        if success:
            await self._reply(r, f"申请 #{app_id} 已通过")
            await self._send_private_msg(
                app["applicant_qq"],
                f"您的 {self._bot_name()} 对话申请 #{app_id} 已通过",
            )
            if app.get("group_id"):
                await self._send_group_msg(
                    app["group_id"],
                    f"本群 {self._bot_name()} 对话已开启",
                )
        else:
            await self._reply(r, f"申请 #{app_id} 通过失败，配置写入异常，已回滚")

    async def _handle_reject(self, r: ParseResult, arg: str) -> None:
        parts = arg.strip().split(None, 1)
        reason = parts[1].strip() if len(parts) > 1 else ""
        app_id = self._parse_app_id_arg(parts[0]) if parts else None
        if app_id is not None:
            app = self._app_queue.get_by_id(app_id)
        else:
            app = self._app_queue.get_first_pending()
            app_id = app["id"] if app else None
        if app is None or not self._app_queue.reject(app_id):
            await self._reply(r, "未找到对应的待审批申请")
            return
        reply_text = f"申请 #{app_id} 已拒绝"
        if reason:
            reply_text += f"，原因：{reason}"
        await self._reply(r, reply_text)
        notify_text = f"您的 {self._bot_name()} 对话申请 #{app_id} 已被拒绝"
        if reason:
            notify_text += f"，原因：{reason}"
        await self._send_private_msg(
            app["applicant_qq"],
            notify_text,
        )
        if app.get("group_id"):
            group_text = f"本群 {self._bot_name()} 对话申请 #{app_id} 已被拒绝"
            if reason:
                group_text += f"，原因：{reason}"
            await self._send_group_msg(app["group_id"], group_text)

    async def _handle_list_all(self, r: ParseResult) -> None:
        pending = self._app_queue.get_pending()
        if not pending:
            await self._reply(r, "当前没有待审批的申请")
            return
        lines = ["待审批申请列表："]
        for app in pending:
            detail = app.get("group_id") or f"用户 {app['applicant_qq']}"
            lines.append(f"#{app['id']} {detail} ({app['timestamp'][:19]})")
        await self._reply(r, "\n".join(lines))

    @staticmethod
    def _parse_app_id_arg(arg: str) -> Optional[int]:
        arg = arg.strip()
        if not arg:
            return None
        try:
            return int(arg)
        except ValueError:
            return None

    async def _write_napcat_whitelist(self, app: Dict[str, Any]) -> bool:
        settings = self._get_settings()
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = settings.resolve_napcat_config_path(plugin_dir)
        if not os.path.isfile(config_path):
            self.ctx.logger.error(f"napcat 配置文件不存在: {config_path}")
            return False
        backup_path = config_path + f".bak.{datetime.now().strftime('%Y%m%d%H%M%S')}"
        try:
            shutil.copy2(config_path, backup_path)
        except OSError as exc:
            self.ctx.logger.error(f"备份 napcat 配置文件失败: {exc}")
            return False
        try:
            with open(config_path, "rb") as f:
                config_data = tomllib.load(f)
        except Exception as exc:
            self.ctx.logger.error(f"读取 napcat 配置文件失败: {exc}")
            return False
        chat = config_data.setdefault("chat", {})
        group_id = app.get("group_id")
        qq = app["applicant_qq"]
        if group_id:
            group_list = chat.setdefault("group_list", [])
            id_str = str(group_id)
            if id_str not in [str(x) for x in group_list]:
                group_list.append(id_str)
        else:
            private_list = chat.setdefault("private_list", [])
            if qq not in [str(x) for x in private_list]:
                private_list.append(qq)
        try:
            import toml
            raw = toml.dumps(config_data)
        except ImportError:
            raw = self._toml_dumps_simple(config_data)
        try:
            tomllib.loads(raw)
        except Exception as exc:
            self.ctx.logger.error(f"新配置 TOML 格式异常，从备份恢复: {exc}")
            try:
                shutil.copy2(backup_path, config_path)
            except OSError:
                self.ctx.logger.error("从备份恢复失败，配置文件可能已损坏")
            return False
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(raw)
        except OSError as exc:
            self.ctx.logger.error(f"写入 napcat 配置文件失败: {exc}，从备份恢复")
            try:
                shutil.copy2(backup_path, config_path)
            except OSError:
                self.ctx.logger.error("从备份恢复失败")
            return False
        return True

    @staticmethod
    def _toml_dumps_simple(data: dict) -> str:
        lines: List[str] = []
        for section, values in data.items():
            if isinstance(values, dict):
                lines.append(f"[{section}]")
                for k, v in values.items():
                    if isinstance(v, list):
                        items = ", ".join(json.dumps(str(x), ensure_ascii=False) for x in v)
                        lines.append(f"{k} = [{items}]")
                    elif isinstance(v, bool):
                        lines.append(f"{k} = {'true' if v else 'false'}")
                    elif isinstance(v, (int, float)):
                        lines.append(f"{k} = {v}")
                    else:
                        lines.append(f"{k} = {json.dumps(str(v), ensure_ascii=False)}")
                lines.append("")
            else:
                if isinstance(values, bool):
                    lines.append(f"{section} = {'true' if values else 'false'}")
                elif isinstance(values, (int, float)):
                    lines.append(f"{section} = {values}")
                else:
                    lines.append(f"{section} = {json.dumps(str(values), ensure_ascii=False)}")
        return "\n".join(lines)

    # ── Queue persistence (T10) ──────────────────────────────

    def _restore_queue(self) -> None:
        try:
            data_dir: str = str(self.ctx.paths.data_dir)
        except Exception:
            return
        queue_path = os.path.join(data_dir, "ai_chat_query_applications.json")
        if not os.path.isfile(queue_path):
            return
        try:
            with open(queue_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._app_queue = ApplicationQueue.from_dict(data)
            self.ctx.logger.info(f"已恢复申请队列 ({len(data.get('applications', []))} 条)")
        except Exception as exc:
            self.ctx.logger.warning(f"恢复申请队列失败: {exc}")

    def _persist_queue(self) -> None:
        try:
            data_dir: str = str(self.ctx.paths.data_dir)
        except Exception:
            return
        os.makedirs(data_dir, exist_ok=True)
        queue_path = os.path.join(data_dir, "ai_chat_query_applications.json")
        try:
            with open(queue_path, "w", encoding="utf-8") as f:
                json.dump(self._app_queue.to_dict(), f, ensure_ascii=False, indent=2)
            self.ctx.logger.info("申请队列已保存")
        except Exception as exc:
            self.ctx.logger.error(f"保存申请队列失败: {exc}")

    # ── Helpers ──────────────────────────────────────────────

    async def _init_bot_nickname(self) -> None:
        settings = self._get_settings()
        if settings.reply.bot_nickname:
            return
        if self._ws_client is None:
            return
        try:
            resp = await self._ws_client.send_action("get_login_info", {})
            data = resp.get("data", {})
            self._self_id = str(data.get("user_id", ""))
            nickname = str(data.get("nickname", ""))
            settings.reply.bot_nickname = nickname
            self.ctx.logger.info(f"已获取机器人信息: {nickname} (QQ: {self._self_id})")
        except Exception as exc:
            self.ctx.logger.warning(f"获取机器人信息失败: {exc}")

    async def _read_napcat_config(self) -> Optional[Dict[str, Any]]:
        try:
            napcat_config = await self.ctx.config.get_plugin("maibot-team.napcat-adapter")
            if napcat_config is not None:
                return napcat_config
        except Exception:
            pass
        settings = self._get_settings()
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = settings.resolve_napcat_config_path(plugin_dir)
        if not os.path.isfile(config_path):
            return None
        try:
            with open(config_path, "rb") as f:
                return tomllib.load(f)
        except Exception:
            return None

    async def _reply(self, r: ParseResult, text: str) -> None:
        ws = self._ws_client
        if ws is None:
            return
        if r.message_type == "group":
            await ws.send_action("send_group_msg", {
                "group_id": int(r.group_id) if r.group_id else 0,
                "message": text,
            })
        else:
            await ws.send_action("send_private_msg", {
                "user_id": int(r.user_id),
                "message": text,
            })

    async def _send_private_msg(self, user_id: str, text: str) -> None:
        ws = self._ws_client
        if ws is None:
            return
        try:
            await ws.send_action("send_private_msg", {
                "user_id": int(user_id),
                "message": text,
            })
        except Exception as exc:
            self.ctx.logger.warning(f"发送私聊消息失败: {exc}")

    async def _send_group_msg(self, group_id: str, text: str) -> None:
        ws = self._ws_client
        if ws is None:
            return
        try:
            await ws.send_action("send_group_msg", {
                "group_id": int(group_id),
                "message": text,
            })
        except Exception as exc:
            self.ctx.logger.warning(f"发送群消息失败: {exc}")

    def _get_settings(self) -> PluginSettings:
        return PluginSettings.model_validate(self.get_plugin_config_data())

    def _bot_name(self) -> str:
        return self._get_settings().reply.bot_nickname or "AI"

    @staticmethod
    def _get_cmd(cmd_list: List[str], fallback: str) -> str:
        return cmd_list[0] if cmd_list else fallback


def create_plugin() -> AiChatQueryPlugin:
    return AiChatQueryPlugin()
