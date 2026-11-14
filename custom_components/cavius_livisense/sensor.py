"""Documented diagnostic timestamps for hubs and detectors."""
from datetime import datetime, timezone
import math

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN, SUPPORTED_MODELS, FIRE_DETECTOR_MODELS

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    known = set()

    def add_new():
        entities = []
        for hub_id, item in (coordinator.data or {}).items():
            if hub_id not in known:
                entities.extend((LastUpdate(coordinator, hub_id), LastWebSocketMessage(coordinator, hub_id), HubDrillState(coordinator, hub_id), LastFireDrill(coordinator, hub_id)))
                known.add(hub_id)
            for device in item["hub"].get("devices", []):
                if isinstance(device, dict) and device.get("model") in SUPPORTED_MODELS and isinstance(device.get("id"), str) and device["id"] not in known:
                    entities.append(LastUpdate(coordinator, hub_id, device["id"]))
                    if device.get("model") in FIRE_DETECTOR_MODELS:
                        entities.extend((LastTested(coordinator, hub_id, device["id"]), LastTestResult(coordinator, hub_id, device["id"])))
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


class LastWebSocketMessage(CoordinatorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Last WebSocket message"

    def __init__(self, coordinator, hub_id):
        super().__init__(coordinator)
        self._attr_unique_id = f"{hub_id}_last_websocket_message"
        self._attr_device_info = {"identifiers": {(DOMAIN, hub_id)}}

    @property
    def native_value(self):
        return self.coordinator.last_websocket_message


class _Diagnostic(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, hub_id, device_id=None):
        super().__init__(coordinator)
        self.hub_id = hub_id
        self.device_id = device_id
        self._attr_device_info = {"identifiers": {(DOMAIN, device_id or hub_id)}}

    @property
    def available(self):
        if not self.coordinator.last_update_success:
            return False
        item = (self.coordinator.data or {}).get(self.hub_id)
        if not item:
            return False
        state = item["hub"].get("state")
        return isinstance(state, dict) and state.get("status") == "online"

    def _object(self):
        item = (self.coordinator.data or {}).get(self.hub_id)
        if not item:
            return {}
        hub = item["hub"]
        if self.device_id is None:
            return hub
        return next((d for d in hub.get("devices", []) if isinstance(d, dict) and d.get("id") == self.device_id), {})


def _iso_datetime(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


class HubDrillState(_Diagnostic):
    _attr_name = "Fire drill state"

    def __init__(self, coordinator, hub_id):
        super().__init__(coordinator, hub_id)
        self._attr_unique_id = f"{hub_id}_fire_drill_state"

    @property
    def native_value(self):
        return self.coordinator.drill_states.get(self.hub_id)


class LastFireDrill(_Diagnostic):
    _attr_name = "Last fire drill"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, hub_id):
        super().__init__(coordinator, hub_id)
        self._attr_unique_id = f"{hub_id}_last_fire_drill"

    @property
    def native_value(self):
        extras = self._object().get("extras")
        return _iso_datetime(extras.get("lastFireDrillAt")) if isinstance(extras, dict) else None


class LastTested(_Diagnostic):
    _attr_name = "Last tested"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, hub_id, device_id):
        super().__init__(coordinator, hub_id, device_id)
        self._attr_unique_id = f"{device_id}_last_tested"

    @property
    def native_value(self):
        return _iso_datetime(self._object().get("lastTestedAt"))


class LastTestResult(_Diagnostic):
    _attr_name = "Last test result"

    def __init__(self, coordinator, hub_id, device_id):
        super().__init__(coordinator, hub_id, device_id)
        self._attr_unique_id = f"{device_id}_last_test_result"

    @property
    def native_value(self):
        extras = self._object().get("extras")
        result = extras.get("lastTestResult") if isinstance(extras, dict) else None
        return result if isinstance(result, str) and result else None
