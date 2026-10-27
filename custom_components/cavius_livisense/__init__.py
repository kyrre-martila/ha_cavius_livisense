"""Cavius LiviSense integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import device_registry as dr

from .api import AuthError, ConnectionError, LiviSenseClient
from .const import DOMAIN, PLATFORMS
from .coordinator import LiviSenseCoordinator

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client = LiviSenseClient(async_get_clientsession(hass), entry.data["email"], entry.data["password"])
    coordinator = LiviSenseCoordinator(hass, client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except AuthError as exc:
        raise ConfigEntryAuthFailed from exc
    except ConnectionError as exc:
        raise ConfigEntryNotReady from exc
    entry.runtime_data = coordinator
    _register_devices(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(coordinator.async_add_listener(lambda: _register_devices(hass, entry, coordinator)))
    return True

def _register_devices(hass: HomeAssistant, entry: ConfigEntry, coordinator: LiviSenseCoordinator) -> None:
    if not coordinator.last_update_success:
        return
    registry = dr.async_get(hass)
    for hub_id, item in coordinator.data.items():
        hub = item["hub"]
        registry.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, hub_id)}, name=hub.get("name"), manufacturer="Cavius", model=hub.get("model"), serial_number=hub.get("serialNo"))
        for device in hub.get("devices", []):
            if not isinstance(device, dict) or device.get("model") != "SMOKECAVIUS" or not isinstance(device.get("id"), str):
                continue
            registry.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, device["id"])}, via_device=(DOMAIN, hub_id), name=device.get("name"), manufacturer="Cavius", model=device.get("model"), serial_number=device.get("serialNo"))

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry.async_unload()
    return unloaded
