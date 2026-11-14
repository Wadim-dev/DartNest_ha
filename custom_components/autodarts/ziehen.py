"""Takeout guard: free the board when "pull darts" hangs although the board is empty.

The board sometimes stays in ``takeoutInProgress`` after the darts are gone and then counts
no more throws. While the board is ready and empty (status ``throw``, no darts) this guard keeps
a picture of each camera. When "pull darts" lasts longer than a few seconds, it compares the
current pictures with those. Only if no camera shows anything that is not on the empty board,
twice in a row, it resets the board. As long as a dart still sticks, the board keeps waiting.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback

from .board import AutodartsBoard, AutodartsError, Stream

if TYPE_CHECKING:
    from .coordinator import ZustandKoordinator

_LOGGER = logging.getLogger(__name__)

KAMERAS: tuple[Stream, ...] = ("cam0", "cam1", "cam2")
BREITE, HOEHE = 320, 180
LEER_RUHE = 3.0  # board empty and ready this long before its pictures are taken
LEER_NEU = 600.0  # take new pictures of the empty board after this long (daylight changes)
HAENGT_NACH = 5.0  # "pull darts" this long before the guard looks
PRUEF_TAKT = 2.0  # seconds between two looks
TREFFER_NOETIG = 2  # looks in a row that must show an empty board
SCHWELLE = 35  # brightness difference (0-255) that counts as changed
MAX_PIXEL = 20  # changed pixels per camera (of 320 x 180) that still count as empty; a dart is far more, an empty board shows 0


def _vorbereiten(jpeg: bytes) -> Any:
    """Small blurred grey picture (runs in the executor)."""
    from PIL import Image, ImageFilter  # Pillow ships with Home Assistant

    with Image.open(io.BytesIO(jpeg)) as bild:
        return bild.convert("L").resize((BREITE, HOEHE)).filter(ImageFilter.GaussianBlur(1))


def _geaendert(leer: Any, jetzt: Any) -> int:
    """Number of pixels that differ from the empty board (brightness evened out first)."""
    from PIL import ImageChops, ImageStat

    m_leer, m_jetzt = ImageStat.Stat(leer).mean[0], ImageStat.Stat(jetzt).mean[0]
    if m_jetzt > 0 and abs(m_leer - m_jetzt) > 1:
        faktor = m_leer / m_jetzt
        jetzt = jetzt.point(lambda v: min(255, round(v * faktor)))
    maske = ImageChops.difference(leer, jetzt).point(lambda v: 255 if v > SCHWELLE else 0)
    return int(maske.histogram()[255])


class ZiehWaechter:
    """Watches the board state and frees a hanging "pull darts" once the board is really empty."""

    def __init__(self, hass: HomeAssistant, board: AutodartsBoard, zustand: ZustandKoordinator) -> None:
        self.hass = hass
        self.board = board
        self.zustand = zustand
        self._leer: dict[str, Any] = {}
        self._leer_zeit = 0.0
        self._status = ""
        self._seit = 0.0
        self._aufgabe: asyncio.Task[None] | None = None
        self.freigaben = 0  # how often the guard freed the board (diagnostics)

    @callback
    def aenderung(self) -> None:
        d = self.zustand.data or {}
        status = str(d.get("status") or "")
        jetzt = time.monotonic()
        if status != self._status:
            self._status, self._seit = status, jetzt
        if self._aufgabe and not self._aufgabe.done():
            return
        dauer = jetzt - self._seit
        if status == "throw" and not d.get("wuerfe") and dauer >= LEER_RUHE:
            if not self._leer or (self._leer_zeit < self._seit) or jetzt - self._leer_zeit > LEER_NEU:
                self._starten(self._leer_merken())
        elif status == "takeoutInProgress" and dauer >= HAENGT_NACH and self._leer:
            self._starten(self._pruefen())

    def _starten(self, arbeit: Any) -> None:
        self._aufgabe = self.hass.async_create_background_task(arbeit, "autodarts takeout guard")

    @callback
    def abbrechen(self) -> None:
        if self._aufgabe and not self._aufgabe.done():
            self._aufgabe.cancel()

    async def _bilder(self) -> dict[str, Any] | None:
        roh = await asyncio.gather(*(self.board.image(k) for k in KAMERAS))
        if any(not r for r in roh):
            return None
        try:
            bilder = [await self.hass.async_add_executor_job(_vorbereiten, r) for r in roh if r]
        except (ImportError, OSError, ValueError) as err:
            _LOGGER.debug("Kamerabild nicht lesbar: %s", err)
            return None
        return dict(zip(KAMERAS, bilder, strict=True))

    async def _leer_merken(self) -> None:
        bilder = await self._bilder()
        d = self.zustand.data or {}
        # nobody threw in the meantime
        if bilder and d.get("status") == "throw" and not d.get("wuerfe"):
            self._leer, self._leer_zeit = bilder, time.monotonic()

    async def _pruefen(self) -> None:
        treffer = 0
        while (self.zustand.data or {}).get("status") == "takeoutInProgress":
            bilder = await self._bilder()
            if bilder is None:
                treffer = 0
            else:
                pixel = [await self.hass.async_add_executor_job(_geaendert, self._leer[k], bilder[k]) for k in KAMERAS]
                treffer = treffer + 1 if max(pixel) <= MAX_PIXEL else 0
                _LOGGER.debug("Darts ziehen: geänderte Pixel je Kamera %s", pixel)
            if treffer >= TREFFER_NOETIG:
                _LOGGER.info("Board hing bei „Darts ziehen“, obwohl es leer ist: freigegeben")
                try:
                    await self.board.reset()
                    self.freigaben += 1
                except AutodartsError as err:
                    _LOGGER.debug("Board-Reset: %s", err)
                return
            await asyncio.sleep(PRUEF_TAKT)

