"""Sound effects for every dart and for big moments, on the speakers."""

from __future__ import annotations

from unittest.mock import patch
import wave

from homeassistant.core import HomeAssistant

from custom_components.autodarts import toene

from .test_ansager import _fertig, _frei, _setzen, _wav, lautsprecher as lautsprecher


def test_dart_und_moment() -> None:
    assert [toene.dart_ton(f) for f in ("M", "S3", "S20", "D8", "T5", "T20", "S25", "D25")] == list(toene.DART_TOENE)
    assert toene.moment(180, False, False, False, 3) == "180"
    assert toene.moment(140, False, False, False, 3) == "ton_forty"
    assert toene.moment(100, False, False, False, 3) == "ton"
    assert toene.moment(0, False, False, False, 3) == "no_score"
    assert toene.moment(0, True, False, False, 2) == "bust"
    assert toene.moment(40, False, True, False, 2) == "checkout"
    assert toene.moment(40, False, True, True, 2) == "match"
    assert toene.moment(45, False, False, False, 3) is None


def test_bereitstellen(tmp_path) -> None:
    ordner = toene.bereitstellen(tmp_path)
    (ordner / "dart_s_v0.wav").write_bytes(b"alt")
    toene.bereitstellen(tmp_path)
    namen = sorted(p.name for p in ordner.glob("*.wav"))
    assert namen == sorted(toene.datei(n) for n in toene.ALLE)  # old version removed
    with wave.open(str(ordner / toene.datei("m_180")), "rb") as w:
        assert w.getframerate() == toene.RATE and 1.0 < w.getnframes() / toene.RATE < 3


async def test_toene_auf_den_lautsprechern(hass: HomeAssistant, geladen, lautsprecher) -> None:
    a = geladen.runtime_data.ansager
    await hass.async_add_executor_job(toene.bereitstellen, lautsprecher["pfad"])
    pack = lautsprecher["pfad"] / "autodarts" / "caller" / "Classic"
    pack.mkdir(parents=True)
    _wav(pack / "180.wav")
    await _setzen(hass, pack="Classic", rest=False)
    stand = {"art": "x01", "gewinner": None, "am_zug": 0, "spieler": [{"name": "Anna", "rest": 321}]}
    with patch.object(geladen.runtime_data.spiel, "stand", stand):
        # a single dart: its sound at once
        a.ereignis("dart", {"player": "Anna", "field": "T20"})
        await _fertig(hass, a)
        assert lautsprecher["play"][-1].data["media_content_id"].endswith("/autodarts/toene/dart_t20_v1.wav")
        await _frei(hass, a)
        # the last dart and the 180: dart, fanfare and voice in one sound, the crowd under it
        a.ereignis("dart", {"player": "Anna", "field": "T20"})
        a.ereignis("visit", {"player": "Anna", "score": 180, "darts": ["T20", "T20", "T20"]})
        await _fertig(hass, a)
    url = lautsprecher["play"][-1].data["media_content_id"]
    assert "/autodarts/toene/_mix/s_" in url
    mix = lautsprecher["pfad"] / "autodarts" / "toene" / "_mix" / url.rsplit("/", 1)[1]
    with wave.open(str(mix), "rb") as w:
        assert w.getnframes() / w.getframerate() > 2.4  # dart + fanfare + voice + crowd


async def test_toene_aus(hass: HomeAssistant, geladen, lautsprecher) -> None:
    a = geladen.runtime_data.ansager
    await hass.async_add_executor_job(toene.bereitstellen, lautsprecher["pfad"])
    await _setzen(hass, ton_dart=False, ton_moment=False, ton_publikum=False, ton_spannung=False, punkte=False)
    a.ereignis("dart", {"player": "Anna", "field": "T20"})
    a.ereignis("visit", {"player": "Anna", "score": 180})
    await _fertig(hass, a)
    assert not lautsprecher["play"] and not lautsprecher["tts"]


