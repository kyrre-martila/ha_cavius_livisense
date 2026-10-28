"""Verified hub online status."""
from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    known = set()

    def add_new():
        new = set(coordinator.data or {}) - known
        if new:
            async_add_entities(entity for hub_id in new for entity in (HubOnline(coordinator, hub_id), WebSocketConnectivity(coordinator, hub_id)))
            known.update(new)

    detector_known = set()

    def add_detectors():
        entities = []
        for hub_id, item in (coordinator.data or {}).items():
            for device in item["hub"].get("devices", []):
                if not isinstance(device, dict) or device.get("model") != "SMOKECAVIUS" or not isinstance(device.get("id"), str):
                    continue
                device_id = device["id"]
                if device_id in detector_known:
                    continue
                detector_known.add(device_id)
                entities.extend(DetectorFlag(coordinator, hub_id, device_id, key) for key in ("smoke", "battery", "signal", "trouble"))
        if entities:
            async_add_entities(entities)

    add_new()
    add_detectors()
    entry.async_on_unload(coordinator.async_add_listener(add_detectors))
    entry.async_on_unload(coordinator.async_add_listener(add_new))

class HubOnline(CoordinatorEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Connectivity"

    def __init__(self, coordinator, hub_id):
        super().__init__(coordinator)
        self.hub_id = hub_id
        self._attr_unique_id = f"{hub_id}_connectivity"
        self._attr_device_info = {"identifiers": {(DOMAIN, hub_id)}}

    @property
    def available(self):
        if not self.coordinator.last_update_success or self.hub_id not in self.coordinator.data:
            return False
        state = self.coordinator.data[self.hub_id]["hub"].get("state")
        return isinstance(state, dict) and isinstance(state.get("status"), str) and bool(state["status"])

    @property
    def is_on(self):
        state = self.coordinator.data[self.hub_id]["hub"].get("state")
        return isinstance(state, dict) and state.get("status") == "online"


class WebSocketConnectivity(CoordinatorEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "WebSocket connectivity"

    def __init__(self, coordinator, hub_id):
        super().__init__(coordinator)
        self._attr_unique_id = f"{hub_id}_websocket_connectivity"
        self._attr_device_info = {"identifiers": {(DOMAIN, hub_id)}}

    @property
    def is_on(self):
        return self.coordinator.websocket_connected


class DetectorFlag(CoordinatorEntity, BinarySensorEntity):
    """Read-only detector alarm or diagnostic flag."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, hub_id, device_id, key):
        super().__init__(coordinator)
        self.hub_id = hub_id
        self.device_id = device_id
        self.key = key
        self._attr_unique_id = f"{device_id}_{key}"
        self._attr_device_info = {"identifiers": {(DOMAIN, device_id)}}
        self._attr_name = {"smoke": "Smoke alarm", "battery": "Battery low", "signal": "Signal loss", "trouble": "Sensor trouble"}[key]
        self._attr_device_class = {"smoke": BinarySensorDeviceClass.SMOKE, "battery": BinarySensorDeviceClass.BATTERY, "signal": BinarySensorDeviceClass.PROBLEM, "trouble": BinarySensorDeviceClass.PROBLEM}[key]
        if key != "smoke":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self):
        if not self.coordinator.last_update_success:
            return False
        item = (self.coordinator.data or {}).get(self.hub_id)
        return bool(item and item["hub"].get("state", {}).get("status") == "online")

    @property
    def is_on(self):
        if self.key == "smoke":
            return self.coordinator.alarm_state(self.hub_id, self.device_id)
        return self.coordinator.flags.get(self.device_id, {}).get(self.key)
