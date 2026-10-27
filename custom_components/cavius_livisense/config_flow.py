"""UI configuration and reauthentication."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ApiError, AuthError, ConnectionError, LiviSenseClient
from .const import DOMAIN

SCHEMA = vol.Schema({vol.Required("email"): str, vol.Required("password"): str})

class CaviusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _validate(self, data: dict) -> tuple[str, str]:
        client = LiviSenseClient(async_get_clientsession(self.hass), data["email"], data["password"])
        user = await client.login()
        uid = user.get("id")
        identity = str(uid) if isinstance(uid, str) and uid else data["email"].strip().casefold()
        return identity, str(user.get("email") or data["email"]).strip()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                identity, title = await self._validate(user_input)
            except AuthError:
                errors["base"] = "invalid_auth"
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except ApiError:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(identity)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=title, data=user_input)
        return self.async_show_form(step_id="user", data_schema=SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data):
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                identity, _ = await self._validate(user_input)
            except AuthError:
                errors["base"] = "invalid_auth"
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except ApiError:
                errors["base"] = "unknown"
            else:
                if identity != self._reauth_entry.unique_id:
                    errors["base"] = "wrong_account"
                else:
                    return self.async_update_reload_and_abort(self._reauth_entry, data_updates=user_input)
        return self.async_show_form(step_id="reauth_confirm", data_schema=SCHEMA, errors=errors)
