"""Small client for the documented LiviSense REST API."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

BASE_URL = "https://app.cavius.livi.cloud"

class ApiError(Exception):
    """Unexpected API response."""

class AuthError(ApiError):
    """Credentials were rejected."""

class ConnectionError(ApiError):
    """Cloud request failed."""

class LiviSenseClient:
    """Use Home Assistant's shared HTTP session."""

    def __init__(self, session: aiohttp.ClientSession, email: str, password: str) -> None:
        self.session = session
        self.email = email
        self.password = password
        self._token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at = datetime.min.replace(tzinfo=timezone.utc)
        self._auth_lock = asyncio.Lock()

    async def login(self) -> dict[str, Any]:
        async with self._auth_lock:
            data = await self._request("POST", "/auth/user/login", json={"email": self.email, "password": self.password}, authenticate=False)
            token = data.get("token")
            user = data.get("user")
            if not isinstance(token, dict) or not isinstance(token.get("accessToken"), str) or not token["accessToken"] or not isinstance(user, dict):
                raise ApiError("Malformed login response")
            self._token = token["accessToken"]
            refresh = token.get("refreshToken")
            self._refresh_token = refresh if isinstance(refresh, str) else None
            try:
                expiry = token.get("expiresAt")
                if isinstance(expiry, str):
                    self._expires_at = datetime.fromisoformat(expiry.replace("Z", "+00:00")).astimezone(timezone.utc)
                else:
                    seconds = float(token["expiresIn"])
                    self._expires_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
            except (KeyError, ValueError, TypeError, OverflowError) as exc:
                raise ApiError("Malformed login expiration") from exc
            return user

    async def _request(self, method: str, path: str, *, authenticate: bool = True, retry: bool = True, **kwargs: Any) -> dict[str, Any]:
        if authenticate and (not self._token or datetime.now(timezone.utc) >= self._expires_at - timedelta(hours=1)):
            await self.login()
        headers = {"Authorization": f"Bearer {self._token}"} if authenticate else {}
        try:
            async with self.session.request(method, BASE_URL + path, headers=headers, timeout=aiohttp.ClientTimeout(total=15), **kwargs) as response:
                if response.status == 401:
                    if not authenticate:
                        raise AuthError("Invalid credentials")
                    if retry:
                        self._token = None
                        await self.login()
                        return await self._request(method, path, retry=False, **kwargs)
                    raise AuthError("Unauthorized")
                if response.status >= 500 or response.status == 429:
                    raise ConnectionError("Cloud unavailable")
                if response.status >= 400:
                    raise ApiError(f"Cloud returned HTTP {response.status}")
                data = await response.json()
        except (ValueError, aiohttp.ContentTypeError) as exc:
            raise ApiError("Invalid JSON response") from exc
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ConnectionError("Cloud connection failed") from exc
        if not isinstance(data, dict):
            raise ApiError("Expected JSON object")
        return data

    async def profile(self) -> dict[str, Any]:
        return await self._request("GET", "/auth/user/profile")

    async def hubs(self) -> dict[str, Any]:
        return await self._request("GET", "/api/hubs?perPage=1000")

    async def hub(self, hub_id: str) -> dict[str, Any]:
        from urllib.parse import quote
        return await self._request("GET", f"/api/hubs/{quote(hub_id, safe='')}")

    async def health(self, hub_id: str) -> dict[str, Any]:
        from urllib.parse import quote
        return await self._request("GET", f"/api/hubs/{quote(hub_id, safe='')}/health")
