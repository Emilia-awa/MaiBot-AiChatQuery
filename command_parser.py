from __future__ import annotations

from typing import Any, Dict, Optional

from .config_models import CommandsConfig


class ParseResult:
    def __init__(
        self,
        command_type: str,
        raw_message: str,
        user_id: str,
        group_id: Optional[str],
        message_type: str,
    ) -> None:
        self.command_type = command_type
        self.raw_message = raw_message
        self.user_id = user_id
        self.group_id = group_id
        self.message_type = message_type


class CommandParser:
    def __init__(self, commands_cfg: CommandsConfig) -> None:
        self._cfg = commands_cfg

    def parse(self, payload: Dict[str, Any]) -> Optional[ParseResult]:
        if payload.get("post_type") != "message":
            return None
        message_type = payload.get("message_type", "")
        raw_message = str(payload.get("raw_message", "")).strip()
        if not raw_message:
            return None
        user_id = str(payload.get("user_id", ""))
        group_id = str(payload.get("group_id", "")) if payload.get("group_id") else None
        command_type = self._match_command(raw_message)
        if command_type is None:
            return None
        return ParseResult(
            command_type=command_type,
            raw_message=raw_message,
            user_id=user_id,
            group_id=group_id,
            message_type=message_type,
        )

    def _match_command(self, text: str) -> Optional[str]:
        enable_slash = self._cfg.enable_slash_prefix
        for cmd in self._cfg.query_commands:
            if self._text_matches(text, cmd, enable_slash):
                return "query"
        for cmd in self._cfg.apply_commands:
            if self._text_matches(text, cmd, enable_slash):
                return "apply"
        for cmd in self._cfg.approve_commands:
            if self._text_matches(text, cmd, enable_slash):
                return "approve"
        for cmd in self._cfg.reject_commands:
            if self._text_matches(text, cmd, enable_slash):
                return "reject"
        for cmd in self._cfg.list_all_commands:
            if self._text_matches(text, cmd, enable_slash):
                return "list_all"
        return None

    @staticmethod
    def _text_matches(text: str, command: str, enable_slash: bool) -> bool:
        if enable_slash:
            if not command.startswith("/"):
                command = "/" + command
            return text == command or text.startswith(command + " ")
        return text == command or text.startswith(command + " ")

    @staticmethod
    def extract_arg(text: str, command: str) -> str:
        for candidate in (command, "/" + command):
            if text.startswith(candidate + " "):
                return text[len(candidate) + 1:].strip()
            if text == candidate:
                return ""
        return ""
