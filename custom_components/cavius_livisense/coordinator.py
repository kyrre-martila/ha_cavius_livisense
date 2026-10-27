"""Periodic cloud state and discovery."""
from __future__ import annotations

import asyncio
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

    async def async_apply_message(self, name: str, payload: Any) -> None:
        """Merge only documented state fields; refresh ambiguous updates."""
        if not isinstance(payload, dict):
            return
        if name == "hub:event":
            self.events.append(payload.copy())
            self.events = self.events[-100:]
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

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        # All HA refresh paths call this hook. Hold the same lock used by WS
        # merges until the snapshot is ready; HA assigns it without yielding.
        async with self._state_lock:
            return await self._fetch_snapshot()

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
