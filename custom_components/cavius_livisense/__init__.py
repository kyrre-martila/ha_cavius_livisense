"""Cavius LiviSense integration."""
from __future__ import annotations

from inspect import signature

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import device_registry as dr

from .api import AuthError, ConnectionError, LiviSenseClient
from .const import DOMAIN, PLATFORMS, SUPPORTED_MODELS
from .coordinator import LiviSenseCoordinator
from .websocket import LiviSenseWebSocket

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
    coordinator.websocket = LiviSenseWebSocket(client, coordinator, entry)
    coordinator.websocket.start()
    entry.async_on_unload(coordinator.async_add_listener(lambda: _register_devices(hass, entry, coordinator)))
    return True

def _register_devices(hass: HomeAssistant, entry: ConfigEntry, coordinator: LiviSenseCoordinator) -> None:
    if not coordinator.last_update_success:
        return
    registry = dr.async_get(hass)
    supports_via_device_id = "via_device_id" in signature(registry.async_get_or_create).parameters
    for hub_id, item in coordinator.data.items():
        hub = item["hub"]
        hub_device = registry.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, hub_id)}, name=hub.get("name"), manufacturer="Cavius", model=hub.get("model"), serial_number=hub.get("serialNo"))
        for device in hub.get("devices", []):
            if not isinstance(device, dict) or device.get("model") not in SUPPORTED_MODELS or not isinstance(device.get("id"), str):
                continue
            relationship = {"via_device_id": hub_device.id} if supports_via_device_id else {}
            device_entry = registry.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, device["id"])}, name=device.get("name"), manufacturer="Cavius", model=device.get("model"), serial_number=device.get("serialNo"), **relationship)
            # HA 2025.1 accepts the registry ID only through async_update_device.
            if not supports_via_device_id and device_entry.via_device_id != hub_device.id:
                registry.async_update_device(device_entry.id, via_device_id=hub_device.id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await entry.runtime_data.websocket.stop()
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    return unloaded
