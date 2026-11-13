"""Privacy-limited discovery diagnostics from the coordinator's cached data."""
from __future__ import annotations

import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

_REDACTED = "**REDACTED**"
# Conservative schema identifiers only. Numbers are excluded because serials,
# UUIDs and opaque tokens cannot reliably be distinguished from model codes.
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z_-]{0,63}\Z")
_SENSITIVE = re.compile(
    r"serial|token|password|secret|email|address|credential|uuid|id$|name$", re.I
)


def _identifier(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        return _REDACTED
    if _SENSITIVE.search(value):
        return _REDACTED
    return value


def _device_diagnostics(raw: Any) -> dict[str, Any]:
    device = raw if isinstance(raw, dict) else {}
    model = _identifier(device.get("model"))
    device_type = _identifier(device.get("type"))
    state = device.get("state")
    result = {
        "model": model,
        "type": device_type,
        "supported_model": device.get("model") == "SMOKECAVIUS",
        "state_fields": sorted({
            safe for key in state
            if (safe := _identifier(key)) not in (None, _REDACTED)
        }) if isinstance(state, dict) else [],
    }
    extras = device.get("extras")
    # Only the documented type-model composition proves this is a schema
    # identifier. Never pass through an arbitrary appDeviceId or nested extras.
    if (
        isinstance(extras, dict)
        and model not in (None, _REDACTED)
        and device_type not in (None, _REDACTED)
        and extras.get("appDeviceId") == f"{device_type}-{model}"
    ):
        result["extras"] = {"appDeviceId": extras["appDeviceId"]}
    return result


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Export discovery metadata without requests, config data or raw payloads."""
    snapshot = entry.runtime_data.data
    hubs = []
    for item in snapshot.values() if isinstance(snapshot, dict) else ():
        hub = item.get("hub") if isinstance(item, dict) else None
        devices = hub.get("devices") if isinstance(hub, dict) else None
        devices = devices if isinstance(devices, list) else []
        hubs.append({
            "device_count": len(devices),
            "devices": [_device_diagnostics(device) for device in devices],
        })
    return {"hub_count": len(hubs), "hubs": hubs}
