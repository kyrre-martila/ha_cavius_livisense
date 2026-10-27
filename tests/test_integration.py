"""Home Assistant integration tests with no live Cavius requests."""
from unittest.mock import patch

import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cavius_livisense.const import DOMAIN
from custom_components.cavius_livisense.api import AuthError, ConnectionError


@pytest.fixture(autouse=True)
def enable_custom_integrations(enable_custom_integrations):
    """Permit loading this custom integration."""


@pytest.mark.asyncio
async def test_config_flow_and_duplicate(hass):
    user = {"id": "account-id", "email": "test@example.com"}
    with (
        patch("custom_components.cavius_livisense.config_flow.LiviSenseClient.login", return_value=user),
        patch.object(hass.config_entries, "async_setup", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"email": "test@example.com", "password": "secret"}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"]["password"] == "secret"
        entry = MockConfigEntry(domain=DOMAIN, data=result["data"], unique_id="account-id")
        entry.add_to_hass(hass)
        duplicate = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"},
            data={"email": "test@example.com", "password": "secret"},
        )
        assert duplicate["type"] is FlowResultType.ABORT


@pytest.mark.asyncio
async def test_setup_unload_reload_and_offline_hub(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Cavius",
        data={"email": "test@example.com", "password": "secret"},
        unique_id="account-id",
    )
    entry.add_to_hass(hass)
    hub = {"data": {
        "id": "hub-id", "name": "Home", "model": "MHub",
        "state": {"status": "offline", "updatedAt": 100},
        "devices": [{"id": "smoke-id", "model": "SMOKECAVIUS", "name": "Kitchen", "state": {}}],
    }}
    with (
        patch("custom_components.cavius_livisense.api.LiviSenseClient.profile", return_value={"hubIds": ["hub-id"]}),
        patch("custom_components.cavius_livisense.api.LiviSenseClient.hubs", return_value={"data": [{"id": "hub-id"}]}),
        patch("custom_components.cavius_livisense.api.LiviSenseClient.hub", return_value=hub),
        patch("custom_components.cavius_livisense.api.LiviSenseClient.health", return_value={"status": "ok", "issues": []}),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data
        assert coordinator.data["hub-id"]["hub"]["devices"][0]["id"] == "smoke-id"
        assert coordinator.data["hub-id"]["hub"]["state"]["status"] == "offline"
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()


@pytest.mark.asyncio
@pytest.mark.parametrize(("error", "message"), [(AuthError, "invalid_auth"), (ConnectionError, "cannot_connect")])
async def test_config_flow_errors(hass, error, message):
    with patch("custom_components.cavius_livisense.config_flow.LiviSenseClient.login", side_effect=error()):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"}, data={"email": "test@example.com", "password": "secret"})
        assert result["type"] is FlowResultType.FORM
        assert result["errors"]["base"] == message
