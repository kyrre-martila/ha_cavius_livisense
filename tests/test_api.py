"""Network-free tests for documented REST behavior."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "livisense_api", Path(__file__).resolve().parents[1] / "custom_components/cavius_livisense/api.py"
)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class Response:
    def __init__(self, status, data):
        self.status = status
        self.data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self):
        return self.data


class Session:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return Response(*next(self.replies))


def login_response():
    return {
        "token": {
            "accessToken": "secret-token",
            "refreshToken": "unused",
            "expiresIn": 604800,
        },
        "user": {"id": "account-uuid", "email": "test@example.com"},
    }


@pytest.mark.asyncio
async def test_login_success():
    session = Session([(200, login_response())])
    client = api.LiviSenseClient(session, "test@example.com", "password")
    assert (await client.login())["id"] == "account-uuid"
    assert session.calls[0][0] == "POST"
    assert session.calls[0][1].endswith("/auth/user/login")
    assert session.calls[0][2]["json"] == {"email": "test@example.com", "password": "password"}


@pytest.mark.asyncio
async def test_invalid_credentials():
    client = api.LiviSenseClient(Session([(401, {"message": "rejected"})]), "a", "b")
    with pytest.raises(api.AuthError):
        await client.login()


@pytest.mark.asyncio
async def test_expired_token_reauthenticates():
    session = Session([(200, login_response()), (200, login_response()), (200, {"hubIds": []})])
    client = api.LiviSenseClient(session, "a", "b")
    await client.login()
    client._expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert await client.profile() == {"hubIds": []}
    assert [call[0] for call in session.calls] == ["POST", "POST", "GET"]


@pytest.mark.asyncio
async def test_401_retries_once():
    session = Session([(200, login_response()), (401, {}), (200, login_response()), (200, {"hubIds": []})])
    client = api.LiviSenseClient(session, "a", "b")
    assert await client.profile() == {"hubIds": []}
    assert [call[0] for call in session.calls] == ["POST", "GET", "POST", "GET"]


@pytest.mark.asyncio
async def test_cloud_failure():
    client = api.LiviSenseClient(Session([(503, {})]), "a", "b")
    with pytest.raises(api.ConnectionError):
        await client.login()


@pytest.mark.asyncio
async def test_missing_optional_fields():
    response = login_response()
    del response["token"]["refreshToken"]
    client = api.LiviSenseClient(Session([(200, response)]), "a", "b")
    assert (await client.login())["id"] == "account-uuid"


@pytest.mark.asyncio
async def test_multiple_hubs_and_smoke_detector_discovery():
    hub_a = {"data": {"id": "hub-a", "state": {"status": "online"}, "devices": [
        {"id": "detector-a", "model": "SMOKECAVIUS", "name": "Kitchen", "state": {"updatedAt": 100}}
    ]}}
    hub_b = {"data": {"id": "hub-b", "state": {"status": "offline"}, "devices": []}}
    session = Session([
        (200, login_response()),
        (200, {"hubIds": ["hub-a", "hub-b"]}),
        (200, {"data": [{"id": "hub-a"}, {"id": "hub-b"}]}),
        (200, hub_a),
        (200, hub_b),
    ])
    client = api.LiviSenseClient(session, "a", "b")
    profile = await client.profile()
    assert profile["hubIds"] == ["hub-a", "hub-b"]
    assert len((await client.hubs())["data"]) == 2
    first = await client.hub("hub-a")
    second = await client.hub("hub-b")
    assert first["data"]["devices"][0]["model"] == "SMOKECAVIUS"
    assert second["data"]["state"]["status"] == "offline"
    assert [call[1].split("livi.cloud")[-1] for call in session.calls[1:]] == [
        "/auth/user/profile", "/api/hubs?perPage=1000", "/api/hubs/hub-a", "/api/hubs/hub-b"
    ]


@pytest.mark.asyncio
async def test_malformed_login_response():
    client = api.LiviSenseClient(Session([(200, {"token": {}, "user": {}})]), "a", "b")
    with pytest.raises(api.ApiError):
        await client.login()



def activity_response(data, **meta):
    return {"data": data, "meta": {"from": 100, "until": 200, "sort": "desc", "hints": {}, **meta}}


async def test_activity_accepts_the_documented_response_without_completeness_claims():
    import json

    # Copied from API notes, with original placeholders; not a live fault capture.
    response = json.loads((Path(__file__).parent / "fixtures/activity_documented.json").read_text())
    session = Session([(200, login_response()), (200, response)])
    meta = response["meta"]
    events = await api.LiviSenseClient(session, "a", "b").activity("hub/id", meta["from"], meta["until"])
    assert events == response["data"]
    assert session.calls[-1][1].endswith("/api/hubs/hub%2Fid/activity")
    assert session.calls[-1][2]["params"] == {"from": meta["from"], "until": meta["until"]}
    assert len(session.calls) == 2


@pytest.mark.parametrize("response", [
    activity_response([], sort="asc"), activity_response([], until=199),
    activity_response([None]), {"data": [], "meta": None},
    activity_response([], **{"from": "100"}),
])
async def test_activity_rejects_malformed_window(response):
    session = Session([(200, login_response()), (200, response)])
    with pytest.raises(api.ApiError):
        await api.LiviSenseClient(session, "a", "b").activity("hub", 100, 200)


@pytest.mark.parametrize("meta", [{}, {"total": 1}, {"currentPage": 1, "lastPage": 2},
                                  {"hints": {"next": "opaque"}}])
async def test_activity_retains_observations_without_guessing_pages(meta):
    response = activity_response([{"code": 1384}], **meta)
    session = Session([(200, login_response()), (200, response)])
    assert await api.LiviSenseClient(session, "a", "b").activity("hub", 100, 200) == response["data"]
    assert len(session.calls) == 2


async def test_activity_processing_bound_and_rate_limit_stop():
    response = activity_response([{}] * 301)
    session = Session([(200, login_response()), (200, response)])
    assert len(await api.LiviSenseClient(session, "a", "b").activity("hub", 100, 200)) == 300
    assert len(session.calls) == 2
    session = Session([(200, login_response()), (429, {})])
    with pytest.raises(api.ConnectionError):
        await api.LiviSenseClient(session, "a", "b").activity("hub", 100, 200)
    assert len(session.calls) == 2


@pytest.mark.parametrize("start,end", [(0, 604801), (100, 100), (200, 100)])
async def test_activity_rejects_oversized_or_invalid_window(start, end):
    session = Session([])
    with pytest.raises(ValueError):
        await api.LiviSenseClient(session, "a", "b").activity("hub", start, end)
    assert not session.calls
