"""Synthetic fault evidence: no credentials, live requests, drills or tests."""
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.cavius_livisense.api import ApiError
from custom_components.cavius_livisense.binary_sensor import DetectorFlag
from custom_components.cavius_livisense.coordinator import LiviSenseCoordinator

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)


def record(code, seconds=-60, **references):
    return {"hub": {"id": "hub"}, "event": {"code": code},
            "happenedAt": (NOW + timedelta(seconds=seconds)).isoformat(), **references}


def detector_event(code, seconds=-60, device="heat"):
    return record(code, seconds, subject={"id": device, "type": "device"})


@pytest.fixture
def coordinator(hass, freezer):
    freezer.move_to(NOW)
    client = MagicMock()
    client.profile = AsyncMock(return_value={"hubIds": ["hub"]})
    client.hub = AsyncMock(return_value={"id": "hub", "state": {"status": "online"}, "devices": [
        {"id": "heat", "model": "HEATCAVIUS", "state": {
            "batteryLevel": None, "warning": None, "signalStrength": None, "alarm": None}},
        {"id": "smoke", "model": "SMOKECAVIUS", "state": {}},
        {"id": "remote", "model": "REMOTECAVIUS", "state": {}},
    ]})
    client.unacknowledged_alarms = AsyncMock(return_value=[])
    client.activity = AsyncMock(return_value=([], False))
    result = LiviSenseCoordinator(hass, client)
    result._fault_stream_started = NOW - timedelta(days=1)
    return result


def status(coordinator, key="battery", device="heat"):
    return DetectorFlag(coordinator, "hub", device, key).is_on


@pytest.mark.parametrize("code,key,value", [(1384, "battery", True), (3384, "battery", False),
    (1381, "signal", True), (3381, "signal", False), (1380, "trouble", True), (1389, "trouble", True)])
async def test_startup_complete_suffix(coordinator, code, key, value):
    coordinator.client.activity.return_value = ([detector_event(code)], True)
    await coordinator.async_refresh()
    assert status(coordinator, key) is value
    assert status(coordinator, key, "smoke") is None
    assert coordinator.alarm_state("hub", "heat") is False


@pytest.mark.parametrize("references", [
    {}, {"subject": {"id": "hub", "type": "hub"}},
    {"subject": {"name": "Basement laundry"}}, {"actor": {"id": "heat"}},
    {"device": {"id": "heat"}, "subject": {"id": "smoke"}},
    {"device": {"id": "heat"}, "subject": {"id": "hub"}},
    {"subject": {"id": "heat", "type": "hub"}},
    {"subject": {"id": "heat"}, "device": None},
    {"device": {"id": ""}},
])
async def test_no_guessing_or_broadcast(coordinator, references):
    await coordinator.async_refresh()
    await coordinator.async_apply_message("hub:event", record(1384, **references))
    assert status(coordinator) is None
    assert status(coordinator, device="smoke") is None


@pytest.mark.parametrize("references", [
    {"device": {"id": "heat"}}, {"subject": {"id": "heat"}},
    {"device": {"id": "heat"}, "subject": {"id": "heat"}},
])
async def test_explicit_agreeing_ids(coordinator, references):
    await coordinator.async_refresh()
    await coordinator.async_apply_message("hub:event", record(1384, **references))
    assert status(coordinator) is True
    assert status(coordinator, device="smoke") is None


@pytest.mark.parametrize("target", ["remote", "unrecognized", "another-hub-device"])
async def test_unknown_and_unsupported_ids_do_not_fan_out(coordinator, target):
    await coordinator.async_refresh()
    await coordinator.async_apply_message("hub:event", detector_event(1384, device=target))
    assert status(coordinator) is None


async def test_ordering_and_recovered_transition(coordinator):
    await coordinator.async_refresh()
    for code, seconds, expected in [(1384, -90, True), (3384, -30, False),
                                    (1384, -60, False), (3384, -30, False), (1384, -10, True)]:
        await coordinator.async_apply_message("hub:event", detector_event(code, seconds))
        assert status(coordinator) is expected
    await coordinator.async_apply_message("hub:event", detector_event(3384, -10))
    assert status(coordinator) is None  # Conflicting equal timestamps have no known order.
    await coordinator.async_apply_message("hub:event", detector_event(3384, -5))
    assert status(coordinator) is False
    await coordinator.async_apply_message("hub:event", detector_event(1384, device="smoke"))
    await coordinator.async_apply_message("hub:event", detector_event(3384, -1))
    assert status(coordinator, device="smoke") is True


