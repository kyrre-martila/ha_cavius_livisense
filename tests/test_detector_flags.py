"""Mocked detector alarm and fault state tests."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.cavius_livisense.coordinator import LiviSenseCoordinator
from custom_components.cavius_livisense.binary_sensor import DetectorFlag


@pytest.fixture
def coordinator(hass):
    client = MagicMock()
    client.profile = AsyncMock(return_value={"hubIds": ["hub"]})
    client.hub = AsyncMock(return_value={"data": {
        "id": "hub", "state": {"status": "online"},
        "devices": [
            {"id": "a", "model": "SMOKECAVIUS", "state": {"alarm": None}},
            {"id": "b", "model": "SMOKECAVIUS", "state": {"alarm": None}},
        ],
    }})
    client.unacknowledged_alarms = AsyncMock(return_value=[])
    return LiviSenseCoordinator(hass, client)


def event(code, subject=None):
    return {"hub": {"id": "hub"}, "event": {"code": code}, "subject": subject or {"id": "hub", "type": "hub"}}


@pytest.mark.asyncio
async def test_fire_association_ambiguous_clear_and_reconciliation(coordinator):
    await coordinator.async_refresh()
    assert coordinator.alarm_state("hub", "a") is False
    await coordinator.async_apply_message("hub:event", event(1110, {"id": "a", "type": "device"}))
    assert coordinator.alarm_state("hub", "a") is True
    assert coordinator.alarm_state("hub", "b") is False
    await coordinator.async_apply_message("hub:event", event(1659, {"id": "a", "type": "device"}))
    assert coordinator.alarm_state("hub", "a") is False
    await coordinator.async_apply_message("hub:event", event(1111))
    assert coordinator.alarm_state("hub", "a") is True
    assert coordinator.alarm_state("hub", "b") is True
    await coordinator.async_apply_message("hub:event", event(9422))
    assert coordinator.alarm_state("hub", "a") is False
    assert coordinator.alarm_state("hub", "b") is False
    coordinator.client.unacknowledged_alarms.return_value = [{"code": 1110, "subject": {"id": "a"}}]
    coordinator.request_alarm_reconciliation()
    await coordinator.async_refresh()
    assert coordinator.alarm_state("hub", "a") is True
    assert coordinator.alarm_state("hub", "b") is False


@pytest.mark.asyncio
async def test_fire_grace_and_fault_recovery(coordinator):
    await coordinator.async_refresh()
    await coordinator.async_apply_message("hub:event", event(1110, {"id": "a"}))
    coordinator.request_alarm_reconciliation()
    await coordinator.async_refresh()
    assert coordinator.alarm_state("hub", "a") is True
    for code, key, value in ((1384, "battery", True), (3384, "battery", False),
                             (1381, "signal", True), (3381, "signal", False),
                             (1380, "trouble", True), (1389, "trouble", True)):
        await coordinator.async_apply_message("hub:event", event(code, {"id": "a"}))
        assert coordinator.flags["a"][key] is value
    coordinator.request_alarm_reconciliation()
    await coordinator.async_refresh()
    assert coordinator.alarm_state("hub", "a") is True
    await coordinator.async_apply_message("hub:event", event(1659, {"id": "a"}))
    assert coordinator.alarm_state("hub", "a") is False
    assert coordinator.flags["a"]["trouble"] is True


@pytest.mark.asyncio
async def test_unknown_offline_and_failed_refresh(coordinator):
    await coordinator.async_refresh()
    sensor = DetectorFlag(coordinator, "hub", "a", "battery")
    assert sensor.is_on is None
    coordinator.data["hub"]["hub"]["state"]["status"] = "offline"
    assert sensor.available is False
    coordinator.client.unacknowledged_alarms.side_effect = Exception("cloud error")
    coordinator.request_alarm_reconciliation()
    # Existing alarm flags survive reconnect/reload-style REST reconciliation.
    await coordinator.async_apply_message("hub:event", event(1384, {"id": "a"}))
    assert sensor.is_on is True


@pytest.mark.asyncio
async def test_specific_clear_preserves_other_and_ambiguous_alarms(coordinator):
    coordinator.client.unacknowledged_alarms.return_value = [
        {"code": 1110, "subject": {"id": "a"}},
        {"code": 1110, "subject": {"id": "b"}},
        {"code": 1111, "subject": {"id": "hub"}},
    ]
    await coordinator.async_refresh()
    await coordinator.async_apply_message("hub:event", event(1659, {"id": "a"}))
    assert coordinator.alarm_state("hub", "a") is False
    assert coordinator.alarm_state("hub", "b") is True
    coordinator.request_alarm_reconciliation()
    await coordinator.async_refresh()
    assert coordinator.alarm_state("hub", "a") is False
    assert coordinator.alarm_state("hub", "b") is True
    await coordinator.async_apply_message("hub:event", event(9421))
    assert coordinator.alarm_state("hub", "a") is False
    assert coordinator.alarm_state("hub", "b") is False


@pytest.mark.asyncio
async def test_failed_reconciliation_preserves_fire(coordinator):
    from custom_components.cavius_livisense.api import ApiError

    await coordinator.async_refresh()
    await coordinator.async_apply_message("hub:event", event(1110, {"id": "a"}))
    coordinator.client.unacknowledged_alarms.side_effect = ApiError("bad response")
    coordinator.request_alarm_reconciliation()
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert coordinator.alarm_state("hub", "a") is True
    assert not DetectorFlag(coordinator, "hub", "a", "smoke").available


@pytest.mark.asyncio
async def test_new_rest_alarm_after_clear_and_stale_alarm(coordinator):
    old = {"id": "old", "code": 1110, "subject": {"id": "a"},
           "happenedAt": "2026-01-01T10:00:00Z"}
    other = {"id": "other", "code": 1110, "subject": {"id": "b"},
             "happenedAt": "2026-01-01T10:00:00Z"}
    coordinator.client.unacknowledged_alarms.return_value = [old, other]
    await coordinator.async_refresh()
    clear = event(1659, {"id": "a"})
    clear["happenedAt"] = "2026-01-01T10:01:00Z"
    await coordinator.async_apply_message("hub:event", clear)
    coordinator.request_alarm_reconciliation()
    await coordinator.async_refresh()
    assert coordinator.alarm_state("hub", "a") is False
    assert coordinator.alarm_state("hub", "b") is True
    new = {"id": "new", "code": 1110, "subject": {"id": "a"},
           "happenedAt": "2026-01-01T10:02:00Z"}
    coordinator.client.unacknowledged_alarms.return_value = [old, other, new]
    coordinator.request_alarm_reconciliation()
    await coordinator.async_refresh()
    assert coordinator.alarm_state("hub", "a") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [9421, 9422])
async def test_global_clear_with_delayed_rest_and_new_alarm(coordinator, code):
    old = {"id": "old", "code": 1111, "subject": {"id": "hub"},
           "happenedAt": "2026-01-01T10:00:00Z"}
    coordinator.client.unacknowledged_alarms.return_value = [old]
    await coordinator.async_refresh()
    clear = event(code)
    clear["happenedAt"] = "2026-01-01T10:01:00Z"
    await coordinator.async_apply_message("hub:event", clear)
    coordinator.request_alarm_reconciliation()
    await coordinator.async_refresh()
    assert coordinator.alarm_state("hub", "a") is False
    assert coordinator.alarm_state("hub", "b") is False
    newer = {"id": "new", "code": 1111, "subject": {"id": "hub"},
             "happenedAt": "2026-01-01T10:02:00Z"}
    coordinator.client.unacknowledged_alarms.return_value = [old, newer]
    coordinator.request_alarm_reconciliation()
    await coordinator.async_refresh()
    assert coordinator.alarm_state("hub", "a") is True
    assert coordinator.alarm_state("hub", "b") is True


@pytest.mark.asyncio
async def test_identity_fallback_and_failure_preserve_alarm(coordinator):
    from custom_components.cavius_livisense.api import ApiError

    old = {"id": "old", "subject": {"id": "a"}}
    coordinator.client.unacknowledged_alarms.return_value = [old]
    await coordinator.async_refresh()
    await coordinator.async_apply_message("hub:event", event(1659, {"id": "a"}))
    coordinator.request_alarm_reconciliation()
    await coordinator.async_refresh()
    assert coordinator.alarm_state("hub", "a") is False
    coordinator.client.unacknowledged_alarms.return_value = [old, {"id": "new", "subject": {"id": "a"}}]
    coordinator.request_alarm_reconciliation()
    await coordinator.async_refresh()
    assert coordinator.alarm_state("hub", "a") is True
    coordinator.client.unacknowledged_alarms.side_effect = ApiError("malformed response")
    coordinator.request_alarm_reconciliation()
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert coordinator.alarm_state("hub", "a") is True


@pytest.mark.asyncio
async def test_new_device_alarm_state_after_clear(coordinator):
    await coordinator.async_refresh()
    await coordinator.async_apply_message("hub:event", event(1659, {"id": "a"}))
    coordinator.client.hub.return_value["data"]["devices"][0]["state"]["alarm"] = "fire"
    await coordinator.async_refresh()
    assert coordinator.alarm_state("hub", "a") is True
