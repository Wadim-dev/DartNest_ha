"""Highlight clips: recording, saving at a 180, list, delete, files for the cards and the TV page."""

from __future__ import annotations

import asyncio
import io
import math
from pathlib import Path
import re
import shutil
import subprocess

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component
import pytest

from custom_components.autodarts import clips as clips_modul
from custom_components.autodarts.const import DOMAIN

from .conftest import BASIS, board_mock


async def _dienst(hass: HomeAssistant, dienst: str, **daten):
    return await hass.services.async_call(DOMAIN, dienst, daten, blocking=True, return_response=dienst == "spieler_anlegen")


def _kal(f) -> dict:
    """Calibration points of a camera that sees the board through the mapping f(mm) -> px."""
    radien = (170, 162, 107, 99)
    ringe = [[list(f(r * math.sin(math.radians(9 + 18 * k)), -r * math.cos(math.radians(9 + 18 * k)))) for k in range(20)] for r in radien]
    return {"ringe": ringe, "bull": list(f(0, 0))}


def test_ecken() -> None:
    # straight camera: 2 px per mm, bull at (640, 360)
    eck = clips_modul.ecken(_kal(lambda x, y: (640 + 2 * x, 360 + 2 * y)))
    assert eck is not None
    assert [tuple(round(v) for v in p) for p in eck] == [(190, -90), (1090, -90), (190, 810), (1090, 810)]

    # tilted camera (perspective): the corners come back exactly
    def schraeg(x: float, y: float) -> tuple[float, float]:
        w = 1 + 0.0008 * x
        return ((640 + 1.6 * x + 0.3 * y) / w, (360 + 0.2 * x + 1.4 * y) / w)

    eck = clips_modul.ecken(_kal(schraeg))
    assert eck is not None
    for p, (x, y) in zip(eck, ((-225, -225), (225, -225), (-225, 225), (225, 225)), strict=True):
        assert math.dist(p, schraeg(x, y)) < 0.01
    assert clips_modul.ecken(None) is None and clips_modul.ecken({"ringe": [[[1, 1]] * 3]}) is None


@pytest.fixture
async def mit_http(hass: HomeAssistant, aioclient_mock, mock_entry, monkeypatch):
    monkeypatch.setattr(clips_modul, "TAKT", 0.02)
    monkeypatch.setattr(clips_modul, "NACHHER", 0.1)
    monkeypatch.setattr(clips_modul, "VORHER", 1.0)
    assert await async_setup_component(hass, "http", {})
    board_mock(aioclient_mock)
    aioclient_mock.get(f"{BASIS}/api/img/cams/0", content=b"jpeg")
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    yield mock_entry
    await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()