@pytest.mark.parametrize("timestamp", [None, "invalid", "2026-11-14T12:00:00", "9999-12-31T23:59:59-12:00", "2027-01-01T00:00:00Z"])
async def test_unorderable_event_is_unknown(coordinator, timestamp):
    await coordinator.async_refresh()
    await coordinator.async_apply_message("hub:event", detector_event(1384))
    event = detector_event(3384)
    event["happenedAt"] = timestamp
    await coordinator.async_apply_message("hub:event", event)
    assert status(coordinator) is None
    await coordinator.async_apply_message("hub:event", detector_event(1384, -1))
    assert status(coordinator) is None


@pytest.mark.parametrize("events,complete", [
    ([], True), ([detector_event(1384)], False),
    ([detector_event(1384, -8 * 86400)], True),
    ([detector_event(1384, 1)], True),
    ([detector_event(1384, -90), detector_event(3384, -30)], True),
    ([{"code": 1384, "subject": {"id": "heat"}}], True),
])
async def test_partial_empty_or_invalid_history_is_unknown(coordinator, events, complete):
    coordinator.client.activity.return_value = (events, complete)
    await coordinator.async_refresh()
    assert status(coordinator) is None


async def test_history_latest_recovery_and_ambiguous_recovery(coordinator):
    coordinator.client.activity.return_value = ([detector_event(3384, -20), detector_event(1384, -60)], True)
    await coordinator.async_refresh()
    assert status(coordinator) is False
    await coordinator.async_apply_message("hub:event", record(1384, -10))
    assert status(coordinator) is None
    await coordinator.async_apply_message("hub:event", detector_event(3384, -5))
    assert status(coordinator) is False


async def test_disconnect_reconnect_gap_and_rate_budget(coordinator, freezer):
    coordinator.client.activity.return_value = ([detector_event(1384)], True)
    await coordinator.async_refresh()
    assert status(coordinator) is True
    coordinator.set_websocket_connected(True)
    coordinator.set_websocket_connected(False)
    assert status(coordinator) is None
    coordinator.set_websocket_connected(True)
    coordinator.request_alarm_reconciliation()
    await coordinator.async_refresh()
    assert coordinator.client.activity.await_count == 1
    assert status(coordinator) is None
    freezer.tick(timedelta(minutes=5))
    coordinator.client.activity.return_value = ([detector_event(3384, 60), detector_event(1384)], True)
    await coordinator.async_refresh()
    assert status(coordinator) is False
    await coordinator.async_apply_message("hub:event", detector_event(1384, -10))
    assert status(coordinator) is False


async def test_reload_checkpoint_requires_complete_bridge(coordinator, hass, freezer):
    coordinator.client.activity.return_value = ([detector_event(1384)], True)
    await coordinator.async_refresh()
    coordinator.set_websocket_connected(False)
    reloaded = LiviSenseCoordinator(hass, coordinator.client)
    reloaded.faults = coordinator.faults
    await reloaded.async_refresh()
    assert status(reloaded) is None
    freezer.tick(timedelta(minutes=5))
    reloaded.client.activity.return_value = ([], False)
    await reloaded.async_refresh()
    assert status(reloaded) is None
    freezer.tick(timedelta(minutes=5))
    reloaded.client.activity.return_value = ([detector_event(1384)], True)
    await reloaded.async_refresh()
    assert status(reloaded) is True


async def test_expired_checkpoint_not_carried_through_empty_window(coordinator, freezer):
    coordinator.client.activity.return_value = ([detector_event(1384)], True)
    await coordinator.async_refresh()
    coordinator.set_websocket_connected(False)
    freezer.tick(timedelta(days=8))
    coordinator.client.activity.return_value = ([], True)
    await coordinator.async_refresh()
    assert status(coordinator) is None


async def test_trouble_has_no_invented_recovery_or_hub_faults(coordinator):
    await coordinator.async_refresh()
    for code in (1302, 3302, 1344, 3380, 3389, 1604):
        await coordinator.async_apply_message("hub:event", detector_event(code))
    assert status(coordinator) is None
    assert status(coordinator, "trouble") is None
    await coordinator.async_apply_message("hub:event", detector_event(1389))
    for code in (3380, 3389, 1604, 3384):
        await coordinator.async_apply_message("hub:event", detector_event(code, -10))
    assert status(coordinator, "trouble") is True
    coordinator.set_websocket_connected(False)
    assert status(coordinator, "trouble") is True