async def test_spannung(hass: HomeAssistant, geladen, lautsprecher) -> None:
    """Two big trebles: a riser after the second dart; the next one on a finish: a heartbeat after the voice."""
    a = geladen.runtime_data.ansager
    await _setzen(hass, rest=False)
    stand = {"art": "x01", "gewinner": None, "am_zug": 0, "aufnahme": {"darts": [{"f": "T20"}, {"f": "T19"}]},
             "spieler": [{"name": "Anna", "rest": 321}]}
    with patch.object(geladen.runtime_data.spiel, "stand", stand):
        kopf, bett, nach = a.effekte([("dart", {"field": "T19"})])
    assert kopf == [toene.datei("dart_t20"), toene.datei(toene.RISER)] and bett is None and nach == []
    stand = {"art": "x01", "gewinner": None, "am_zug": 1, "weg": ["T20", "D20"],
             "spieler": [{"name": "Anna", "rest": 321}, {"name": "Ben", "rest": 100}]}
    with patch.object(geladen.runtime_data.spiel, "stand", stand):
        kopf, bett, nach = a.effekte([("dart", {"field": "S5"}), ("visit", {"score": 45, "darts": ["S20", "S20", "S5"]})])
    assert kopf == [toene.datei("dart_s")] and nach == [toene.datei(toene.HERZ)]
    # single switches: only the crowd, or only the fanfare
    with patch.object(geladen.runtime_data.spiel, "stand", {"art": "x01", "gewinner": None, "spieler": []}):
        await _setzen(hass, ton_dart=False, ton_moment=False, ton_spannung=False)
        assert a.effekte([("dart", {"field": "T20"}), ("visit", {"score": 180, "darts": ["T20"] * 3})]) == ([], toene.datei("m_jubel_lang"), [])
        await _setzen(hass, ton_dart=False, ton_publikum=False, ton_spannung=False)
        assert a.effekte([("dart", {"field": "T20"}), ("visit", {"score": 180, "darts": ["T20"] * 3})]) == ([toene.datei("m_180")], None, [])
        await _setzen(hass, ton_dart=False, ton_moment=False, ton_publikum=False, ton_spannung=False)
        assert a.effekte([("dart", {"field": "T20"})]) is None


def test_normieren_und_mischen(tmp_path) -> None:
    """A pack is prepared once (8 kHz WAV converted, matching WAV copied); joining then needs no ffmpeg."""
    from custom_components.autodarts import ansager as ansager_modul

    toene.bereitstellen(tmp_path)
    tordner = tmp_path / "autodarts" / "toene"
    quelle = tmp_path / "pack"
    quelle.mkdir()
    _wav(quelle / "180.wav")  # 8 kHz: converted
    with wave.open(str(quelle / "bust.wav"), "wb") as w:  # already in the right format: copied
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(toene.RATE)
        w.writeframes(b"\x00\x01" * 4410)
    norm = ansager_modul._normieren("ffmpeg", quelle, tordner / "_pack" / "x")
    assert norm == {"180": "180.wav", "bust": "bust.wav"}
    assert (tordner / "_pack" / "x" / "bust.wav").read_bytes() == (quelle / "bust.wav").read_bytes()
    (quelle / "bust.wav").unlink()
    assert ansager_modul._normieren("ffmpeg", quelle, tordner / "_pack" / "x") == {"180": "180.wav"}  # gone from the pack: gone here
    with patch("subprocess.run", side_effect=AssertionError("no ffmpeg in the game")):
        name = ansager_modul._mit_toenen("ffmpeg", tordner, [toene.datei("dart_t20"), toene.datei("m_180")],
                                         tordner / "_pack" / "x" / "180.wav", toene.datei("m_jubel_lang"), [toene.datei("m_herz")])
    assert name
    with wave.open(str(tordner / "_mix" / name), "rb") as w:
        lang = w.getnframes() / w.getframerate()
    kopf = sum(ansager_modul._dauer(tordner / toene.datei(n)) for n in ("dart_t20", "m_180"))
    assert lang >= kopf + ansager_modul._dauer(tordner / toene.datei("m_jubel_lang")) - 0.01


async def test_music_assistant_ohne_gong(hass: HomeAssistant, geladen, lautsprecher) -> None:
    from homeassistant.helpers import entity_registry as er

    from custom_components.autodarts import ansager as ansager_modul

    eintrag = er.async_get(hass).async_get_or_create("media_player", "music_assistant", "echo")
    hass.states.async_set(eintrag.entity_id, "idle", {"supported_features": ansager_modul.MEDIA_ANNOUNCE})
    a = geladen.runtime_data.ansager
    await hass.async_add_executor_job(toene.bereitstellen, lautsprecher["pfad"])
    await _setzen(hass, lautsprecher=[eintrag.entity_id, "media_player.kueche"])
    a.ereignis("dart", {"player": "Anna", "field": "S5"})
    await _fertig(hass, a)
    aufrufe = {tuple(c.data["entity_id"]): c.data for c in lautsprecher["play"]}
    assert aufrufe[(eintrag.entity_id,)]["extra"] == {"use_pre_announce": False}
    assert "extra" not in aufrufe[("media_player.kueche",)]


async def test_handy_laedt_vor(hass: HomeAssistant, geladen, lautsprecher, hass_ws_client) -> None:
    """A phone that plays the caller gets the addresses of all sound effects right away."""
    from homeassistant.setup import async_setup_component

    await async_setup_component(hass, "media_source", {})
    await hass.async_add_executor_job(toene.bereitstellen, lautsprecher["pfad"])
    await _setzen(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 7, "type": "autodarts/ansager/hoeren"})
    assert (await ws.receive_json())["success"]
    m = await ws.receive_json()
    urls = m["event"]["vorladen"]
    assert len(urls) == len(toene.ALLE) and all("/autodarts/toene/" in u for u in urls)
    await _setzen(hass, ton_dart=False, ton_moment=False, ton_publikum=False, ton_spannung=False)
    assert await geladen.runtime_data.ansager.vorlade_urls() == []
