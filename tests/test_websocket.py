"""Mocked realtime state tests."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.cavius_livisense.api import AuthError
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


@pytest.mark.asyncio
async def test_reception_and_reconnect():
    client = MagicMock()
    client.access_token = AsyncMock(return_value="token")
    coordinator = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    coordinator.async_apply_message = AsyncMock()
    sockets = []

    class Socket:
        closed = False

        def __init__(self):
            self.iterations = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.iterations:
                raise StopAsyncIteration
            self.iterations += 1
            return aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, '{"name":"hub:state","data":{"hub":{"id":"hub","state":{"status":"offline"}}}}', None)

    class Connection:
        async def __aenter__(self):
            ws = Socket()
            sockets.append(ws)
            return ws

        async def __aexit__(self, *args):
            return False

    client.session.ws_connect.side_effect = lambda *args, **kwargs: Connection()
    listener = LiviSenseWebSocket(client, coordinator)
    original_sleep = __import__("asyncio").sleep

    async def short_sleep(_delay):
        await original_sleep(0)

    with patch("custom_components.cavius_livisense.websocket.asyncio.sleep", side_effect=short_sleep):
        listener.start()
        for _ in range(100):
            if len(sockets) >= 2:
                break
            await original_sleep(0)
        await listener.stop()
    assert len(sockets) >= 2
    assert coordinator.async_apply_message.await_count >= 2
    assert coordinator.async_request_refresh.await_count >= 2


@pytest.mark.asyncio
async def test_auth_failure_requests_reauth_once():
    client = MagicMock()
    client.access_token = AsyncMock(side_effect=AuthError("rejected"))
    coordinator = MagicMock()
    entry = MagicMock()
    listener = LiviSenseWebSocket(client, coordinator, entry)
    await listener._run()
    entry.async_start_reauth.assert_called_once_with(coordinator.hass)
    client.access_token.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("refresh_path", ["scheduled", "manual"])
async def test_refresh_and_merge_are_serialized(hass, refresh_path):
    client = MagicMock()
    coordinator = LiviSenseCoordinator(hass, client)
    coordinator.async_set_updated_data({"hub": {"hub": {"id": "hub", "state": {"status": "offline"}, "devices": []}}})
    entered = asyncio.Event()
    release = asyncio.Event()

    client.profile = AsyncMock(return_value={"hubIds": ["hub"]})

    async def hub_snapshot(_hub_id):
        entered.set()
        await release.wait()
        return {"data": {"id": "hub", "state": {"status": "offline"}, "devices": []}}

    client.hub = AsyncMock(side_effect=hub_snapshot)
    refresh = asyncio.create_task(coordinator._async_refresh(scheduled=True) if refresh_path == "scheduled" else coordinator.async_refresh())
    await entered.wait()
    merge = asyncio.create_task(coordinator.async_apply_message("hub:state", {"hub": {"id": "hub", "state": {"status": "online"}}}))
    await asyncio.sleep(0)
    assert not merge.done()
    release.set()
    await asyncio.gather(refresh, merge)
    assert coordinator.data["hub"]["hub"]["state"]["status"] == "online"

    coordinator.async_request_refresh = AsyncMock()
    await asyncio.wait_for(coordinator.async_apply_message("device:state", {"hub": {"id": "hub"}, "device": {"id": "missing"}, "state": {"updatedAt": 2}}), 1)
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_websocket_first_then_rest_and_refresh_request(hass):
    client = MagicMock()
    client.profile = AsyncMock(return_value={"hubIds": ["hub"]})
    client.hub = AsyncMock(return_value={"data": {"id": "hub", "state": {"status": "offline"}, "devices": []}})
    coordinator = LiviSenseCoordinator(hass, client)
    coordinator.async_set_updated_data({"hub": {"hub": {"id": "hub", "state": {"status": "online"}, "devices": []}}})
    await coordinator.async_apply_message("hub:updated", {"hub": {"id": "hub"}})
    await coordinator.async_refresh()
    assert coordinator.data["hub"]["hub"]["state"]["status"] == "offline"
    client.hub.assert_awaited()
