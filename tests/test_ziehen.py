"""Takeout guard: a hanging "pull darts" is freed only when the cameras show an empty board."""

from __future__ import annotations

import asyncio
import io
import time
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from PIL import Image, ImageDraw

from custom_components.autodarts import ziehen
from custom_components.autodarts.ziehen import ZiehWaechter


def _jpeg(dart: bool = False, hell: float = 1.0) -> bytes:
    bild = Image.new("RGB", (1280, 720), (40, 40, 40))
    malen = ImageDraw.Draw(bild)
    malen.ellipse((340, 60, 940, 660), fill=(200, 190, 170), outline=(20, 20, 20), width=30)
    malen.ellipse((560, 280, 720, 440), fill=(30, 140, 60))
    if dart:
        malen.line((640, 200, 760, 90), fill=(230, 230, 240), width=10)
        malen.polygon([(760, 90), (800, 40), (820, 100)], fill=(250, 30, 30))
    if hell != 1.0:
        bild = bild.point(lambda v: min(255, round(v * hell)))
    puffer = io.BytesIO()
    bild.save(puffer, "JPEG", quality=85)
    return puffer.getvalue()


class _Board:
    def __init__(self) -> None:
        self.bild = _jpeg()
        self.resets = 0
        self.zustand: _Zustand | None = None

    async def image(self, strom: str) -> bytes | None:
        await asyncio.sleep(0)
        return self.bild

    async def reset(self) -> None:
        self.resets += 1
        if self.zustand:
            self.zustand.data = {"status": "throw", "wuerfe": []}


class _Zustand:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {"status": "throw", "wuerfe": []}


async def _ticken(w: ZiehWaechter, sekunden: float) -> None:
    ende = time.monotonic() + sekunden
    while time.monotonic() < ende:
        w.aenderung()
        await asyncio.sleep(0.02)


async def _starten(hass: HomeAssistant) -> tuple[ZiehWaechter, _Board, _Zustand]:
    board, zustand = _Board(), _Zustand()
    board.zustand = zustand
    w = ZiehWaechter(hass, board, zustand)  # type: ignore[arg-type]
    await _ticken(w, 0.3)
    assert set(w._leer) == {"cam0", "cam1", "cam2"}
    return w, board, zustand


@patch.multiple(ziehen, LEER_RUHE=0.05, HAENGT_NACH=0.1, PRUEF_TAKT=0.05)
async def test_haengt_bei_leerem_board_wird_frei(hass: HomeAssistant) -> None:
    w, board, zustand = await _starten(hass)
    zustand.data = {"status": "takeoutInProgress", "wuerfe": []}
    board.bild = _jpeg(hell=1.15)  # somewhat brighter light does not count as a dart
    await _ticken(w, 0.6)
    assert board.resets == 1
    assert w.freigaben == 1
    w.abbrechen()


@patch.multiple(ziehen, LEER_RUHE=0.05, HAENGT_NACH=0.1, PRUEF_TAKT=0.05)
async def test_dart_steckt_noch_board_wartet(hass: HomeAssistant) -> None:
    w, board, zustand = await _starten(hass)
    zustand.data = {"status": "takeoutInProgress", "wuerfe": []}
    board.bild = _jpeg(dart=True)
    await _ticken(w, 0.6)
    assert board.resets == 0
    # dart pulled: now the board is freed
    board.bild = _jpeg()
    await _ticken(w, 0.5)
    assert board.resets == 1
    w.abbrechen()


@patch.multiple(ziehen, LEER_RUHE=0.05, HAENGT_NACH=0.1, PRUEF_TAKT=0.05)
async def test_kurzes_ziehen_ohne_eingriff(hass: HomeAssistant) -> None:
    w, board, zustand = await _starten(hass)
    zustand.data = {"status": "takeoutInProgress", "wuerfe": []}
    await _ticken(w, 0.05)
    zustand.data = {"status": "throw", "wuerfe": []}
    await _ticken(w, 0.3)
    assert board.resets == 0
    w.abbrechen()


@patch.multiple(ziehen, LEER_RUHE=0.05, HAENGT_NACH=0.1, PRUEF_TAKT=0.05)
async def test_kamera_fehlt_kein_eingriff(hass: HomeAssistant) -> None:
    w, board, zustand = await _starten(hass)
    zustand.data = {"status": "takeoutInProgress", "wuerfe": []}
    board.bild = b""
    await _ticken(w, 0.5)
    assert board.resets == 0
    w.abbrechen()
