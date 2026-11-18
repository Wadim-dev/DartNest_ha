"""Camera guard: a camera that stops during a game restarts the board software."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant

from custom_components.autodarts import kamerawache


def _wache(hass: HomeAssistant, fps: list[int | None]) -> tuple[kamerawache.KameraWache, MagicMock]:
    board = MagicMock(restart=AsyncMock())
    zustand = MagicMock(online=True, data={"laeuft": True})
    system = MagicMock(data={"kamera_fps": fps})
    spiel = MagicMock(spiel={"phase": "laeuft"})
    return kamerawache.KameraWache(hass, board, zustand, system, spiel), system


async def test_kamera_steht(hass: HomeAssistant) -> None:
    w, system = _wache(hass, [30, 30, 30])
    w.aenderung()
    system.data = {"kamera_fps": [0, 0, 30]}
    w.aenderung()
    await hass.async_block_till_done()
    w.board.restart.assert_not_called()  # one look is not enough
    with patch.object(kamerawache.time, "monotonic", return_value=5.0):  # a machine that started a moment ago
        w.aenderung()
        await hass.async_block_till_done()
    w.board.restart.assert_called_once()
    with patch.object(kamerawache.time, "monotonic", return_value=60.0):
        w.aenderung()
        w.aenderung()
        await hass.async_block_till_done()
    assert w.board.restart.call_count == 1  # not again right away
    with patch.object(kamerawache.time, "monotonic", return_value=10**9):
        w.aenderung()
        w.aenderung()
        await hass.async_block_till_done()
        assert w.board.restart.call_count == 2
    with patch.object(kamerawache.time, "monotonic", return_value=2 * 10**9):
        w.aenderung()
        w.aenderung()
        await hass.async_block_till_done()
    assert w.board.restart.call_count == 2  # then only a message


async def test_fehlende_kamera_und_pause(hass: HomeAssistant) -> None:
    w, system = _wache(hass, [30, 30, 0])  # a board with two cameras
    for _ in range(4):
        w.aenderung()
    await hass.async_block_till_done()
    w.board.restart.assert_not_called()
    w.spiel.spiel = None  # no game: nothing
    system.data = {"kamera_fps": [0, 0, 0]}
    for _ in range(4):
        w.aenderung()
    await hass.async_block_till_done()
    w.board.restart.assert_not_called()
