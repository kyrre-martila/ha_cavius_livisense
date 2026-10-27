"""Verified hub online status."""
from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities(HubOnline(coordinator, hub_id) for hub_id in coordinator.data)

class HubOnline(CoordinatorEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_has_entity_name = True
    _attr_name = "Connectivity"

    def __init__(self, coordinator, hub_id):
        super().__init__(coordinator)
        self.hub_id = hub_id
        self._attr_unique_id = f"{hub_id}_connectivity"
        self._attr_device_info = {"identifiers": {(DOMAIN, hub_id)}}

    @property
    def available(self):
        return self.coordinator.last_update_success and self.hub_id in self.coordinator.data

    @property
    def is_on(self):
        return self.coordinator.data[self.hub_id]["hub"].get("state", {}).get("status") == "online"
