"""Issue #5 regression coverage with mocked cloud responses, never devices."""
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.cavius_livisense.coordinator import LiviSenseCoordinator
from custom_components.cavius_livisense.sensor import (
    LastFireDrill,
    LastTested,
    LastTestResult,
)


@pytest.fixture
def coordinator(hass):
    client = MagicMock()
    client.activity = AsyncMock(return_value=([], False))
    client.profile = AsyncMock(return_value={"hubIds": ["hub"]})
    hub = {"id": "hub", "state": {"status": "online"}, "extras": None,
           "devices": [{"id": key, "model": model, "state": {"alarm": None}}
                       for key, model in [("smoke", "SMOKECAVIUS"), ("heat", "HEATCAVIUS")]]}
    client.hub = AsyncMock(side_effect=lambda _: deepcopy(hub))
    client.unacknowledged_alarms = AsyncMock(return_value=[])
    return LiviSenseCoordinator(hass, client)


def event(code, target="hub"):
    return {"hub": {"id": "hub"}, "event": {"code": code}, "subject": {"id": target}}


@pytest.mark.parametrize("code", [1119, 3119, 9811, 9812, 9411, 9412, 9413, 9421, 9422,
                                  1130, 1137, 1384, 1604, 9996])
async def test_non_fire_startup_recovery(coordinator, code):
    coordinator.client.unacknowledged_alarms.return_value = [event(code)]
    await coordinator.async_refresh()
    assert all(coordinator.alarm_state("hub", key) is False for key in ("smoke", "heat"))


@pytest.mark.parametrize("record", [{"code": 9999}, {"event": {"code": "unknown"}}, {},
                                    {"event": None}, {"code": None}, {"code": 1111.5},
                                    {"code": True}])
async def test_unclassified_rest_is_unknown_but_never_masks_fire(coordinator, record):
    coordinator.client.unacknowledged_alarms.return_value = [record]
    await coordinator.async_refresh()
    assert coordinator.alarm_state("hub", "smoke") is None
    await coordinator.async_apply_message("hub:event", event(1111))
    assert coordinator.alarm_state("hub", "smoke") is True
    assert coordinator.alarm_state("hub", "heat") is True


@pytest.mark.parametrize("code", [1110, "1111"])
@pytest.mark.parametrize("nested", [True, False])
async def test_rest_fire_survives_acknowledgement_and_snapshot(coordinator, code, nested):
    record = event(code) if nested else {"code": code}
    coordinator.client.unacknowledged_alarms.return_value = [record]
    await coordinator.async_refresh()
    assert coordinator.alarm_state("hub", "smoke") is True
    await coordinator.async_apply_message("hub:event", event(9411))
    coordinator.client.unacknowledged_alarms.return_value = []
    coordinator.request_alarm_reconciliation()
    await coordinator.async_refresh()
    assert coordinator.alarm_state("hub", "smoke") is True
    assert coordinator.alarm_state("hub", "heat") is True
    await coordinator.async_apply_message("hub:event", event(9422))
    assert coordinator.alarm_state("hub", "smoke") is False
    coordinator.request_alarm_reconciliation()
    await coordinator.async_refresh()
    assert coordinator.alarm_state("hub", "heat") is False
    await coordinator.async_apply_message("hub:event", event(1110, "heat"))
    assert coordinator.alarm_state("hub", "heat") is True
    assert coordinator.alarm_state("hub", "smoke") is False


async def test_websocket_lifecycle_classification_and_fire(coordinator):
    await coordinator.async_refresh()
    for code, expected in [(9811, "planned"), (1119, "active"), (3119, "ended"),
                           (9812, "cancelled"), (9812, "cancelled")]:
        await coordinator.async_apply_message("hub:event", event(code))
        assert coordinator.drill_states["hub"] == expected
        assert coordinator.alarm_state("hub", "smoke") is False
    await coordinator.async_apply_message("hub:event", event(1111))
    for code in [1119, 9411, 9412, 9413, 3119, 9812]:
        await coordinator.async_apply_message("hub:event", event(code))
        assert coordinator.alarm_state("hub", "smoke") is True
        assert coordinator.alarm_state("hub", "heat") is True


@pytest.mark.parametrize("value", [{"code": 1119}, {"event": {"code": "1119"}},
                                   {"code": 3119}])