async def test_clip_bei_180(hass: HomeAssistant, mit_http, hass_client, hass_client_no_auth, monkeypatch) -> None:
    kodiert: list[tuple] = []

    def kodieren(ffmpeg, bilder, fps, eck, video: Path, vorschau: Path, mitte, fx=None, knall=None) -> bool:
        kodiert.append((len(bilder), fps, eck, mitte, fx, knall))
        video.write_bytes(b"mp4")
        vorschau.write_bytes(b"jpg")
        return True

    monkeypatch.setattr(clips_modul, "_kodieren", kodieren)

    def rendern(ffmpeg, ordner: Path) -> bool:
        ordner.mkdir(parents=True)
        (ordner / "001.png").write_bytes(b"png")
        return True

    monkeypatch.setattr(clips_modul, "knall_rendern", rendern)
    c = mit_http.runtime_data.clips
    attr = lambda: hass.states.get("sensor.autodarts_board_players").attributes["clips"]  # noqa: E731
    assert attr()["an"] is False and attr()["anlaesse"] == ["one_eighty", "high_finish", "match_won"] and attr()["liste"] == []

    anna = (await _dienst(hass, "spieler_anlegen", name="Anna"))["id"]
    ben = (await _dienst(hass, "spieler_anlegen", name="Ben"))["id"]
    await _dienst(hass, "spiel_starten", spieler=[anna, ben], startwert=501)
    with pytest.raises(ServiceValidationError) as fehler:
        await _dienst(hass, "clip_speichern")
    assert fehler.value.translation_key == "clips_off"
    await asyncio.sleep(0.1)
    assert not c._laeuft()  # off: nothing is recorded

    await _dienst(hass, "clips_setzen", an=True)
    await asyncio.sleep(0.3)
    assert c._laeuft() and len(c._puffer) > 3
    for i in range(3):
        await _dienst(hass, "wurf_aendern", dart=i, feld="T20")
    await _dienst(hass, "weiter")
    for _ in range(50):
        await asyncio.sleep(0.05)
        if attr()["liste"]:
            break
    liste = attr()["liste"]
    assert len(liste) == 1 and liste[0]["art"] == "one_eighty" and liste[0]["spieler"] == "Anna" and liste[0]["wert"] == 180
    assert kodiert[0][0] > 3
    assert kodiert[0][4] is None  # typed darts: no impact effect
    knall = kodiert[0][5]
    assert knall is not None and (knall / "001.png").exists()  # explosion prepared once when recording starts
    assert attr()["effekt"] is True
    cid = liste[0]["id"]
    assert (c.ordner / f"{cid}.mp4").exists() and (c.ordner / f"{cid}.jpg").exists()

    # replay on the TV page, the file for the cards (login) and for the TV page (secret link)
    tv = mit_http.runtime_data.fernseher
    assert tv.live()["clip"]["id"] == cid
    token = tv.url().rsplit("/", 1)[1]
    assert (await (await hass_client()).get(f"/api/autodarts/clip/{cid}.mp4")).status == 200
    ohne = await hass_client_no_auth()
    assert (await ohne.get(f"/api/autodarts/clip/{cid}.mp4")).status == 401
    assert (await ohne.get(f"/autodarts/tv/{token}/clip/{cid}.jpg")).status == 200
    assert (await ohne.get(f"/autodarts/tv/{token}/clip/../../secrets.yaml")).status == 404
    assert (await ohne.get(f"/autodarts/tv/falsch/clip/{cid}.mp4")).status == 404
    assert (await ohne.get(f"/autodarts/tv/{token}/overlay")).status == 200

    # button "save clip" and the limit
    await _dienst(hass, "clips_setzen", anzahl=1, wiederholung=False)
    assert tv.live()["clip"] is None
    await _dienst(hass, "clip_speichern")
    for _ in range(50):
        await asyncio.sleep(0.05)
        if attr()["liste"][0]["id"] != cid:
            break
    liste = attr()["liste"]
    assert len(liste) == 1 and liste[0]["art"] == "manual" and not (c.ordner / f"{cid}.mp4").exists()

    await _dienst(hass, "clip_loeschen", id=liste[0]["id"])
    assert attr()["liste"] == [] and not list(c.ordner.glob("*.mp4"))

    # leftover files that are not in the list are removed, files of listed clips stay
    await _dienst(hass, "clip_speichern")
    for _ in range(50):
        await asyncio.sleep(0.05)
        if attr()["liste"]:
            break
    behalten = attr()["liste"][0]["id"]
    (c.ordner / "0123456789ab.mp4").write_bytes(b"alt")
    (c.ordner / "notiz.txt").write_text("fremd")
    await c._waisen_weg()
    assert not (c.ordner / "0123456789ab.mp4").exists() and (c.ordner / "notiz.txt").exists()
    assert (c.ordner / f"{behalten}.mp4").exists()

    # disk almost full: no new clip
    monkeypatch.setattr(clips_modul, "FREI_MIN", 10**18)
    anzahl = len(kodiert)
    await _dienst(hass, "clip_speichern")
    await asyncio.sleep(0.5)
    assert len(kodiert) == anzahl and attr()["liste"][0]["id"] == behalten

    # game over: recording stops
    await _dienst(hass, "spiel_abbrechen")
    await asyncio.sleep(0.1)
    assert not c._laeuft()

    # unloading removes the prepared explosion
    await c.beenden()
    assert not knall.exists() and c._knall is None


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_kodieren_mit_ffmpeg(tmp_path: Path) -> None:
    bild = tmp_path / "b.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360", "-frames:v", "1", str(bild)], check=True)
    bilder = [bild.read_bytes()] * 12
    eck = [(100.0, 20.0), (540.0, 0.0), (80.0, 340.0), (560.0, 350.0)]
    assert clips_modul._kodieren("ffmpeg", bilder, 6.0, eck, tmp_path / "c.mp4", tmp_path / "c.jpg", 5)
    assert (tmp_path / "c.mp4").stat().st_size > 1000 and (tmp_path / "c.jpg").exists()
    assert clips_modul._kodieren("ffmpeg", bilder, 6.0, None, tmp_path / "d.mp4", tmp_path / "d.jpg", 5)
    assert not clips_modul._kodieren("ffmpeg", [b"kaputt"] * 8, 6.0, None, tmp_path / "e.mp4", tmp_path / "e.jpg", 2)


GERADE = _kal(lambda x, y: (640 + 2 * x, 360 + 2 * y))  # straight camera: 2 px per mm, bull at (640, 360)


def test_dart_punkte() -> None:
    # T20 middle: 103 mm above the bull
    kamera, radius, im_clip = clips_modul.dart_punkte(GERADE, 0.0, 103 / 170)
    assert math.dist(kamera, (640, 360 - 206)) < 0.1
    assert abs(radius - 2 * clips_modul.FX_UMKREIS) < 0.1
    halb = clips_modul.GROESSE / 2
    assert math.dist(im_clip, (halb, halb - 103 / clips_modul.RAND_MM * halb)) < 0.1
    # double 6 on the right
    kamera, _, im_clip = clips_modul.dart_punkte(GERADE, 166 / 170, 0.0)
    assert math.dist(kamera, (640 + 332, 360)) < 0.1 and im_clip[0] > halb
    assert clips_modul.dart_punkte(None, 0.0, 0.0) is None


