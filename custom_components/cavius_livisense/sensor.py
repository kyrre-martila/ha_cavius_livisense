"""Documented diagnostic timestamps for hubs and detectors."""
from datetime import datetime, timezone
import math

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    known = set()

    def add_new():
        entities = []
        for hub_id, item in (coordinator.data or {}).items():
            if hub_id not in known:
                entities.append(LastUpdate(coordinator, hub_id))
                known.add(hub_id)
            for device in item["hub"].get("devices", []):
                if isinstance(device, dict) and device.get("model") == "SMOKECAVIUS" and isinstance(device.get("id"), str) and device["id"] not in known:
                    entities.append(LastUpdate(coordinator, hub_id, device["id"]))
                    known.add(device["id"])
        if entities:
            async_add_entities(entities)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))

class LastUpdate(CoordinatorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
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
        hub_state = hub.get("state")
        if not isinstance(hub_state, dict) or hub_state.get("status") != "online":
            return False
        state = self._state()
        return isinstance(state, dict) and self.native_value is not None

    def _state(self):
        item = (self.coordinator.data or {}).get(self.hub_id)
        if item is None:
            return None
        hub = item["hub"]
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
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
