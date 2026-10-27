"""Periodic cloud state and discovery."""
from __future__ import annotations

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
    """Fetch complete snapshots; a future WebSocket listener can request refreshes."""

    def __init__(self, hass: HomeAssistant, client: LiviSenseClient) -> None:
        super().__init__(hass, logger=__import__("logging").getLogger(__name__), name=DOMAIN, update_interval=UPDATE_INTERVAL)
        self.client = client

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            profile = await self.client.profile()
            profile_data = _unwrap(profile)
            ids = profile_data.get("hubIds", [])
            if not isinstance(ids, list):
                raise ApiError("Malformed hub IDs")
            await self.client.hubs()  # Confirm that account hub listing is accessible.
            snapshot: dict[str, dict[str, Any]] = {}
            for hub_id in ids:
                if not isinstance(hub_id, str) or not hub_id:
                    continue
                hub = _unwrap(await self.client.hub(hub_id))
                if not isinstance(hub.get("id"), str):
                    raise ApiError("Hub lacks ID")
                health = await self.client.health(hub_id)
                snapshot[hub_id] = {"hub": hub, "health": health}
            return snapshot
        except AuthError as exc:
            raise ConfigEntryAuthFailed from exc
        except ApiError as exc:
            raise UpdateFailed(str(exc)) from exc
