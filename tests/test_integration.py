"""Home Assistant integration tests with no live Cavius requests."""
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cavius_livisense.const import DOMAIN
from custom_components.cavius_livisense.config_flow import CaviusConfigFlow
from custom_components.cavius_livisense.api import AuthError, ConnectionError


@pytest.fixture(autouse=True)
def verify_cleanup():
    """The HA 2025 fixture leaves its own safe-shutdown worker during teardown."""
    yield


@pytest.fixture(autouse=True)
def enable_custom_integrations(enable_custom_integrations):
    """Permit loading this custom integration."""


@pytest.mark.asyncio
async def test_duplicate_account_guard():
    flow = CaviusConfigFlow()
    flow._validate = AsyncMock(return_value=("account-id", "test@example.com"))
    with (
        patch.object(flow, "async_set_unique_id", new_callable=AsyncMock) as set_id,
        patch.object(flow, "_abort_if_unique_id_configured", side_effect=RuntimeError("duplicate")) as guard,
    ):
        with pytest.raises(RuntimeError, match="duplicate"):
            await flow.async_step_user({"email": "test@example.com", "password": "secret"})
        set_id.assert_awaited_once_with("account-id")
        guard.assert_called_once_with()


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
        patch("custom_components.cavius_livisense.api.LiviSenseClient.hubs", return_value={"data": [{"id": "hub-id"}]}) as list_hubs,
        patch("custom_components.cavius_livisense.api.LiviSenseClient.hub", return_value=hub),
        patch("custom_components.cavius_livisense.api.LiviSenseClient.health", return_value={"status": "ok", "issues": []}) as health,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data
        list_hubs.assert_not_awaited()
        health.assert_not_awaited()
        assert coordinator.data["hub-id"]["hub"]["devices"][0]["id"] == "smoke-id"
        assert coordinator.data["hub-id"]["hub"]["state"]["status"] == "offline"
        assert hass.states.get("binary_sensor.home_connectivity").state == "off"
        assert hass.states.get("sensor.kitchen_last_cloud_update").state == "unavailable"
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_unload(entry.entry_id)



@pytest.mark.asyncio
@pytest.mark.parametrize(("error", "message"), [(AuthError, "invalid_auth"), (ConnectionError, "cannot_connect")])
async def test_config_flow_errors(hass, error, message):
    with patch("custom_components.cavius_livisense.config_flow.LiviSenseClient.login", side_effect=error()):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"}, data={"email": "test@example.com", "password": "secret"})
        assert result["type"] is FlowResultType.FORM
        assert result["errors"]["base"] == message
