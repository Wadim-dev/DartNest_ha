"""Several boards: own game, power plug and TV per board; players, statistics and tournaments shared."""

from __future__ import annotations

from http import HTTPStatus

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.autodarts import spiel as spiel_modul
from custom_components.autodarts.const import CONF_HOST, CONF_PORT, DOMAIN

from .conftest import PORT, board_mock

HOST2 = "192.0.2.11"


@pytest.fixture(autouse=True)
def ohne_pausen(monkeypatch):
    async def nichts(_s: float) -> None:
        return None

    monkeypatch.setattr(spiel_modul, "_pause", nichts)


@pytest.fixture
async def zwei(hass: HomeAssistant, aioclient_mock, mock_entry):
    """The first board plus a second one (a further config entry), with the web server for the TV page."""
    assert await async_setup_component(hass, "http", {})
    board_mock(aioclient_mock)
    board_mock(aioclient_mock, basis=f"http://{HOST2}:{PORT}")
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    zweites = MockConfigEntry(domain=DOMAIN, unique_id=f"{HOST2}:{PORT}", title="Autodarts Board 2", data={CONF_HOST: HOST2, CONF_PORT: PORT})
    zweites.add_to_hass(hass)
    assert await hass.config_entries.async_setup(zweites.entry_id)
    await hass.async_block_till_done()
    yield mock_entry, zweites
    for e in (zweites, mock_entry):
        await hass.config_entries.async_unload(e.entry_id)
    await hass.async_block_till_done()


async def _spieler(hass: HomeAssistant, *namen: str) -> list[str]:
    ids = []
    for n in namen:
        r = await hass.services.async_call(DOMAIN, "spieler_anlegen", {"name": n}, blocking=True, return_response=True)
        ids.append(r["id"])
    return ids


def _eid(hass: HomeAssistant, eintrag: MockConfigEntry, schluessel: str) -> str:
    vorsilbe = "" if eintrag.unique_id == DOMAIN else eintrag.entry_id + "_"
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"autodarts_{vorsilbe}{schluessel}")


def _spiel(hass: HomeAssistant, eintrag: MockConfigEntry) -> str:
    return _eid(hass, eintrag, "spiel")


async def test_eigene_entitaeten_und_geraete(hass: HomeAssistant, zwei) -> None:
    erstes, zweites = zwei
    reg = er.async_get(hass)
    eins, zwei_ = _spiel(hass, erstes), _spiel(hass, zweites)
    assert eins.startswith("sensor.autodarts_board_") and zwei_.startswith("sensor.autodarts_board_2_") and eins != zwei_
    assert reg.async_get_entity_id("sensor", DOMAIN, f"autodarts_{zweites.entry_id}_verbindung").startswith("sensor.autodarts_board_2_")
    geraete = dr.async_get(hass)
    assert geraete.async_get_device(identifiers={(DOMAIN, zweites.entry_id)}).name == "Autodarts Board 2"


async def test_spiel_je_board_spieler_gemeinsam(hass: HomeAssistant, zwei) -> None:
    erstes, zweites = zwei
    geraet = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, zweites.entry_id)})
    a, b = await _spieler(hass, "Alex", "Sam")
    await hass.services.async_call(DOMAIN, "spiel_starten", {"spieler": [a, b], "board": geraet.id}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(_spiel(hass, zweites)).state == "running"
    assert hass.states.get(_spiel(hass, erstes)).state == "idle"
    # both boards know both players
    assert len(hass.states.get(_eid(hass, erstes, "spieler")).attributes["spieler"]) == 2
    assert len(hass.states.get(_eid(hass, zweites, "spieler")).attributes["spieler"]) == 2
    # board by name, unknown board
    await hass.services.async_call(DOMAIN, "spiel_abbrechen", {"board": "autodarts board 2"}, blocking=True)
    assert hass.states.get(_spiel(hass, zweites)).state == "idle"
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "spiel_abbrechen", {"board": "Keller"}, blocking=True)


