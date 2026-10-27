"""Periodic cloud state and discovery."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ApiError, AuthError, LiviSenseClient
from .const import DOMAIN, UPDATE_INTERVAL


def _unwrap(response: dict[str, Any]) -> dict[str, Any]:
    data = response.get("data", response)
    if not isinstance(data, dict):
        raise ApiError("Expected object data")
    return data


class LiviSenseCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Serialize REST snapshots and WebSocket state updates."""

    def __init__(self, hass: HomeAssistant, client: LiviSenseClient) -> None:
        super().__init__(hass, logger=__import__("logging").getLogger(__name__), name=DOMAIN, update_interval=UPDATE_INTERVAL)
        self.client = client
        self._state_lock = asyncio.Lock()
        self.events: list[dict[str, Any]] = []
        self.flags: dict[str, dict[str, bool]] = {}
        self.alarm_events: dict[str, list[dict[str, Any]]] = {}
        self._cleared_alarms: set[str] = set()
        self._last_alarm_fetch: datetime | None = None
        self.websocket_connected: bool | None = None
        self.last_websocket_message: datetime | None = None

    def set_websocket_connected(self, connected: bool) -> None:
        if self.websocket_connected != connected:
            self.websocket_connected = connected
            self.async_update_listeners()

    def record_websocket_message(self) -> None:
        self.last_websocket_message = datetime.now(timezone.utc)
        self.async_update_listeners()

    async def async_apply_message(self, name: str, payload: Any) -> None:
        """Merge only documented state fields; refresh ambiguous updates."""
        if not isinstance(payload, dict):
            return
        if name == "hub:event":
            self.events.append(payload.copy())
            self.events = self.events[-100:]
            await self._apply_event(payload)
            return
        if name in ("hub:updated", "device:updated"):
            await self.async_request_refresh()
            return
        if name not in ("hub:state", "device:state"):
            return
        hub_ref = payload.get("hub")
        if not isinstance(hub_ref, dict) or not isinstance(hub_ref.get("id"), str):
            return
        fields = ("alarm", "status", "mode", "updatedAt", "lastAlarmAt", "lastEventAt") if name == "hub:state" else ("alarm", "warning", "batteryLevel", "signalStrength", "updatedAt", "lastEventAt")
        source = hub_ref.get("state") if name == "hub:state" else payload.get("state")
        if not isinstance(source, dict):
            return
        changes = {key: source[key] for key in fields if key in source}
        if not changes:
            return
        refresh = False
        async with self._state_lock:
            item = (self.data or {}).get(hub_ref["id"])
            if not item:
                return
            target = item["hub"]
            if name == "device:state":
                ref = payload.get("device")
                if not isinstance(ref, dict) or not isinstance(ref.get("id"), str):
                    return
                target = next((device for device in target.get("devices", []) if isinstance(device, dict) and device.get("id") == ref["id"]), None)
                if target is None:
                    refresh = True
            if not refresh:
                state = target.get("state")
                if not isinstance(state, dict):
                    state = target["state"] = {}
                state.update(changes)
                # Keep the scheduled REST fallback timer intact.
                self.async_update_listeners()
        if refresh:
            await self.async_request_refresh()

    def _detectors(self, hub_id: str) -> set[str]:
        hub = (self.data or {}).get(hub_id, {}).get("hub", {})
        return {device["id"] for device in hub.get("devices", []) if isinstance(device, dict) and device.get("model") == "SMOKECAVIUS" and isinstance(device.get("id"), str)}

    def _targets(self, hub_id: str, event: dict[str, Any]) -> set[str]:
        detectors = self._detectors(hub_id)
        for ref in (event.get("device"), event.get("subject")):
            if isinstance(ref, dict) and ref.get("id") in detectors:
                return {ref["id"]}
        return set()

    async def _apply_event(self, payload: dict[str, Any]) -> None:
        hub = payload.get("hub")
        event = payload.get("event")
        if not isinstance(hub, dict) or hub.get("id") not in (self.data or {}) or not isinstance(event, dict):
            return
        try:
            code = int(event.get("code"))
        except (TypeError, ValueError):
            return
        hub_id = hub["id"]
        async with self._state_lock:
            targets = self._targets(hub_id, payload)
            if code in (1110, 1111) and not targets:
                targets = self._detectors(hub_id)
            if code in (9421, 9422):
                targets = self._detectors(hub_id)
            if code in (1384, 3384, 1381, 3381, 1380, 1389, 1659, 1110, 1111, 9421, 9422):
                for device_id in targets:
                    flags = self.flags.setdefault(device_id, {})
                    if code in (1110, 1111):
                        flags["smoke"] = True
                        self._cleared_alarms.discard(device_id)
                    elif code in (1659, 9421, 9422):
                        flags["smoke"] = False
                        self._cleared_alarms.add(device_id)
                    elif code in (1384, 3384):
                        flags["battery"] = code == 1384
                    elif code in (1381, 3381):
                        flags["signal"] = code == 1381
                    elif code in (1380, 1389):
                        flags["trouble"] = True
                self.async_update_listeners()

    def alarm_state(self, hub_id: str, device_id: str) -> bool | None:
        item = (self.data or {}).get(hub_id)
        if not item:
            return None
        device = next((d for d in item["hub"].get("devices", []) if isinstance(d, dict) and d.get("id") == device_id), None)
        if device is None:
            return None
        state = device.get("state")
        flag = self.flags.get(device_id, {}).get("smoke")
        if flag is True:
            return True
        if flag is not False and isinstance(state, dict) and state.get("alarm") is not None:
            return True
        for event in self.alarm_events.get(hub_id, []):
            if flag is False and device_id in self._cleared_alarms:
                continue
            if self._targets(hub_id, event) == {device_id}:
                return True
            if not self._targets(hub_id, event):
                return True
        if device_id in self.flags and "smoke" in self.flags[device_id]:
            return False
        if isinstance(state, dict) and "alarm" in state and hub_id in self.alarm_events:
            return False
        return None

    @staticmethod
    def _event_code(event: dict[str, Any]) -> int | None:
        raw = event.get("event", event)
        try:
            return int(raw.get("code")) if isinstance(raw, dict) else None
        except (TypeError, ValueError):
            return None

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        # All HA refresh paths call this hook. Hold the same lock used by WS
        # merges until the snapshot is ready; HA assigns it without yielding.
        async with self._state_lock:
            snapshot = await self._fetch_snapshot()
            now = datetime.now(timezone.utc)
            if self._last_alarm_fetch is None or now - self._last_alarm_fetch >= timedelta(seconds=90):
                try:
                    alarms = {hub_id: await self.client.unacknowledged_alarms(hub_id) for hub_id in snapshot}
                except AuthError as exc:
                    raise ConfigEntryAuthFailed from exc
                except ApiError as exc:
                    raise UpdateFailed(str(exc)) from exc
                self.alarm_events = alarms
                for hub_id, item in snapshot.items():
                    for device in item["hub"]["devices"]:
                        if not isinstance(device, dict):
                            continue
                        device_id = device.get("id")
                        if device_id in self._cleared_alarms and not any(
                            not self._targets(hub_id, event) or self._targets(hub_id, event) == {device_id}
                            for event in alarms[hub_id]
                        ):
                            self._cleared_alarms.discard(device_id)
                self._last_alarm_fetch = now
            return snapshot

    def request_alarm_reconciliation(self) -> None:
        self._last_alarm_fetch = None

    async def _fetch_snapshot(self) -> dict[str, dict[str, Any]]:
        try:
            profile = await self.client.profile()
            profile_data = _unwrap(profile)
            ids = profile_data.get("hubIds")
            if not isinstance(ids, list):
                raise ApiError("Malformed hub IDs")
            snapshot: dict[str, dict[str, Any]] = {}
            for hub_id in ids:
                if not isinstance(hub_id, str) or not hub_id:
                    raise ApiError("Malformed hub ID")
                hub = _unwrap(await self.client.hub(hub_id))
                if hub.get("id") != hub_id or not isinstance(hub.get("devices"), list):
                    raise ApiError("Malformed hub data")
                snapshot[hub_id] = {"hub": hub}
            return snapshot
        except AuthError as exc:
            raise ConfigEntryAuthFailed from exc
        except ApiError as exc:
            raise UpdateFailed(str(exc)) from exc
