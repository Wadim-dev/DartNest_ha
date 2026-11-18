"""Camera guard: restart the Board Manager when cameras stop delivering pictures.

Sometimes a USB camera of the board PC stops (0 pictures a second) while the detection still
says it runs. The board then counts no more darts and the live picture stands still. While a
game is on and the detection runs, this guard looks at the frame rate of every camera (system
data, every 15 s). A camera at 0 in two looks in a row restarts the Board Manager software,
at most once every few minutes; after a second restart in a row that did not help, it only
writes a warning (then the board PC itself must be switched off and on).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, callback

from .board import AutodartsBoard, AutodartsError
from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import SystemKoordinator, ZustandKoordinator
    from .spiel import Spielleiter

_LOGGER = logging.getLogger(__name__)

LOOKS = 2  # looks in a row with a stopped camera
PAUSE = 240.0  # seconds after a restart before the guard may restart again
MAX_VERSUCHE = 2  # restarts in a row without success, then only a warning


class KameraWache:
    """Restarts the board software when a camera stands still during a game."""

    def __init__(self, hass: HomeAssistant, board: AutodartsBoard, zustand: ZustandKoordinator,
                 system: SystemKoordinator, spiel: Spielleiter) -> None:
        self.hass = hass
        self.board = board
        self.zustand = zustand
        self.system = system
        self.spiel = spiel
        self._treffer = 0
        self._zuletzt: float | None = None  # time of the last restart
        self._versuche = 0
        self._lief: set[int] = set()

    def _steht(self) -> list[int]:
        """Numbers (1–3) of the cameras that deliver no pictures although the detection runs."""
        fps = (self.system.data or {}).get("kamera_fps") or []
        self._lief |= {i for i, f in enumerate(fps) if f}
        # only cameras that delivered pictures before: a board with fewer cameras is not a stopped camera
        return [i + 1 for i, f in enumerate(fps) if i in self._lief and f is not None and f <= 0]

    @callback
    def aenderung(self) -> None:
        sp = self.spiel.spiel
        laeuft = self.zustand.online and bool((self.zustand.data or {}).get("laeuft"))
        if not laeuft or not sp or sp.get("phase") not in ("bulloff", "laeuft"):
            self._treffer = 0
            return
        stehen = self._steht()
        if not stehen:
            self._treffer = 0
            if self._versuche:
                self._versuche = 0  # the cameras run again
                persistent_notification.async_dismiss(self.hass, f"{DOMAIN}_kamera_steht")
            return
        self._treffer += 1
        if self._treffer < LOOKS or (self._zuletzt is not None and time.monotonic() - self._zuletzt < PAUSE):
            return
        self._treffer = 0
        self._zuletzt = time.monotonic()
        if self._versuche >= MAX_VERSUCHE:
            if self._versuche == MAX_VERSUCHE:
                self._versuche += 1  # tell it once
                _LOGGER.warning("Camera %s of the board still delivers no pictures after restarting the board software; "
                                "restart the board PC", ", ".join(map(str, stehen)))
                persistent_notification.async_create(
                    self.hass, f"Camera {', '.join(map(str, stehen))} of the Autodarts board delivers no pictures, also after "
                    "restarting the board software, so no darts are counted. Restart the board PC (or unplug the camera "
                    "and plug it in again).", "DartNest", f"{DOMAIN}_kamera_steht")
            return
        self._versuche += 1
        _LOGGER.warning("Camera %s of the board delivers no pictures: restarting the board software",
                        ", ".join(map(str, stehen)))
        self.hass.async_create_task(self._neustart())

    async def _neustart(self) -> None:
        try:
            await self.board.restart()
        except AutodartsError as err:
            _LOGGER.debug("Board restart: %s", err)
