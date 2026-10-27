"""Realtime LiviSense state synchronization."""
from __future__ import annotations

import asyncio
import json
import logging

import aiohttp
from homeassistant.config_entries import ConfigEntry

from .api import AuthError, LiviSenseClient
from .coordinator import LiviSenseCoordinator

_LOGGER = logging.getLogger(__name__)
WS_URL = "wss://app.cavius.livi.cloud/ws"


class LiviSenseWebSocket:
    """One reconnecting listener for a configured account."""

    def __init__(self, client: LiviSenseClient, coordinator: LiviSenseCoordinator, entry: ConfigEntry | None = None) -> None:
        self.client = client
        self.coordinator = coordinator
        self.entry = entry
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _keepalive(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        while not ws.closed:
            await asyncio.sleep(30)
            if not ws.closed:
                await ws.send_json({"ping": 1})

    async def _run(self) -> None:
        delay = 5
        while True:
            try:
                token = await self.client.access_token()
                # Never include the authenticated URL in exception messages or logs.
                async with self.client.session.ws_connect(
                    WS_URL, params={"token": token}, heartbeat=None
                ) as ws:
                    delay = 5
                    await self.coordinator.async_request_refresh()
                    keepalive = asyncio.create_task(self._keepalive(ws))
                    try:
                        async for message in ws:
                            if message.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    frame = json.loads(message.data)
                                except (TypeError, ValueError):
                                    continue
                                if not isinstance(frame, dict) or not isinstance(frame.get("name"), str):
                                    continue
                                name = frame["name"]
                                if name == "user:loggedOut":
                                    self.client.invalidate_token()
                                    break
                                await self.coordinator.async_apply_message(name, frame.get("data"))
                            elif message.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE):
                                break
                    finally:
                        keepalive.cancel()
                        try:
                            await keepalive
                        except asyncio.CancelledError:
                            pass
            except asyncio.CancelledError:
                raise
            except aiohttp.WSServerHandshakeError as exc:
                if exc.status in (401, 403):
                    self.client.invalidate_token()
                _LOGGER.warning("LiviSense WebSocket handshake failed (HTTP %s)", exc.status)
            except AuthError:
                _LOGGER.warning("LiviSense authentication failed; requesting reauthentication")
                if self.entry is not None:
                    self.entry.async_start_reauth(self.coordinator.hass)
                return
            except (aiohttp.ClientError, asyncio.TimeoutError):
                _LOGGER.warning("LiviSense WebSocket connection failed")
            except Exception:
                _LOGGER.warning("LiviSense WebSocket listener failed")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)
