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
            async_add_entities(HubOnline(coordinator, hub_id) for hub_id in new)
            known.update(new)

    add_new()
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
        return isinstance(state, dict) and isinstance(state.get("status"), str)

    @property
    def is_on(self):
        state = self.coordinator.data[self.hub_id]["hub"].get("state")
        return isinstance(state, dict) and state.get("status") == "online"
