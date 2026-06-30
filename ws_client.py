from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Callable, Coroutine, Dict, Optional


class NapCatWsClient:
    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        logger: Any,
        on_message: Callable[[Dict[str, Any]], Coroutine[Any, Any, None]],
    ) -> None:
        self._host = host
        self._port = port
        self._token = token
        self._logger = logger
        self._on_message = on_message
        self._ws_url = f"ws://{host}:{port}"
        self._session: Any = None
        self._ws: Any = None
        self._connection_task: Optional[asyncio.Task[None]] = None
        self._stop_requested: bool = False
        self._pending_actions: Dict[str, asyncio.Future[Dict[str, Any]]] = {}

    async def start(self) -> None:
        self._stop_requested = False
        self._connection_task = asyncio.create_task(
            self._connection_loop(), name="ai_chat_query.ws"
        )

    async def stop(self) -> None:
        self._stop_requested = True
        task = self._connection_task
        self._connection_task = None
        if self._ws is not None and not self._ws.closed:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._session is not None and not self._session.closed:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._fail_pending_actions("连接已关闭")

    async def send_action(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        ws = self._ws
        if ws is None or ws.closed:
            raise RuntimeError("NapCat 未连接")
        echo_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Dict[str, Any]] = loop.create_future()
        self._pending_actions[echo_id] = future
        payload = json.dumps(
            {"action": action, "params": params, "echo": echo_id},
            ensure_ascii=False,
        )
        try:
            await ws.send_str(payload)
            return await asyncio.wait_for(future, timeout=15.0)
        finally:
            self._pending_actions.pop(echo_id, None)

    async def send_str(self, data: str) -> None:
        ws = self._ws
        if ws is None or ws.closed:
            raise RuntimeError("NapCat 未连接")
        await ws.send_str(data)

    async def _connection_loop(self) -> None:
        import aiohttp

        reconnect_delay = 5.0
        headers = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        while not self._stop_requested:
            try:
                self._session = aiohttp.ClientSession(headers=headers)
                async with self._session.ws_connect(
                    self._ws_url, heartbeat=30.0
                ) as ws:
                    self._ws = ws
                    self._logger.info(f"AI 对话查询插件已连接 NapCat: {self._ws_url}")
                    await self._receive_loop(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.warning(
                    f"AI 对话查询插件连接 NapCat 失败: {exc}，{reconnect_delay} 秒后重试"
                )
            finally:
                self._ws = None
                if self._session is not None and not self._session.closed:
                    try:
                        await self._session.close()
                    except Exception:
                        pass
                    self._session = None
            if self._stop_requested:
                break
            await asyncio.sleep(reconnect_delay)

    async def _receive_loop(self, ws: Any) -> None:
        from aiohttp import WSMsgType

        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                    break
                continue
            try:
                payload = json.loads(msg.data)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            echo_id = str(payload.get("echo") or "").strip()
            if echo_id and echo_id in self._pending_actions:
                future = self._pending_actions.pop(echo_id)
                if not future.done():
                    future.set_result(payload)
                continue
            asyncio.create_task(self._on_message(payload))

    def _fail_pending_actions(self, reason: str) -> None:
        for echo_id, future in list(self._pending_actions.items()):
            if not future.done():
                future.set_exception(RuntimeError(reason))
        self._pending_actions.clear()
