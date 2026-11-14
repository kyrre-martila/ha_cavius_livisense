"""Periodic cloud state and discovery."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ApiError, AuthError, LiviSenseClient
from .const import DOMAIN, UPDATE_INTERVAL, FIRE_DETECTOR_MODELS


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
        self.drill_states: dict[str, str] = {}
        self._cleared_alarms: dict[str, tuple[datetime | None, set[str]]] = {}
        self._cleared_device_states: dict[str, Any] = {}
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
        fields = ("alarm", "status", "mode", "updatedAt", "lastAlarmAt", "lastEventAt") if name == "hub:state" else ("alarm", "warning", "batteryLevel", "signalStrength", "updatedAt", "lastEventAt", "lastWarningAt")
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
        return {device["id"] for device in hub.get("devices", []) if isinstance(device, dict) and device.get("model") in FIRE_DETECTOR_MODELS and isinstance(device.get("id"), str)}

    @staticmethod
    def _device_reference(hub_id: str, event: dict[str, Any]) -> str | None:
        """Distinguish an explicit device (even unsupported) from a hub event."""
        for ref in (event.get("device"), event.get("subject")):
            if isinstance(ref, dict) and isinstance(ref.get("id"), str) and ref["id"] != hub_id:
                return ref["id"]
        return None

    def _targets(self, hub_id: str, event: dict[str, Any]) -> set[str]:
        reference = self._device_reference(hub_id, event)
        return {reference} if reference in self._detectors(hub_id) else set()

    @staticmethod
    def _event_time(event: dict[str, Any]) -> datetime | None:
        value = event.get("happenedAt")
        if not isinstance(value, str):
            return None
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        try:
            return result.astimezone(timezone.utc) if result.tzinfo else None
        except (ValueError, OverflowError):
            return None

    @staticmethod
    def _event_identity(event: dict[str, Any]) -> str:
        # Prefer an event ID when supplied; otherwise preserve the complete record.
        identifier = event.get("id")
        if isinstance(identifier, str) and identifier:
            return "id:" + identifier
        return "record:" + json.dumps(event, sort_keys=True, separators=(",", ":"), default=str)

    def _event_cleared(self, device_id: str, event: dict[str, Any]) -> bool:
        clear = self._cleared_alarms.get(device_id)
        if clear is None:
            return False
        clear_time, identities = clear
        event_time = self._event_time(event)
        if clear_time is not None and event_time is not None:
            return event_time <= clear_time
        return self._event_identity(event) in identities

    async def _apply_event(self, payload: dict[str, Any]) -> None:
        hub = payload.get("hub")
        event = payload.get("event")
        if not isinstance(hub, dict) or hub.get("id") not in (self.data or {}) or not isinstance(event, dict):
            return
        code = self._event_code(payload)
        if code is None:
            return
        hub_id = hub["id"]
        async with self._state_lock:
            # Drill lifecycle is diagnostic only; it must never set a fire flag.
            drill = {9811: "planned", 1119: "active", 3119: "ended", 9812: "cancelled"}
            if code in drill:
                self.drill_states[hub_id] = drill[code]
                self.async_update_listeners()
                return
            targets = self._targets(hub_id, payload)
            if code in (1110, 1111, 9421, 9422) and self._device_reference(hub_id, payload) is None:
                targets = self._detectors(hub_id)
            if code in (1384, 3384, 1381, 3381, 1380, 1389, 1659, 1110, 1111, 9421, 9422):
                for device_id in targets:
                    flags = self.flags.setdefault(device_id, {})
                    if code in (1110, 1111):
                        # Keep the existing internal alarm key for both fire models.
                        flags["smoke"] = True
                        self._cleared_alarms.pop(device_id, None)
                        self._cleared_device_states.pop(device_id, None)
                    elif code in (1659, 9421, 9422):
                        flags["smoke"] = False
                        previous = self._cleared_alarms.get(device_id)
                        identities = set(previous[1]) if previous else set()
                        identities.update(
                            self._event_identity(alarm)
                            for alarm in self.alarm_events.get(hub_id, [])
                            if self._device_reference(hub_id, alarm) in (None, device_id)
                        )
                        self._cleared_alarms[device_id] = (self._event_time(payload), identities)
                        device = next((d for d in self.data[hub_id]["hub"].get("devices", [])
                                       if isinstance(d, dict) and d.get("id") == device_id), {})
                        state = device.get("state")
                        self._cleared_device_states[device_id] = state.get("alarm") if isinstance(state, dict) else None
                    elif code in (1384, 3384):
                        flags["battery"] = code == 1384
                    elif code in (1381, 3381):
                        flags["signal"] = code == 1381
                    elif code in (1380, 1389):
                        flags["trouble"] = True
                self.async_update_listeners()

    @classmethod
    def _is_alarm_record(cls, event: dict[str, Any]) -> bool:
        """Only label-confirmed fire codes establish fire from event records."""
        return cls._event_code(event) in {1110, 1111}

    @classmethod
    def _is_non_fire_record(cls, event: dict[str, Any]) -> bool:
        """Documented lifecycle, fault, test and non-fire alarm records."""
        return cls._event_code(event) in {
            1119, 3119, 9811, 9812, 9411, 9412, 9413, 9421, 9422, 1659,
            1384, 3384, 1381, 3381, 1380, 1389, 1302, 3302, 1301, 3301,
            1344, 1137, 3137, 1130, 3130, 1400, 3408, 1602, 1604, 9995, 9996,
        }

    @classmethod
    def _confirmed_drill_state(cls, value: Any) -> bool:
        # Non-null state.alarm has never been captured. Only an explicit
        # documented drill code is sufficient evidence; do not infer a drill
        # from a concurrent hub drill, free text, or an unrelated REST record.
        return isinstance(value, dict) and cls._event_code(value) in {1119, 3119, 9811, 9812}

    def alarm_state(self, hub_id: str, device_id: str) -> bool | None:
        item = (self.data or {}).get(hub_id)
        if not item or device_id not in self._detectors(hub_id):
            return None
        device = next((d for d in item["hub"].get("devices", []) if isinstance(d, dict) and d.get("id") == device_id), None)
        if device is None:
            return None
        state = device.get("state")
        flag = self.flags.get(device_id, {}).get("smoke")
        if flag is True:
            return True
        if (isinstance(state, dict) and state.get("alarm") is not None
                and not self._confirmed_drill_state(state["alarm"])):
            if flag is not False or state["alarm"] != self._cleared_device_states.get(device_id):
                return True
        ambiguous = False
        for event in self.alarm_events.get(hub_id, []):
            if not isinstance(event, dict) or self._is_non_fire_record(event):
                continue
            if flag is False and self._event_cleared(device_id, event):
                continue
            if (self._targets(hub_id, event) == {device_id}
                    or self._device_reference(hub_id, event) is None):
                if self._is_alarm_record(event):
                    return True
                ambiguous = True
        if ambiguous:
            return None
        if device_id in self.flags and "smoke" in self.flags[device_id]:
            return False
        if isinstance(state, dict) and "alarm" in state and hub_id in self.alarm_events:
            return False
        return None

    @staticmethod
    def _event_code(event: dict[str, Any]) -> int | None:
        raw = event.get("event", event)
        value = raw.get("code") if isinstance(raw, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            return None
        try:
            return int(value)
        except ValueError:
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
                recovered = {}
                for hub_id, events in alarms.items():
                    if not isinstance(events, list):
                        continue
                    records = [event for event in events if isinstance(event, dict)]
                    identities = {self._event_identity(event) for event in records}
                    # Acknowledgement removes records from this endpoint. It is
                    # not a clear: retain recovered fires until an actual clear.
                    records.extend(
                        event for event in self.alarm_events.get(hub_id, [])
                        if self._is_alarm_record(event)
                        and self._event_identity(event) not in identities
                    )
                    recovered[hub_id] = records
                self.alarm_events = recovered
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
