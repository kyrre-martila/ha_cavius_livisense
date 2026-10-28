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


def _valid_application_message(frame: dict) -> bool:
    """Accept documented application frames without assuming a shared data shape."""
    name = frame["name"]
    if name in ("ping", "pong"):
        return False
    if name == "user:loggedOut":
        return "data" not in frame or frame["data"] is None
    data = frame.get("data")
    if not isinstance(data, dict):
        return False
    hub = data.get("hub")
    if name in ("hub:state", "hub:updated", "device:state", "device:updated", "hub:event"):
        if not isinstance(hub, dict) or not isinstance(hub.get("id"), str):
            return False
        if name in ("device:state", "device:updated"):
            device = data.get("device")
            if not isinstance(device, dict) or not isinstance(device.get("id"), str):
                return False
        if name == "hub:state" and not isinstance(hub.get("state"), dict):
            return False
        if name == "device:state" and not isinstance(data.get("state"), dict):
            return False
        if name == "hub:event" and not isinstance(data.get("event"), dict):
            return False
    return True


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
        self.coordinator.set_websocket_connected(False)

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
                    self.coordinator.request_alarm_reconciliation()
                    await self.coordinator.async_request_refresh()
                    keepalive = asyncio.create_task(self._keepalive(ws))
                    try:
                        if not ws.closed:
                            self.coordinator.set_websocket_connected(True)
                            _LOGGER.debug("LiviSense WebSocket connection established")
                        async for message in ws:
                            if message.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    frame = json.loads(message.data)
                                except (TypeError, ValueError):
                                    continue
                                if not isinstance(frame, dict) or not isinstance(frame.get("name"), str):
                                    continue
                                name = frame["name"]
                                if not _valid_application_message(frame):
                                    continue
                                self.coordinator.record_websocket_message()
                                _LOGGER.debug("LiviSense WebSocket message type: %s", name if name in ("hub:state", "device:state", "hub:updated", "device:updated", "hub:event", "user:loggedOut") else "other")
                                if name == "user:loggedOut":
                                    self.client.invalidate_token()
                                    break
                                await self.coordinator.async_apply_message(name, frame.get("data"))
                            elif message.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE):
                                break
                    finally:
                        self.coordinator.set_websocket_connected(False)
                        _LOGGER.debug("LiviSense WebSocket disconnected")
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
                self.coordinator.set_websocket_connected(False)
                _LOGGER.warning("LiviSense authentication failed; requesting reauthentication")
                if self.entry is not None:
                    self.entry.async_start_reauth(self.coordinator.hass)
                return
            except (aiohttp.ClientError, asyncio.TimeoutError):
                _LOGGER.warning("LiviSense WebSocket connection failed")
            except Exception:
                _LOGGER.warning("LiviSense WebSocket listener failed")
            self.coordinator.set_websocket_connected(False)
            _LOGGER.debug("LiviSense WebSocket reconnect scheduled in %s seconds", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)
