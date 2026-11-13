"""Confirmed models and fire routing, using synthetic state and event payloads."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cavius_livisense import binary_sensor, sensor
from custom_components.cavius_livisense.const import DOMAIN
from custom_components.cavius_livisense.coordinator import LiviSenseCoordinator
from custom_components.cavius_livisense.diagnostics import async_get_config_entry_diagnostics


def hub_data():
    return {"id": "hub", "name": "Home", "state": {"status": "online", "updatedAt": 100},
            "devices": [{"id": f"device-{i}", "name": f"Device {i}", "type": "CaviusGroup",
                         "model": model, "state": {"alarm": None, "updatedAt": 100}}
                        for i, model in enumerate(["SMOKECAVIUS"] * 5 + ["HEATCAVIUS"] * 2
                                                  + ["REMOTECAVIUS", "UNKNOWN"])]}


@pytest.fixture
def coordinator(hass):
    client = MagicMock()
    client.profile = AsyncMock(return_value={"hubIds": ["hub"]})
    client.hub = AsyncMock(return_value=hub_data())
    client.unacknowledged_alarms = AsyncMock(return_value=[])
    return LiviSenseCoordinator(hass, client)


def event(code, target="hub", field="subject"):
    return {"hub": {"id": "hub"}, "event": {"code": code}, field: {"id": target}}


async def test_entities_and_diagnostics(coordinator):
    await coordinator.async_refresh()
    entry = SimpleNamespace(runtime_data=coordinator, async_on_unload=MagicMock())
    entities = []
    await binary_sensor.async_setup_entry(coordinator.hass, entry, entities.extend)
    await sensor.async_setup_entry(coordinator.hass, entry, entities.extend)
    by_id = {entity.unique_id: entity for entity in entities}
    expected = {f"device-{i}_{key}" for i in range(7)
                for key in (("smoke" if i < 5 else "heat"), "battery", "signal", "trouble", "last_cloud_update")}
    expected |= {"device-7_last_cloud_update", "hub_connectivity", "hub_websocket_connectivity",
                 "hub_last_cloud_update", "hub_last_websocket_message"}
    assert set(by_id) == expected
    assert by_id["device-5_heat"].device_class == BinarySensorDeviceClass.HEAT
    assert by_id["device-0_smoke"].device_class == BinarySensorDeviceClass.SMOKE
    assert by_id["device-5_battery"].device_class == BinarySensorDeviceClass.BATTERY
    assert by_id["device-5_trouble"].device_class == BinarySensorDeviceClass.PROBLEM
    assert by_id["device-7_last_cloud_update"].device_class == SensorDeviceClass.TIMESTAMP
    exported = await async_get_config_entry_diagnostics(None, entry)
    assert [d["supported_model"] for d in exported["hubs"][0]["devices"]] == [True] * 8 + [False]
    for status in ("offline", None):
        coordinator.data["hub"]["hub"]["state"] = {"status": status}
        assert not by_id["device-5_heat"].available
        assert not by_id["device-7_last_cloud_update"].available
    coordinator.data["hub"]["hub"]["state"] = None
    assert not by_id["device-5_heat"].available
    coordinator.last_update_success = False
    assert not by_id["device-5_heat"].available
    for call in entry.async_on_unload.call_args_list:
        call.args[0]()


@pytest.mark.parametrize("code", [1110, 1111])
@pytest.mark.parametrize("clear", [1659, 9421, 9422])
@pytest.mark.parametrize("field", ["device", "subject"])
async def test_heat_alarm_recovery_and_specific_routing(coordinator, code, clear, field):
    await coordinator.async_refresh()
    await coordinator.async_apply_message("hub:event", event(code, "device-5", field))
    assert coordinator.alarm_state("hub", "device-5") is True
    assert coordinator.alarm_state("hub", "device-6") is False
    assert coordinator.alarm_state("hub", "device-0") is False
    await coordinator.async_apply_message("hub:event", event(code, "device-6"))
    await coordinator.async_apply_message("hub:event", event(clear, "device-5", field))
    assert coordinator.alarm_state("hub", "device-5") is False
    assert coordinator.alarm_state("hub", "device-6") is True
    assert coordinator.alarm_state("hub", "device-7") is None


@pytest.mark.parametrize("code", [1110, 1111, 9421, 9422])
@pytest.mark.parametrize("target", ["device-7", "device-8", "missing"])
async def test_non_detector_event_never_broadcasts(coordinator, code, target):
    await coordinator.async_refresh()
    await coordinator.async_apply_message("hub:event", event(1110, "device-5"))
    await coordinator.async_apply_message("hub:event", event(code, target))
    assert coordinator.alarm_state("hub", "device-5") is True
    assert coordinator.alarm_state("hub", "device-0") is False
    assert target not in coordinator.flags


async def test_hub_events_and_rest_exclude_remote(coordinator):
    await coordinator.async_refresh()
    await coordinator.async_apply_message("hub:event", event(1110))
    assert all(coordinator.alarm_state("hub", f"device-{i}") for i in range(7))
    assert "device-7" not in coordinator.flags
    assert coordinator.alarm_state("hub", "device-7") is None
    await coordinator.async_apply_message("hub:event", event(9422))
    assert all(coordinator.alarm_state("hub", f"device-{i}") is False for i in range(7))
    coordinator.client.unacknowledged_alarms.return_value = [{"id": "new", "code": 1110, "subject": {"id": "device-5"}}]
    coordinator.request_alarm_reconciliation()
    await coordinator.async_refresh()
    assert coordinator.alarm_state("hub", "device-5") is True
    assert coordinator.alarm_state("hub", "device-6") is False
    coordinator.alarm_events["hub"] = [{"code": 1110, "subject": {"id": "device-7"}}]
    assert coordinator.alarm_state("hub", "device-6") is False
    coordinator.alarm_events["hub"] = [{"id": "new-hub", "code": 1110}]
    assert coordinator.alarm_state("hub", "device-6") is True
    assert coordinator.alarm_state("hub", "device-7") is None


async def test_heat_state_merge_faults_and_reconciliation(coordinator):
    await coordinator.async_refresh()
    await coordinator.async_apply_message("device:state", {"hub": {"id": "hub"},
        "device": {"id": "device-5"}, "state": {"alarm": "fire", "lastWarningAt": 123}})
    assert coordinator.alarm_state("hub", "device-5") is True
    assert coordinator.data["hub"]["hub"]["devices"][5]["state"]["lastWarningAt"] == 123
    await coordinator.async_apply_message("hub:event", event(1659, "device-5"))
    assert coordinator.alarm_state("hub", "device-5") is False
    for code, key, value in [(1384, "battery", True), (3384, "battery", False),
                             (1381, "signal", True), (3381, "signal", False),
                             (1380, "trouble", True), (1389, "trouble", True)]:
        await coordinator.async_apply_message("hub:event", event(code, "device-5"))
        assert coordinator.flags["device-5"][key] is value
        assert "device-6" not in coordinator.flags


@pytest.fixture(autouse=True)
def enable_custom_integrations(enable_custom_integrations):
    pass


@pytest.fixture(autouse=True)
def verify_cleanup():
    """Same HA 2025 safe-shutdown fixture workaround as test_integration."""
    yield


async def test_real_ha_registry_reload_and_stable_ids(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={"email": "test@example.com", "password": "secret"})
    entry.add_to_hass(hass)
    # Simulate the smoke entities already registered by an older integration.
    entities = er.async_get(hass)
    old = entities.async_get_or_create("binary_sensor", DOMAIN, "device-0_smoke", suggested_object_id="existing_smoke", config_entry=entry)
    with (
        patch("custom_components.cavius_livisense.api.LiviSenseClient.profile", return_value={"hubIds": ["hub"]}),
        patch("custom_components.cavius_livisense.api.LiviSenseClient.hub", return_value=hub_data()),
        patch("custom_components.cavius_livisense.api.LiviSenseClient.unacknowledged_alarms", return_value=[]),
        patch("custom_components.cavius_livisense.websocket.LiviSenseWebSocket.start"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        registry = dr.async_get(hass)
        hub = registry.async_get_device(identifiers={(DOMAIN, "hub")})
        assert len(registry.devices) == 9
        for i in range(8):
            device = registry.async_get_device(identifiers={(DOMAIN, f"device-{i}")})
            assert device.via_device_id == hub.id
        before = {e.unique_id: e.entity_id for e in er.async_entries_for_config_entry(entities, entry.entry_id)}
        assert before["device-0_smoke"] == old.entity_id
        assert hass.states.get(old.entity_id) is not None
        assert len(before) == 40
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert {e.unique_id: e.entity_id for e in er.async_entries_for_config_entry(entities, entry.entry_id)} == before
        assert len(registry.devices) == 9
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_heat_delayed_rest_clear_and_new_alarm(coordinator):
    old = {"id": "old", "code": 1110, "subject": {"id": "device-5"},
           "happenedAt": "2026-01-01T10:00:00Z"}
    coordinator.client.unacknowledged_alarms.return_value = [old]
    await coordinator.async_refresh()
    clear = event(1659, "device-5")
    clear["happenedAt"] = "2026-01-01T10:01:00Z"
    await coordinator.async_apply_message("hub:event", clear)
    coordinator.request_alarm_reconciliation()
    await coordinator.async_refresh()
    assert coordinator.alarm_state("hub", "device-5") is False
    coordinator.client.unacknowledged_alarms.return_value = [old, {
        "id": "new", "code": 1110, "subject": {"id": "device-5"},
        "happenedAt": "2026-01-01T10:02:00Z"}]
    coordinator.request_alarm_reconciliation()
    await coordinator.async_refresh()
    assert coordinator.alarm_state("hub", "device-5") is True
    from custom_components.cavius_livisense.api import ApiError
    coordinator.client.unacknowledged_alarms.side_effect = ApiError("unavailable")
    coordinator.request_alarm_reconciliation()
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert coordinator.alarm_state("hub", "device-5") is True
    assert not binary_sensor.DetectorFlag(coordinator, "hub", "device-5", "heat").available
