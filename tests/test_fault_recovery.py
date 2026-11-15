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
    client.activity = AsyncMock(return_value=[])
    result = LiviSenseCoordinator(hass, client)
    result._fault_stream_started = NOW - timedelta(days=1)
    return result


def status(coordinator, key="battery", device="heat"):
    return DetectorFlag(coordinator, "hub", device, key).is_on


@pytest.mark.parametrize("code,key,value", [(1384, "battery", True), (3384, "battery", False),
    (1381, "signal", True), (3381, "signal", False), (1380, "trouble", True), (1389, "trouble", True)])
async def test_startup_observations_without_completeness(coordinator, code, key, value):
    coordinator.client.activity.return_value = [detector_event(code)]
    await coordinator.async_refresh()
    assert status(coordinator, key) is (value if key == "trouble" else None)
    assert DetectorFlag(coordinator, "hub", "heat", key).extra_state_attributes["last_observed_event_code"] == code
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


@pytest.mark.parametrize("events", [
    [], [detector_event(1384)], [detector_event(1384, -8 * 86400)],
    [detector_event(1384, 1)],
    [detector_event(1384, -90), detector_event(3384, -30)],
    [{"code": 1384, "subject": {"id": "heat"}}],
])
async def test_partial_empty_or_invalid_history_is_unknown(coordinator, events):
    coordinator.client.activity.return_value = events
    await coordinator.async_refresh()
    assert status(coordinator) is None


async def test_history_latest_recovery_and_ambiguous_recovery(coordinator):
    coordinator.client.activity.return_value = [detector_event(3384, -20), detector_event(1384, -60)]
    await coordinator.async_refresh()
    assert status(coordinator) is None  # The list may omit a newer low event.
    await coordinator.async_apply_message("hub:event", record(1384, -10))
    assert status(coordinator) is None
    await coordinator.async_apply_message("hub:event", detector_event(3384, -5))
    assert status(coordinator) is False


async def test_disconnect_reconnect_gap_and_rate_budget(coordinator, freezer):
    coordinator.client.activity.return_value = [detector_event(1384)]
    await coordinator.async_refresh()
    await coordinator.async_apply_message("hub:event", detector_event(1384, -30))
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
    coordinator.client.activity.return_value = [detector_event(3384, 60), detector_event(1384)]
    await coordinator.async_refresh()
    assert status(coordinator) is None  # Partial history cannot close a gap.
    await coordinator.async_apply_message("hub:event", detector_event(3384, 300))
    assert status(coordinator) is False
    await coordinator.async_apply_message("hub:event", detector_event(1384, -10))
    assert status(coordinator) is False


async def test_reload_retains_observations_but_never_stale_healthy_state(coordinator, hass, freezer):
    await coordinator.async_refresh()
    await coordinator.async_apply_message("hub:event", detector_event(3384))
    assert status(coordinator) is False
    coordinator.set_websocket_connected(False)
    reloaded = LiviSenseCoordinator(hass, coordinator.client)
    reloaded.faults = coordinator.faults
    await reloaded.async_refresh()
    assert status(reloaded) is None
    freezer.tick(timedelta(minutes=5))
    reloaded.client.activity.return_value = [detector_event(3384)]
    await reloaded.async_refresh()
    assert status(reloaded) is None
    assert reloaded.faults.attributes("hub", "heat", "battery")["last_observed_event_code"] == 3384
    await reloaded.async_apply_message("hub:event", detector_event(1384, 300))
    assert status(reloaded) is True


async def test_old_observation_and_empty_window_do_not_restore_state(coordinator, freezer):
    await coordinator.async_refresh()
    await coordinator.async_apply_message("hub:event", detector_event(1384))
    assert status(coordinator) is True
    coordinator.set_websocket_connected(False)
    freezer.tick(timedelta(days=8))
    coordinator.client.activity.return_value = []
    await coordinator.async_refresh()
    assert status(coordinator) is None
    assert coordinator.faults.attributes("hub", "heat", "battery")["last_observed_event_code"] == 1384


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
    coordinator.client.activity.return_value = [detector_event(1384, -60)]
    await coordinator.async_refresh()
    assert status(coordinator) is False


async def test_empty_history_never_closes_a_delivery_gap(coordinator, freezer):
    await coordinator.async_refresh()
    await coordinator.async_apply_message("hub:event", detector_event(1384))
    assert status(coordinator) is True
    coordinator.set_websocket_connected(False)
    freezer.tick(timedelta(minutes=5))
    coordinator.client.activity.return_value = []
    await coordinator.async_refresh()
    assert status(coordinator) is None


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
    coordinator.client.activity.return_value = events
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


@pytest.mark.parametrize("code,key", [(1384, "battery"), (3384, "battery"),
                                      (1381, "signal"), (3381, "signal")])
