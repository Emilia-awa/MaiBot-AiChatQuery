from __future__ import annotations

import os
from typing import List

from maibot_sdk import PluginConfigBase, Field


# ── Config models ──────────────────────────────────────────────


class NapCatConfig(PluginConfigBase):
    __ui_label__ = "NapCat 连接"
    ws_host: str = Field(default="127.0.0.1", description="NapCat 端口 B 的 WebSocket 地址")
    ws_port: int = Field(default=3002, description="NapCat 端口 B 的端口号")
    token: str = Field(default="", description="NapCat 鉴权 Token，留空则不鉴权")


class CommandsConfig(PluginConfigBase):
    __ui_label__ = "命令设置"
    query_commands: List[str] = Field(default_factory=lambda: ["/查询ai聊天"])
    apply_commands: List[str] = Field(default_factory=lambda: ["/开启ai聊天"])
    approve_commands: List[str] = Field(default_factory=lambda: ["/通过申请"])
    reject_commands: List[str] = Field(default_factory=lambda: ["/拒绝申请"])
    list_all_commands: List[str] = Field(default_factory=lambda: ["/查申请"])
    enable_slash_prefix: bool = Field(default=True)


class AdminConfig(PluginConfigBase):
    __ui_label__ = "管理员设置"
    super_admin_qq: str = Field(default="", description="超级管理员 QQ 号")
    napcat_config_path: str = Field(
        default="",
        description="napcat 适配器 config.toml 路径，留空自动检测",
    )


class ReplyConfig(PluginConfigBase):
    __ui_label__ = "回复设置"
    bot_nickname: str = Field(default="", description="机器人昵称，留空自动获取")


class PluginSectionConfig(PluginConfigBase):
    __ui_label__ = "插件设置"
    enabled: bool = Field(default=True)
    config_version: str = Field(default="1.0.0")


class PluginSettings(PluginConfigBase):
    __ui_label__ = "AI 对话查询插件配置"
    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    napcat: NapCatConfig = Field(default_factory=NapCatConfig)
    commands: CommandsConfig = Field(default_factory=CommandsConfig)
    admin: AdminConfig = Field(default_factory=AdminConfig)
    reply: ReplyConfig = Field(default_factory=ReplyConfig)

    def resolve_napcat_config_path(self, plugin_dir: str) -> str:
        if self.admin.napcat_config_path:
            candidate = self.admin.napcat_config_path
            if not os.path.isabs(candidate):
                candidate = os.path.normpath(os.path.join(plugin_dir, candidate))
            return candidate
        return os.path.normpath(
            os.path.join(plugin_dir, "..", "maibot-team_napcat-adapter", "config.toml")
        )
