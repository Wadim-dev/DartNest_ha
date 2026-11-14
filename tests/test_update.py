"""Only HACS updates DartNest; settings services are for admins."""

from __future__ import annotations

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.autodarts.const import DOMAIN

from .conftest import board_mock


async def test_alte_update_entitaet_weg(hass: HomeAssistant, aioclient_mock, mock_entry: MockConfigEntry) -> None:
    board_mock(aioclient_mock)
    mock_entry.add_to_hass(hass)
    reg = er.async_get(hass)
    reg.async_get_or_create("update", DOMAIN, f"{DOMAIN}_integration_update", config_entry=mock_entry)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert reg.async_get_entity_id("update", DOMAIN, f"{DOMAIN}_integration_update") is None
    ids = [e.unique_id for e in er.async_entries_for_config_entry(reg, mock_entry.entry_id) if e.domain == "update"]
    assert len(ids) == 1  # only the board software
    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()


async def test_einstellungen_nur_fuer_admins(hass: HomeAssistant, geladen, hass_read_only_user) -> None:
    """Settings and services that reach other devices need an admin; playing works for everyone."""
    gast = Context(user_id=hass_read_only_user.id)
    for dienst, daten in (("pixel_hinzufuegen", {"adresse": "192.0.2.50"}), ("fernseher_setzen", {"auto": True}),
                          ("strom_setzen", {}), ("ansager_setzen", {"einstellungen": {}}), ("lichteffekte_setzen", {"regeln": []})):
        with pytest.raises(Unauthorized):
            await hass.services.async_call(DOMAIN, dienst, daten, blocking=True, context=gast)
    await hass.services.async_call(DOMAIN, "spieler_anlegen", {"name": "Mia"}, blocking=True, context=gast)