async def test_ende_und_rueckgaengig_je_board(hass: HomeAssistant, zwei) -> None:
    """Undo after the end takes back the history entry of its own game, not the newest one."""
    erstes, zweites = zwei
    le1, le2 = erstes.runtime_data.spiel, zweites.runtime_data.spiel
    a, _b = await _spieler(hass, "Alex", "Sam")
    for le in (le1, le2):
        await le.starten([a], {"spiel": "x01", "startwert": 101, "auswurf": "straight", "einwurf": "straight", "legs": 1, "sets": 0, "bull_off": False})
    for le in (le1, le2):
        for f in ("T20", "S20", "S1"):
            await le.wurf_aendern(None, 9, f)
        await le.weiter()
        for f in ("S20",):
            await le.wurf_aendern(None, 9, f)
    assert le1.spiel["phase"] == le2.spiel["phase"] == "beendet"
    verlauf = le1.daten["verlauf"]
    assert [v["board"] for v in verlauf] == ["Autodarts Board 2", "Autodarts Board"]
    await le1.rueckgaengig()
    assert [v["board"] for v in le1.daten["verlauf"]] == ["Autodarts Board 2"]


async def test_turnier_auf_beiden_boards(hass: HomeAssistant, zwei) -> None:
    erstes, zweites = zwei
    ids = await _spieler(hass, "Alex", "Sam", "Mia", "Ben")
    await hass.services.async_call(DOMAIN, "turnier_starten", {"spieler": ids, "legs": 1}, blocking=True)
    await hass.async_block_till_done()
    sp1, sp2 = erstes.runtime_data.spiel.spiel, zweites.runtime_data.spiel.spiel
    assert sp1["turnier"]["k"] != sp2["turnier"]["k"]
    spieler = {s["name"] for s in sp1["spieler"]} | {s["name"] for s in sp2["spieler"]}
    assert spieler == {"Alex", "Sam", "Mia", "Ben"}
    a = hass.states.get(_spiel(hass, erstes)).attributes["turnier"]
    belegt = [m.get("board") for r in a["runden"] for m in r if m.get("board")]
    assert sorted(belegt) == ["Autodarts Board", "Autodarts Board 2"]
    # no free match while both play: "next" on a busy board does nothing
    await hass.services.async_call(DOMAIN, "turnier_weiter", {"board": zweites.entry_id}, blocking=True)
    assert zweites.runtime_data.spiel.spiel["id"] == sp2["id"]
    await hass.services.async_call(DOMAIN, "turnier_abbrechen", {}, blocking=True)
    assert "turnier" not in erstes.runtime_data.spiel.spiel and "turnier" not in zweites.runtime_data.spiel.spiel


async def test_strom_und_fernseher_je_board(hass: HomeAssistant, zwei, hass_client_no_auth) -> None:
    erstes, zweites = zwei
    await hass.services.async_call(DOMAIN, "strom_setzen", {"schalter": "switch.keller", "board": zweites.entry_id}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(_eid(hass, zweites, "spieler")).attributes["strom"] == {"schalter": "switch.keller"}
    assert hass.states.get(_eid(hass, erstes, "spieler")).attributes["strom"] == {}
    f1, f2 = erstes.runtime_data.fernseher, zweites.runtime_data.fernseher
    assert f1.einst["token"] != f2.einst["token"]
    client = await hass_client_no_auth()
    seite = await (await client.get(f"/autodarts/tv/{f1.einst['token']}")).text()
    assert "EventSource(BASE + '/live')" in seite
    await hass.services.async_call(DOMAIN, "fernseher_setzen", {"alle": True}, blocking=True)
    seite = await (await client.get(f"/autodarts/tv/{f1.einst['token']}")).text()
    assert "/boards" in seite and "iframe" in seite
    einzeln = await client.get(f"/autodarts/tv/{f2.einst['token']}/einzeln")
    assert einzeln.status == HTTPStatus.OK
    boards = f1.boards()["boards"]
    assert [b["name"] for b in boards] == ["Autodarts Board", "Autodarts Board 2"] and boards[0]["eigen"]
    assert boards[1]["token"] == f2.einst["token"]


async def test_zweites_board_entladen(hass: HomeAssistant, zwei, hass_storage) -> None:
    erstes, zweites = zwei
    await _spieler(hass, "Alex")
    await zweites.runtime_data.spiel.starten(None, {"spieler": [erstes.runtime_data.spiel.daten["spieler"][0]["id"]]})
    await hass.config_entries.async_unload(zweites.entry_id)
    await hass.async_block_till_done()
    gespeichert = hass_storage["autodarts.spiel"]["data"]
    assert gespeichert["boards"][zweites.entry_id]["spiel"]["phase"] == "laeuft"
    assert gespeichert.get("spiel") is None
    assert list(erstes.runtime_data.spiel.ablage.leiter) == [erstes.entry_id]
