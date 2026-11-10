"""WebSocket pub/sub: thread-safe publish from engine workers, async fan-out to clients."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from fastapi import WebSocket


class Hub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.history: list[dict[str, Any]] = []

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def _broadcast(self, msg: dict[str, Any]) -> None:
        text = json.dumps(msg, default=str)
        for ws in list(self.clients):
            try:
                await ws.send_text(text)
            except Exception:
                self.clients.discard(ws)

    def publish(self, topic: str, data: Any) -> None:
        """Safe to call from any thread."""
        msg = {"topic": topic, "data": data}
        self.history = [*self.history[-199:], msg]
        if self.loop is None or self.loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            self.loop.create_task(self._broadcast(msg))
        else:
            with contextlib.suppress(RuntimeError):
                asyncio.run_coroutine_threadsafe(self._broadcast(msg), self.loop)
