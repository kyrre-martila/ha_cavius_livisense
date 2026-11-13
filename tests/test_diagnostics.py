"""Safe discovery exports use all cached devices without network activity."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.cavius_livisense.diagnostics import async_get_config_entry_diagnostics


async def test_all_devices_and_no_requests():
    # Heat/remote model strings are synthetic fixtures, not API claims.
    devices = [
        {"model": model, "type": kind, "extras": {"appDeviceId": f"{kind}-{model}"},
         "state": {"alarm": "private value", "batteryLevel": 99}}
        for model, kind in [("SMOKECAVIUS", "CaviusGroup"),
                            ("TEST_HEAT", "TestHeatType"),
                            ("TEST_REMOTE", "TestRemoteType"),
                            ("TEST_UNKNOWN", "UnknownType")]
    ]
    snapshot = {"private-hub-id": {"hub": {"devices": devices}},
                "another-id": {"hub": {"devices": []}}}
    before = copy.deepcopy(snapshot)
    client = AsyncMock()
    entry = SimpleNamespace(runtime_data=SimpleNamespace(data=snapshot, client=client))
    result = await async_get_config_entry_diagnostics(None, entry)
    assert result["hub_count"] == 2
    assert [hub["device_count"] for hub in result["hubs"]] == [4, 0]
    exported = result["hubs"][0]["devices"]
    assert [d["model"] for d in exported] == [d["model"] for d in devices]
    assert [d["type"] for d in exported] == [d["type"] for d in devices]
    assert [d["supported_model"] for d in exported] == [True, False, False, False]
    assert exported[1]["extras"] == {"appDeviceId": "TestHeatType-TEST_HEAT"}
    assert exported[0]["state_fields"] == ["alarm", "batteryLevel"]
    assert snapshot == before
    assert client.mock_calls == []


@pytest.mark.parametrize("value", [None, [], "bad", 42])
async def test_missing_snapshot(value):
    entry = SimpleNamespace(runtime_data=SimpleNamespace(data=value))
    assert await async_get_config_entry_diagnostics(None, entry) == {"hub_count": 0, "hubs": []}


async def test_malformed_and_sensitive_fields():
    secret = "alice@example.com"
    devices = [None, {}, {"model": [], "type": {}, "state": []},
               {"model": secret, "type": "550e8400-e29b-41d4-a716-446655440000"},
               {"model": "SN123456", "type": "Kitchen Detector"},
               {"model": "SMOKECAVIUS", "type": "CaviusGroup",
                "id": "private-device", "serialNo": "private-serial", "name": "Laundry room",
                "extras": {"appDeviceId": "private-app-id", "token": "private-token"},
                "state": {"alarm": secret, "serialNo": secret, secret: secret, 42: secret}}]
    entry = SimpleNamespace(
        data={"email": secret, "password": "private-password"},
        runtime_data=SimpleNamespace(data={"private-hub": {"hub": {
            "name": "Private home", "address": secret, "devices": devices}}}))
    result = await async_get_config_entry_diagnostics(None, entry)
    output = json.dumps(result)
    for sensitive in [secret, "550e8400", "SN123456", "Kitchen", "private-", "Laundry", "Private home", "serialNo"]:
        assert sensitive not in output
    exported = result["hubs"][0]["devices"]
    assert len(exported) == len(devices)
    assert exported[0]["model"] is None
    assert exported[2]["model"] == "**REDACTED**"
    assert exported[-1]["state_fields"] == ["alarm"]
    assert "extras" not in exported[-1]


async def test_malformed_hubs():
    entry = SimpleNamespace(runtime_data=SimpleNamespace(data={
        "a": None, "b": {"hub": []}, "c": {"hub": {"devices": {}}}}))
    result = await async_get_config_entry_diagnostics(None, entry)
    assert result == {"hub_count": 3, "hubs": [{"device_count": 0, "devices": []}] * 3}


async def test_smoke_discovery_filters_unchanged(hass):
    from unittest.mock import MagicMock, patch

    from custom_components.cavius_livisense import _register_devices, binary_sensor, sensor
    from custom_components.cavius_livisense.coordinator import LiviSenseCoordinator

    coordinator = LiviSenseCoordinator(hass, MagicMock())
    coordinator.async_set_updated_data({"hub": {"hub": {"devices": [
        {"id": "smoke", "model": "SMOKECAVIUS", "state": {"alarm": None}},
        {"id": "heat", "model": "TEST_HEAT"},
        {"id": "remote", "model": "TEST_REMOTE"},
    ]}}})
    entry = SimpleNamespace(entry_id="entry", runtime_data=coordinator, async_on_unload=MagicMock())
    registry = MagicMock()
    with patch("custom_components.cavius_livisense.dr.async_get", return_value=registry):
        _register_devices(hass, entry, coordinator)
    assert [call.kwargs["identifiers"] for call in registry.async_get_or_create.call_args_list] == [
        {("cavius_livisense", "hub")}, {("cavius_livisense", "smoke")}]
    entities = []
    await binary_sensor.async_setup_entry(hass, entry, entities.extend)
    await sensor.async_setup_entry(hass, entry, entities.extend)
    assert len(entities) == 9
    assert {entity.device_id for entity in entities if hasattr(entity, "device_id")} == {None, "smoke"}
    assert coordinator._detectors("hub") == {"smoke"}
    for call in entry.async_on_unload.call_args_list:
        call.args[0]()
