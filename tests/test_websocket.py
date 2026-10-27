"""Mocked realtime state tests."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.cavius_livisense.coordinator import LiviSenseCoordinator
from custom_components.cavius_livisense.websocket import LiviSenseWebSocket


@pytest.mark.asyncio
async def test_state_merge_and_dispatch(hass):
    coordinator = LiviSenseCoordinator(hass, MagicMock())
    coordinator.async_set_updated_data({"hub": {"hub": {
        "id": "hub", "state": {"status": "offline", "mode": "disarm"},
        "devices": [{"id": "device", "state": {"warning": None, "updatedAt": 1}}],
    }}})
    await coordinator.async_apply_message("hub:state", {"hub": {"id": "hub", "state": {"status": "online", "undocumented": True}}})
    await coordinator.async_apply_message("device:state", {"hub": {"id": "hub"}, "device": {"id": "device"}, "state": {"updatedAt": 2}})
    assert coordinator.data["hub"]["hub"]["state"] == {"status": "online", "mode": "disarm"}
    assert coordinator.data["hub"]["hub"]["devices"][0]["state"] == {"warning": None, "updatedAt": 2}
    await coordinator.async_apply_message("hub:event", {"event": {"code": 1604}})
    assert coordinator.events == [{"event": {"code": 1604}}]
    coordinator.async_request_refresh = AsyncMock()
    await coordinator.async_apply_message("device:updated", {"hub": {"id": "hub"}, "device": {"id": "device"}})
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_keepalive_and_clean_stop():
    client = MagicMock()
    coordinator = MagicMock()
    listener = LiviSenseWebSocket(client, coordinator)
    ws = MagicMock(closed=False)
    ws.send_json = AsyncMock(side_effect=RuntimeError("sent"))
    with patch("custom_components.cavius_livisense.websocket.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(RuntimeError, match="sent"):
            await listener._keepalive(ws)
    ws.send_json.assert_awaited_once_with({"ping": 1})
    listener.start()
    await listener.stop()
