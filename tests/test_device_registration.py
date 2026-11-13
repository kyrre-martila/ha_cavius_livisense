"""Device registration across the old and new Home Assistant registry APIs."""
from types import SimpleNamespace
from inspect import signature

import pytest
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cavius_livisense import _register_devices
from custom_components.cavius_livisense.const import DOMAIN


@pytest.fixture(autouse=True)
def verify_cleanup():
    """Same HA 2025 safe-shutdown fixture workaround as test_integration."""
    yield


@pytest.mark.parametrize("modern_api", [False, True], ids=["ha-2025.1", "via-device-id-api"])
async def test_registration_preserves_devices_and_relationships(hass, monkeypatch, modern_api):
    """Exercise both API shapes against the real device and entity registries."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    original = registry.async_get_or_create
    native_via_device_id = "via_device_id" in signature(original).parameters
    calls = []

    if modern_api:
        # Model the new keyword while retaining the real registry's identity logic.
        def get_or_create(*, via_device_id=None, **kwargs):
            assert "via_device" not in kwargs
            calls.append((kwargs["identifiers"], via_device_id))
            if native_via_device_id:
                return original(via_device_id=via_device_id, **kwargs)
            device = original(**kwargs)
            if via_device_id is not None:
                return registry.async_update_device(device.id, via_device_id=via_device_id)
            return device

        monkeypatch.setattr(registry, "async_get_or_create", get_or_create)
    else:
        def get_or_create(**kwargs):
            # Reject either relationship keyword, as on the minimum version.
            assert "via_device_id" not in kwargs
            assert "via_device" not in kwargs
            return original(**kwargs)

        monkeypatch.setattr(registry, "async_get_or_create", get_or_create)

    models = ["SMOKECAVIUS", "HEATCAVIUS", "REMOTECAVIUS"]
    hub = {"name": "Home", "model": "MHub", "serialNo": "hub-serial", "devices": [
        {"id": f"device-{i}", "model": model, "name": f"Room {i}", "serialNo": f"serial-{i}"}
        for i, model in enumerate(models)
    ]}
    coordinator = SimpleNamespace(last_update_success=True, data={"hub": {"hub": hub}})
    # Existing devices from an earlier release must be updated, not replaced.
    hub_device = original(config_entry_id=entry.entry_id, identifiers={(DOMAIN, "hub")})
    old_devices = [original(config_entry_id=entry.entry_id, identifiers={(DOMAIN, d["id"])})
                   for d in hub["devices"]]
    entities = er.async_get(hass)
    old_entity = entities.async_get_or_create(
        "binary_sensor", DOMAIN, "device-0_smoke", config_entry=entry,
        device_id=old_devices[0].id, suggested_object_id="existing_smoke")

    for _ in range(2):
        _register_devices(hass, entry, coordinator)
        assert len(registry.devices) == 4
        registered_hub = registry.async_get_device(identifiers={(DOMAIN, "hub")})
        assert registered_hub.id == hub_device.id
        assert registered_hub.identifiers == {(DOMAIN, "hub")}
        assert (registered_hub.name, registered_hub.model, registered_hub.serial_number) == (
            "Home", "MHub", "hub-serial")
        for i, old in enumerate(old_devices):
            device = registry.async_get_device(identifiers={(DOMAIN, f"device-{i}")})
            assert device.id == old.id
            assert device.identifiers == {(DOMAIN, f"device-{i}")}
            assert device.via_device_id == hub_device.id
            assert device.config_entries == {entry.entry_id}
            assert (device.name, device.manufacturer, device.model, device.serial_number) == (
                f"Room {i}", "Cavius", models[i], f"serial-{i}")
        assert registered_hub.config_entries == {entry.entry_id}
        assert entities.async_get(old_entity.entity_id) == old_entity

    if modern_api:
        assert calls == [({(DOMAIN, "hub")}, None), *[
            ({(DOMAIN, f"device-{i}")}, hub_device.id) for i in range(3)
        ]] * 2
