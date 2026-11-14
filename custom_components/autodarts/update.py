"""Update entity of the board software (reported by the board).

DartNest itself is updated by HACS only."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity, UpdateEntityFeature
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import befehl
from .const import DOMAIN
from .coordinator import DartsDaten, DartsEntry
from .entity import DartsEntitaet

PARALLEL_UPDATES = 1
ALTE_ENTITAET = f"{DOMAIN}_integration_update"  # removed in 1.34.0: updates only through HACS


async def async_setup_entry(hass: HomeAssistant, entry: DartsEntry, async_add_entities: AddEntitiesCallback) -> None:
    reg = er.async_get(hass)
    if alt := reg.async_get_entity_id("update", DOMAIN, ALTE_ENTITAET):
        reg.async_remove(alt)
    async_add_entities([BoardUpdate(entry.runtime_data)], True)


class BoardUpdate(DartsEntitaet, UpdateEntity):
    """Update der Board-Software (meldet das Board selbst)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_supported_features = UpdateEntityFeature.INSTALL
    _attr_title = "Autodarts Board"
    _attr_device_class = UpdateDeviceClass.FIRMWARE

    def __init__(self, d: DartsDaten) -> None:
        super().__init__(d, d.system, "board_update", "board_software")

    @property
    def available(self) -> bool:
        return super().available and bool(self.coordinator.data)

    @property
    def installed_version(self) -> str | None:
        return (self.coordinator.data or {}).get("version")

    @property
    def latest_version(self) -> str | None:
        neu = (self.coordinator.data or {}).get("update")
        return neu or self.installed_version

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        await befehl(self.daten.board.apply_update())
        await self.coordinator.async_request_refresh()