async def test_delayed_preconnection_delivery_is_retained_as_unconfirmed_evidence(coordinator, code, key):
    await coordinator.async_refresh()
    coordinator.set_websocket_connected(True)  # Connection boundary is NOW.
    delayed = detector_event(code, -1)  # Generated just before connection, not a guessed grace period.
    await coordinator.async_apply_message("hub:event", delayed)
    entity = DetectorFlag(coordinator, "hub", "heat", key)
    assert entity.is_on is None  # Delayed delivery and replay cannot yet be distinguished.
    assert entity.extra_state_attributes == {
        "last_observed_event_at": (NOW - timedelta(seconds=1)).isoformat(),
        "last_observed_event_code": code,
        "last_observed_event_source": "websocket",
        "current_status_confirmed": False,
    }
    assert coordinator.faults.records[("hub", "heat", key)][0] == NOW - timedelta(seconds=1)
    # A newer event generated in this stream establishes current state.
    await coordinator.async_apply_message("hub:event", detector_event(code, 0))
    assert entity.is_on is (code in (1384, 1381))
    assert entity.extra_state_attributes["current_status_confirmed"]


@pytest.mark.parametrize("code,old_code,key", [(1384, 3384, "battery"), (3384, 1384, "battery"),
                                               (1381, 3381, "signal"), (3381, 1381, "signal")])
async def test_delayed_stale_and_duplicate_events_never_replace_newer_current_evidence(coordinator, code, old_code, key):
    await coordinator.async_refresh()
    coordinator.set_websocket_connected(True)
    await coordinator.async_apply_message("hub:event", detector_event(code, 0))
    entity = DetectorFlag(coordinator, "hub", "heat", key)
    for event in (detector_event(old_code, -1), detector_event(code, 0), detector_event(old_code, -86400)):
        await coordinator.async_apply_message("hub:event", event)
        assert entity.is_on is (code in (1384, 1381))
        assert entity.extra_state_attributes["last_observed_event_code"] == code


async def test_delayed_newer_event_in_gap_invalidates_old_healthy_state(coordinator, freezer):
    await coordinator.async_refresh()
    await coordinator.async_apply_message("hub:event", detector_event(3384, -60))
    assert status(coordinator) is False
    coordinator.set_websocket_connected(False)
    freezer.tick(timedelta(seconds=10))
    coordinator.set_websocket_connected(True)
    await coordinator.async_apply_message("hub:event", detector_event(1384, 5))
    assert status(coordinator) is None
    assert coordinator.faults.attributes("hub", "heat", "battery")["last_observed_event_code"] == 1384
    await coordinator.async_apply_message("hub:event", detector_event(3384, -60))
    assert status(coordinator) is None
    freezer.tick(timedelta(seconds=1))
    await coordinator.async_apply_message("hub:event", detector_event(1384, 11))
    assert status(coordinator) is True


async def test_duplicate_recovery_does_not_reconfirm_state_after_gap(coordinator):
    await coordinator.async_refresh()
    recovery = detector_event(3384, 0)
    await coordinator.async_apply_message("hub:event", recovery)
    assert status(coordinator) is False
    coordinator.set_websocket_connected(False)
    coordinator.set_websocket_connected(True)
    # Even if the reconnect has the same timestamp, a duplicate cannot heal a gap.
    await coordinator.async_apply_message("hub:event", recovery)
    assert status(coordinator) is None


@pytest.mark.parametrize("code", [1384, 3384])
async def test_documented_activity_shape_does_not_prove_latest_fault_or_recovery(coordinator, code):
    from custom_components.cavius_livisense.api import LiviSenseClient

    # Synthetic detector event in the documented envelope. No count/page fields.
    response = {"data": [{"code": code, "subject": {"id": "heat"},
                          "happenedAt": (NOW - timedelta(minutes=1)).isoformat()}],
                "meta": {"from": int(NOW.timestamp()) - 7 * 86400,
                         "until": int(NOW.timestamp()), "sort": "desc", "hints": {}}}
    api_client = LiviSenseClient(MagicMock(), "unused", "unused")
    api_client._request = AsyncMock(return_value=response)
    coordinator.client.activity = api_client.activity
    await coordinator.async_refresh()
    assert status(coordinator) is None  # A newer opposite transition could be omitted.
    attributes = coordinator.faults.attributes("hub", "heat", "battery")
    assert attributes["last_observed_event_code"] == code
    assert attributes["last_observed_event_source"] == "activity"
    assert not attributes["current_status_confirmed"]
    api_client._request.assert_awaited_once()


