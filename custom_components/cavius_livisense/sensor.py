"""Documented diagnostic timestamps for hubs and detectors."""
from datetime import datetime, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    entities = []
    for hub_id, item in coordinator.data.items():
        entities.append(LastUpdate(coordinator, hub_id))
        for device in item["hub"].get("devices", []):
            if isinstance(device, dict) and device.get("model") == "SMOKECAVIUS" and isinstance(device.get("id"), str):
                entities.append(LastUpdate(coordinator, hub_id, device["id"]))
    async_add_entities(entities)

class LastUpdate(CoordinatorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_has_entity_name = True
    _attr_name = "Last cloud update"

    def __init__(self, coordinator, hub_id, device_id=None):
        super().__init__(coordinator)
        self.hub_id = hub_id
        self.device_id = device_id
        self._attr_unique_id = f"{device_id or hub_id}_last_cloud_update"
        self._attr_device_info = {"identifiers": {(DOMAIN, device_id or hub_id)}}

    @property
    def available(self):
        if not self.coordinator.last_update_success or self.hub_id not in self.coordinator.data:
            return False
        hub = self.coordinator.data[self.hub_id]["hub"]
        if hub.get("state", {}).get("status") != "online":
            return False
        return self._state() is not None

    def _state(self):
        hub = self.coordinator.data[self.hub_id]["hub"]
        if self.device_id is None:
            return hub.get("state") if isinstance(hub.get("state"), dict) else None
        for device in hub.get("devices", []):
            if isinstance(device, dict) and device.get("id") == self.device_id:
                return device.get("state") if isinstance(device.get("state"), dict) else None
        return None

    @property
    def native_value(self):
        state = self._state()
        value = state.get("updatedAt") if state else None
        if not isinstance(value, (int, float)):
            return None
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
