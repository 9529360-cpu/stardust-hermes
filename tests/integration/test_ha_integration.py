"""Home Assistant tool integration over real HTTP against a local fake server."""

import pytest

pytestmark = pytest.mark.integration

from tests.fakes.fake_ha_server import FakeHAServer, ENTITY_STATES
from tools.homeassistant_tool import (
    _async_call_service,
    _async_get_state,
    _async_list_entities,
)


class TestToolRest:
    """Call the async tool functions directly against the fake server.

    Note: we call ``_async_*`` instead of the sync ``_handle_*`` wrappers
    because the sync wrappers use ``_run_async`` which blocks the event
    loop, deadlocking with the in-process fake server.  The async functions
    are the real logic; the sync wrappers are trivial bridge code already
    covered by unit tests.
    """

    @pytest.mark.asyncio
    async def test_list_entities_returns_all(self, monkeypatch):
        """_async_list_entities returns all entities from the fake server."""
        async with FakeHAServer() as server:
            monkeypatch.setattr("tools.homeassistant_tool._get_config",
                                lambda url=server.url, token=server.token: (url, token))

            result = await _async_list_entities()

            assert result["count"] == len(ENTITY_STATES)
            ids = {e["entity_id"] for e in result["entities"]}
            assert "light.bedroom" in ids
            assert "climate.thermostat" in ids

    @pytest.mark.asyncio
    async def test_list_entities_domain_filter(self, monkeypatch):
        """Domain filter is applied after fetching from server."""
        async with FakeHAServer() as server:
            monkeypatch.setattr("tools.homeassistant_tool._get_config",
                                lambda url=server.url, token=server.token: (url, token))

            result = await _async_list_entities(domain="light")

            assert result["count"] == 2
            for e in result["entities"]:
                assert e["entity_id"].startswith("light.")

    @pytest.mark.asyncio
    async def test_get_state_single_entity(self, monkeypatch):
        """_async_get_state returns full entity details."""
        async with FakeHAServer() as server:
            monkeypatch.setattr("tools.homeassistant_tool._get_config",
                                lambda url=server.url, token=server.token: (url, token))

            result = await _async_get_state("light.bedroom")

            assert result["entity_id"] == "light.bedroom"
            assert result["state"] == "on"
            assert result["attributes"]["brightness"] == 200
            assert result["last_changed"] is not None

    @pytest.mark.asyncio
    async def test_get_state_not_found(self, monkeypatch):
        """Non-existent entity raises an aiohttp error (404)."""
        import aiohttp as _aiohttp

        async with FakeHAServer() as server:
            monkeypatch.setattr("tools.homeassistant_tool._get_config",
                                lambda url=server.url, token=server.token: (url, token))

            with pytest.raises(_aiohttp.ClientResponseError) as exc_info:
                await _async_get_state("light.nonexistent")
            assert exc_info.value.status == 404

    @pytest.mark.asyncio
    async def test_call_service_turn_on(self, monkeypatch):
        """_async_call_service sends correct payload and server records it."""
        async with FakeHAServer() as server:
            monkeypatch.setattr("tools.homeassistant_tool._get_config",
                                lambda url=server.url, token=server.token: (url, token))

            result = await _async_call_service(
                domain="light",
                service="turn_on",
                entity_id="light.bedroom",
                data={"brightness": 255},
            )

            assert result["success"] is True
            assert result["service"] == "light.turn_on"
            assert len(result["affected_entities"]) == 1
            assert result["affected_entities"][0]["state"] == "on"

            # Verify fake server recorded the call
            assert len(server.received_service_calls) == 1
            call = server.received_service_calls[0]
            assert call["domain"] == "light"
            assert call["service"] == "turn_on"
            assert call["data"]["entity_id"] == "light.bedroom"
            assert call["data"]["brightness"] == 255


class TestAuthAndErrors:
    @pytest.mark.asyncio
    async def test_rest_unauthorized(self, monkeypatch):
        """Async function raises on 401 when token is wrong."""
        import aiohttp as _aiohttp

        async with FakeHAServer() as server:
            monkeypatch.setattr("tools.homeassistant_tool._get_config",
                                lambda url=server.url, token="bad-token": (url, token))

            with pytest.raises(_aiohttp.ClientResponseError) as exc_info:
                await _async_list_entities()
            assert exc_info.value.status == 401

    @pytest.mark.asyncio
    async def test_rest_server_error(self, monkeypatch):
        """Async function raises on 500 response."""
        import aiohttp as _aiohttp

        async with FakeHAServer() as server:
            server.force_500 = True
            monkeypatch.setattr("tools.homeassistant_tool._get_config",
                                lambda url=server.url, token=server.token: (url, token))

            with pytest.raises(_aiohttp.ClientResponseError) as exc_info:
                await _async_list_entities()
            assert exc_info.value.status == 500