def _bilder(dart_ab: int, n: int = 10, arm: int | None = None) -> list[bytes]:
    """Camera images 1280×720: the dart (dark spot at the T20) shows from image dart_ab, an arm moves elsewhere."""
    from PIL import Image, ImageDraw

    out = []
    for i in range(n):
        im = Image.new("RGB", (1280, 720), (150, 140, 120))
        d = ImageDraw.Draw(im)
        if arm is not None and i >= arm:
            d.rectangle((100 + 40 * i, 500, 260 + 40 * i, 700), fill=(220, 180, 160))
        if i >= dart_ab:
            d.ellipse((630, 144, 650, 164), fill=(20, 20, 30))
        puffer = io.BytesIO()
        im.save(puffer, "JPEG", quality=85)
        out.append(puffer.getvalue())
    return out


def test_einschlag_bild() -> None:
    kamera, radius, _ = clips_modul.dart_punkte(GERADE, 0.0, 103 / 170)
    assert clips_modul.einschlag_bild(_bilder(6, arm=2), list(range(10)), kamera, radius) == 6
    assert clips_modul.einschlag_bild(_bilder(3), list(range(2, 10)), kamera, radius) == 3
    # dart already there before the window (or never): no clear moment
    assert clips_modul.einschlag_bild(_bilder(0), list(range(10)), kamera, radius) is None
    assert clips_modul.einschlag_bild([b"kaputt"] * 4, list(range(4)), kamera, radius) is None


async def test_einschlag(hass: HomeAssistant, mit_http) -> None:
    c = mit_http.runtime_data.clips
    bilder = [(100.0 + i * 0.2, b) for i, b in enumerate(_bilder(25, n=40))]
    fps = 5.0
    dart = {"um": 100.0 + 25 * 0.2 + 0.7, "x": 0.0, "y": 103 / 170, "quelle": "board"}
    t0, x, y = await c._einschlag({"dart": dart, "davor": 101.0}, bilder, fps, GERADE)
    assert abs(t0 - 25 / fps) < 1e-6 and abs(x - 360) < 0.1 and y < 360
    # nothing to see in the images: shortly before the dart was counted
    t0, _, _ = await c._einschlag({"dart": dart, "davor": None}, [(t, bilder[0][1]) for t, _ in bilder], fps, GERADE)
    assert abs(t0 - (dart["um"] - clips_modul.FX_VERZUG - 100.0)) <= 0.1 + 1e-6
    # no effect: typed dart, bot, no calibration, switched off
    assert await c._einschlag({"dart": dart | {"quelle": "hand"}}, bilder, fps, GERADE) is None
    assert await c._einschlag({"dart": dart | {"x": None}}, bilder, fps, GERADE) is None
    assert await c._einschlag({"dart": dart}, bilder, fps, None) is None
    assert await c._einschlag({}, bilder, fps, GERADE) is None
    await _dienst(hass, "clips_setzen", effekt=False)
    assert await c._einschlag({"dart": dart}, bilder, fps, GERADE) is None


async def test_merken_nimmt_letzten_dart(hass: HomeAssistant, mit_http) -> None:
    c = mit_http.runtime_data.clips
    c._aufnahme = hass.loop.create_future()  # counts as recording
    for i in range(3):
        c.ereignis("dart", {"x": 0.1 * i, "y": 0.5, "source": "board", "field": "T20", "points": 60})
    assert c.merken("one_eighty", {"player": "Anna", "score": 180})
    assert c._offen["dart"]["x"] == 0.2 and c._offen["davor"] is not None
    c._offen = None
    assert c.merken("manual", {}) and "dart" not in c._offen
    c._offen = None
    c._aufnahme.cancel()
    c._aufnahme = None


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_kodieren_mit_einschlag(tmp_path: Path) -> None:
    bilder = _bilder(6, n=12)
    eck = clips_modul.ecken(GERADE)
    assert clips_modul._kodieren("ffmpeg", bilder, 5.0, eck, tmp_path / "c.mp4", tmp_path / "c.jpg", 6, (1.2, 360.0, 195.0))
    assert (tmp_path / "c.mp4").stat().st_size > 1000 and (tmp_path / "c.jpg").exists()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_knall_vorbereitet(tmp_path: Path) -> None:
    knall = tmp_path / "knall"
    assert clips_modul.knall_rendern("ffmpeg", knall)
    assert len(list(knall.glob("*.png"))) == round(clips_modul.FX_DAUER * 25)
    assert not clips_modul.knall_rendern("gibtsnicht", tmp_path / "x")
    # explosion near the end: the last frame is held until it is over
    bilder = _bilder(9, n=10)
    video = tmp_path / "c.mp4"
    assert clips_modul._kodieren("ffmpeg", bilder, 5.0, clips_modul.ecken(GERADE), video, tmp_path / "c.jpg", 9, (1.8, 360.0, 195.0), knall)
    info = subprocess.run(["ffmpeg", "-i", str(video)], capture_output=True, check=False).stderr.decode()
    h, m, sek = re.search(r"Duration: (\d+):(\d+):([\d.]+)", info).groups()
    dauer = int(h) * 3600 + int(m) * 60 + float(sek)
    assert dauer >= 1.8 + clips_modul.FX_DAUER