async def test_null_fields_and_failed_optional_recovery(coordinator):
    coordinator.client.activity.side_effect = ApiError("Cloud unavailable")
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert status(coordinator) is None
    await coordinator.async_apply_message("hub:event", detector_event(1384))
    await coordinator.async_apply_message("device:state", {"hub": {"id": "hub"},
        "device": {"id": "heat"}, "state": {"warning": None, "batteryLevel": 100, "signalStrength": 50}})
    assert status(coordinator) is True
    assert status(coordinator, "signal") is None
    coordinator.last_update_success = False
    assert not DetectorFlag(coordinator, "hub", "heat", "battery").available


async def test_live_replay_before_stream_cannot_establish_current_state(coordinator):
    await coordinator.async_refresh()
    coordinator._fault_stream_started = NOW
    await coordinator.async_apply_message("hub:event", detector_event(1384, -30))
    assert status(coordinator) is None
    await coordinator.async_apply_message("hub:event", detector_event(1384, 0))
    assert status(coordinator) is True


async def test_rest_never_overwrites_newer_observed_transition(coordinator, freezer):
    await coordinator.async_refresh()
    await coordinator.async_apply_message("hub:event", detector_event(3384, -10))
    freezer.tick(timedelta(minutes=5))
    coordinator.client.activity.return_value = ([detector_event(1384, -60)], True)
    await coordinator.async_refresh()
    assert status(coordinator) is False


async def test_checkpoint_carries_old_fault_through_complete_empty_suffix(coordinator, freezer):
    coordinator.client.activity.return_value = ([detector_event(1384, -7 * 86400 + 60)], True)
    await coordinator.async_refresh()
    assert status(coordinator) is True
    coordinator.set_websocket_connected(False)
    freezer.tick(timedelta(minutes=5))
    coordinator.client.activity.return_value = ([], True)
    await coordinator.async_refresh()
    assert status(coordinator) is True


async def test_unattributed_recovery_invalidates_old_fault_without_clearing_others(coordinator):
    await coordinator.async_refresh()
    await coordinator.async_apply_message("hub:event", detector_event(1384, -90))
    await coordinator.async_apply_message("hub:event", record(3384, -30))
    assert status(coordinator) is None
    assert status(coordinator, device="smoke") is None


async def test_fresh_live_transition_resolves_only_its_fault_after_gap(coordinator, freezer):
    await coordinator.async_refresh()
    await coordinator.async_apply_message("hub:event", detector_event(1384))
    await coordinator.async_apply_message("hub:event", detector_event(1381))
    coordinator.set_websocket_connected(False)
    assert status(coordinator) is None
    coordinator.set_websocket_connected(True)
    freezer.tick(timedelta(seconds=1))
    await coordinator.async_apply_message("hub:event", detector_event(3384, 1))
    assert status(coordinator) is False
    assert status(coordinator, "signal") is None
    assert status(coordinator, device="smoke") is None


@pytest.mark.parametrize("events", [
    [detector_event(1384), detector_event(1384)],
    [{**detector_event(1384), "hub": {"id": "other"}}],
    [{**detector_event(1384), "id": "same"}, {**detector_event(3384, -90), "id": "same"}],
])
async def test_duplicate_pages_and_cross_hub_history_are_incomplete(coordinator, events):
    coordinator.client.activity.return_value = (events, True)
    await coordinator.async_refresh()
    assert status(coordinator) is None


@pytest.mark.parametrize("source", ["rest", "websocket"])
async def test_hub_offline_gap_is_recorded_even_inside_activity_budget(coordinator, source):
    await coordinator.async_refresh()
    await coordinator.async_apply_message("hub:event", detector_event(1384))
    assert status(coordinator) is True
    if source == "rest":
        coordinator.client.hub.return_value["state"]["status"] = "offline"
        await coordinator.async_refresh()
    else:
        await coordinator.async_apply_message("hub:state", {"hub": {"id": "hub", "state": {"status": "offline"}}})
    assert not DetectorFlag(coordinator, "hub", "heat", "battery").available
    await coordinator.async_apply_message("hub:state", {"hub": {"id": "hub", "state": {"status": "online"}}})
    assert status(coordinator) is None
    assert coordinator.client.activity.await_count == 1