async def test_newer_partial_history_invalidates_older_live_healthy_value(coordinator, freezer):
    await coordinator.async_refresh()
    await coordinator.async_apply_message("hub:event", detector_event(3384, -60))
    assert status(coordinator) is False
    freezer.tick(timedelta(minutes=5))
    coordinator.client.activity.return_value = [detector_event(1384, 1)]
    await coordinator.async_refresh()
    assert status(coordinator) is None
    assert coordinator.faults.attributes("hub", "heat", "battery")["last_observed_event_code"] == 1384


async def test_full_restart_has_no_implicit_baseline(coordinator, hass):
    await coordinator.async_refresh()
    await coordinator.async_apply_message("hub:event", detector_event(3384))
    assert status(coordinator) is False
    restarted = LiviSenseCoordinator(hass, coordinator.client)
    await restarted.async_refresh()
    assert status(restarted) is None
    assert restarted.faults.records == {}
    assert restarted.faults.attributes("hub", "heat", "battery") == {}


async def test_hub_gap_delayed_delivery_is_not_confirmed_from_an_older_socket(coordinator, freezer):
    await coordinator.async_refresh()
    await coordinator.async_apply_message("hub:state", {"hub": {"id": "hub", "state": {"status": "offline"}}})
    freezer.tick(timedelta(seconds=10))
    await coordinator.async_apply_message("hub:state", {"hub": {"id": "hub", "state": {"status": "online"}}})
    await coordinator.async_apply_message("hub:event", detector_event(3384, 5))
    assert status(coordinator) is None
    assert coordinator.faults.attributes("hub", "heat", "battery")["last_observed_event_code"] == 3384


async def test_delayed_event_within_continuous_stream_updates_current_watermark(coordinator, freezer):
    await coordinator.async_refresh()
    coordinator.set_websocket_connected(True)
    await coordinator.async_apply_message("hub:event", detector_event(1384, 0))
    freezer.tick(timedelta(seconds=10))
    # A redundant status frame is not a new connection or a delivery gap.
    await coordinator.async_apply_message("hub:state", {"hub": {"id": "hub", "state": {"status": "online"}}})
    await coordinator.async_apply_message("hub:event", detector_event(3384, 5))
    assert status(coordinator) is False
    await coordinator.async_apply_message("hub:event", detector_event(1384, 1))
    assert status(coordinator) is False


async def test_post_disconnect_delivery_is_observation_until_connection_is_restored(coordinator, freezer):
    await coordinator.async_refresh()
    coordinator.set_websocket_connected(False)
    freezer.tick(timedelta(seconds=1))
    await coordinator.async_apply_message("hub:event", detector_event(3384, 1))
    assert status(coordinator) is None
    assert coordinator.faults.attributes("hub", "heat", "battery")["last_observed_event_code"] == 3384


async def test_first_live_delivery_of_current_activity_observation_can_confirm(coordinator):
    coordinator.client.activity.return_value = [detector_event(1384, 0)]
    await coordinator.async_refresh()
    assert status(coordinator) is None
    coordinator.set_websocket_connected(True)
    event = detector_event(1384, 0)
    await coordinator.async_apply_message("hub:event", event)
    assert status(coordinator) is True
    assert coordinator.faults.attributes("hub", "heat", "battery")["last_observed_event_source"] == "websocket"
    coordinator.set_websocket_connected(False)
    coordinator.set_websocket_connected(True)
    await coordinator.async_apply_message("hub:event", event)
    assert status(coordinator) is None  # Its second live delivery cannot close the gap.


@pytest.mark.parametrize("code,key", [(1384, "battery"), (3384, "battery"),
                                      (1381, "signal"), (3381, "signal")])
@pytest.mark.parametrize("condition", ["offline", "disconnected", "before_stream", "missing", "invalid", "future"])
async def test_first_live_delivery_of_activity_requires_current_stream_and_online_hub(coordinator, code, key, condition):
    coordinator.client.activity.return_value = [detector_event(code, 0)]
    await coordinator.async_refresh()
    coordinator.set_websocket_connected(True)
    event = detector_event(code, 0)
    if condition == "offline":
        await coordinator.async_apply_message("hub:state", {"hub": {"id": "hub", "state": {"status": "offline"}}})
    elif condition == "disconnected":
        coordinator.set_websocket_connected(False)
    elif condition == "before_stream":
        coordinator._fault_stream_started = NOW + timedelta(seconds=1)
    elif condition == "missing":
        event.pop("happenedAt")
    elif condition == "invalid":
        event["happenedAt"] = "not-a-timestamp"
    else:
        event["happenedAt"] = (NOW + timedelta(seconds=1)).isoformat()
    await coordinator.async_apply_message("hub:event", event)
    identity = ("hub", "heat", key)
    assert coordinator.faults.value(*identity) is None
    assert identity not in coordinator.faults.confirmed
    assert status(coordinator, key, "smoke") is None