async def test_explicit_drill_state_rest_and_websocket(coordinator, value):
    hub = await coordinator.client.hub("hub")
    hub["devices"][0]["state"]["alarm"] = value
    coordinator.client.hub.side_effect = None
    coordinator.client.hub.return_value = hub
    await coordinator.async_refresh()
    assert coordinator.alarm_state("hub", "smoke") is False
    await coordinator.async_apply_message("device:state", {
        "hub": {"id": "hub"}, "device": {"id": "heat"}, "state": {"alarm": value}})
    assert coordinator.alarm_state("hub", "heat") is False
    await coordinator.async_apply_message("hub:event", event(1110))
    assert coordinator.alarm_state("hub", "smoke") is True
    assert coordinator.alarm_state("hub", "heat") is True


@pytest.mark.parametrize("value", ["fire", "drill", {"type": "drill"}, {"code": 9999}, True])
async def test_opaque_state_safe_fallback_during_drill(coordinator, value):
    await coordinator.async_refresh()
    await coordinator.async_apply_message("hub:event", event(1119))
    await coordinator.async_apply_message("device:state", {
        "hub": {"id": "hub"}, "device": {"id": "smoke"}, "state": {"alarm": value}})
    assert coordinator.alarm_state("hub", "smoke") is True
    assert coordinator.alarm_state("hub", "heat") is False


@pytest.mark.parametrize("value", [None, "", "invalid", "2026-01-01", "2026-01-01T10:00:00",
                                   "0001-01-01T00:00:00+02:00", "9999-12-31T23:59:59-02:00",
                                   123, True, {}])
async def test_diagnostic_invalid_and_missing_values(coordinator, value):
    await coordinator.async_refresh()
    hub = coordinator.data["hub"]["hub"]
    hub["extras"] = {"lastFireDrillAt": value}
    hub["devices"][0].update(lastTestedAt=value, extras=None)
    entities = [LastFireDrill(coordinator, "hub"), LastTested(coordinator, "hub", "smoke"),
                LastTestResult(coordinator, "hub", "smoke")]
    assert all(entity.native_value is None for entity in entities)
    assert all(entity.available for entity in entities)
    hub["devices"] = []
    assert not entities[1].available
    assert not entities[2].available
    hub["state"] = None
    assert not entities[0].available


async def test_diagnostic_valid_timestamps_and_failures(coordinator):
    await coordinator.async_refresh()
    hub = coordinator.data["hub"]["hub"]
    hub["extras"] = {"lastFireDrillAt": "2026-01-01T12:00:00+02:00"}
    hub["devices"][0].update(lastTestedAt="2026-01-01T10:00:00Z",
                              extras={"lastTestResult": "passed"})
    drill = LastFireDrill(coordinator, "hub")
    tested = LastTested(coordinator, "hub", "smoke")
    result = LastTestResult(coordinator, "hub", "smoke")
    assert drill.native_value == tested.native_value
    assert drill.native_value.utcoffset().total_seconds() == 0
    assert result.native_value == "passed"
    for extras in [None, {}, {"lastTestResult": None}, {"lastTestResult": {}}]:
        hub["devices"][0]["extras"] = extras
        assert result.native_value is None
    coordinator.last_update_success = False
    assert not tested.available


async def test_websocket_drill_frames_reach_coordinator(coordinator):
    """Exercise JSON frame decoding and routing, not just the event helper."""
    import asyncio
    import json
    from unittest.mock import patch

    import aiohttp

    from custom_components.cavius_livisense.websocket import LiviSenseWebSocket

    await coordinator.async_refresh()
    coordinator.client.access_token = AsyncMock(return_value="mock-token")
    received = asyncio.Event()
    frames = iter([9811, 1119, 9812, 9812, 9411])

    class Socket:
        closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            code = next(frames, None)
            if code is None:
                received.set()
                await asyncio.Event().wait()
            return aiohttp.WSMessage(aiohttp.WSMsgType.TEXT,
                                     json.dumps({"name": "hub:event", "data": event(code)}), None)

    connection = MagicMock()
    connection.__aenter__ = AsyncMock(return_value=Socket())
    connection.__aexit__ = AsyncMock(return_value=False)
    coordinator.client.session.ws_connect.return_value = connection
    listener = LiviSenseWebSocket(coordinator.client, coordinator)
    with patch.object(coordinator, "async_request_refresh", new_callable=AsyncMock):
        listener.start()
        try:
            await asyncio.wait_for(received.wait(), 1)
            assert coordinator.drill_states["hub"] == "cancelled"
            assert len(coordinator.events) == 5
            assert coordinator.alarm_state("hub", "smoke") is False
            assert coordinator.alarm_state("hub", "heat") is False
        finally:
            await listener.stop()

