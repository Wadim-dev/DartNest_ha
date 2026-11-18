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
    await _setzen(hass, toene=False, punkte=False)
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
    await _setzen(hass, toene=False)
    assert a.effekte([("dart", {"field": "T20"})]) is None
