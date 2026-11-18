"""Config flow: find or enter the Autodarts Board Manager and test it."""

from __future__ import annotations

from ipaddress import IPv4Address
from typing import Any

from homeassistant.config_entries import SOURCE_RECONFIGURE, ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import voluptuous as vol

from .api import board_erstellen
from .board import AutodartsError
from .const import CONF_HOST, CONF_KENNUNG, CONF_PORT, DOMAIN, GERAETENAME, STANDARD_PORT

SCHEMA = vol.Schema({
    vol.Required(CONF_HOST): str,
    vol.Required(CONF_PORT, default=STANDARD_PORT): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
})


async def board_testen(flow: ConfigFlow, host: str, port: int) -> None:
    """Raise AutodartsError if the Board Manager does not answer on /api/state."""
    await board_erstellen(flow.hass, host, port).state()


def _adresse(info: ZeroconfServiceInfo) -> str | None:
    """IPv4 address of the announcing board (only addresses the board itself sent from)."""
    nutzbar = [a for a in info.ip_addresses if not (a.is_loopback or a.is_link_local or a.is_unspecified)]
    nutzbar.sort(key=lambda a: not isinstance(a, IPv4Address))
    return str(nutzbar[0]) if nutzbar else None


def _adresse_von(eintrag: ConfigEntry) -> tuple[str, int]:
    return eintrag.data[CONF_HOST], int(eintrag.data.get(CONF_PORT, STANDARD_PORT))


def _port(info: ZeroconfServiceInfo) -> int:
    """Plain HTTP port; newer Board Managers also announce an HTTPS port."""
    for wert in (info.properties.get("insecurePort"), info.port):
        try:
            port = int(wert)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if 0 < port < 65536:
            return port
    return STANDARD_PORT


class DartsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up an Autodarts board; every further board is one more entry."""

    VERSION = 1

    def __init__(self) -> None:
        self._gefunden: dict[str, Any] = {}

    async def _formular(self, step_id: str, user_input: dict[str, Any] | None, vorschlag: dict[str, Any] | None) -> ConfigFlowResult | None:
        """Test the input; return the form again on errors, None when the board answered."""
        fehler: dict[str, str] = {}
        if user_input is not None:
            try:
                await board_testen(self, user_input[CONF_HOST], user_input[CONF_PORT])
            except AutodartsError:
                fehler["base"] = "cannot_connect"
            else:
                return None
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(SCHEMA, user_input or vorschlag),
            errors=fehler,
        )

    def _titel(self) -> str:
        """The first board keeps its old name, every further board gets a number (rename the device as you like)."""
        n = len(self._async_current_entries(include_ignore=False))
        return GERAETENAME if not n else f"{GERAETENAME} {n + 1}"

    async def _kennung_setzen(self, host: str, port: int, kennung: str | None) -> None:
        """Unique id of a new board: the domain for the first one (as before several boards), else its address or name."""
        erste = not any(e.unique_id == DOMAIN for e in self._async_current_entries(include_ignore=False))
        await self.async_set_unique_id(DOMAIN if erste else (f"zc:{kennung}" if kennung else f"{host}:{port}"))
        self._abort_if_unique_id_configured()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Add a board by hand (also a second, third … board)."""
        if user_input is not None and any(_adresse_von(e) == (user_input[CONF_HOST], user_input[CONF_PORT])
                                          for e in self._async_current_entries(include_ignore=False)):
            return self.async_abort(reason="already_configured")
        if (form := await self._formular("user", user_input, None)) is not None:
            return form
        assert user_input is not None
        await self._kennung_setzen(user_input[CONF_HOST], user_input[CONF_PORT], None)
        return self.async_create_entry(title=self._titel(), data=user_input)

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """The Board Manager announces itself as _autodarts-board._tcp (name = the board, address may change)."""
        host = _adresse(discovery_info)
        if host is None:
            return self.async_abort(reason="cannot_connect")
        port = _port(discovery_info)
        kennung = discovery_info.name.split("._autodarts-board")[0] or None
        eintraege = self._async_current_entries(include_ignore=False)
        # known address: only remember the name of the board
        for e in eintraege:
            if _adresse_von(e) == (host, port):
                if kennung and e.data.get(CONF_KENNUNG) != kennung:
                    self.hass.config_entries.async_update_entry(e, data={**e.data, CONF_KENNUNG: kennung})
                return self.async_abort(reason="already_configured")
        # a known board with a new address (or the only board from before 1.38, which has no name yet):
        # take the new address over, but only if the old one no longer answers
        gleich = [e for e in eintraege if kennung and e.data.get(CONF_KENNUNG) == kennung]
        if not gleich and len(eintraege) == 1 and not eintraege[0].data.get(CONF_KENNUNG):
            gleich = eintraege
        for e in gleich:
            try:
                await board_testen(self, *_adresse_von(e))
            except AutodartsError:
                try:
                    await board_testen(self, host, port)
                except AutodartsError:
                    return self.async_abort(reason="already_configured")
                self.hass.config_entries.async_update_entry(e, data={**e.data, CONF_HOST: host, CONF_PORT: port, **({CONF_KENNUNG: kennung} if kennung else {})})
                self.hass.config_entries.async_schedule_reload(e.entry_id)
            return self.async_abort(reason="already_configured")
        # a new board
        try:
            await board_testen(self, host, port)
        except AutodartsError:
            return self.async_abort(reason="cannot_connect")
        await self._kennung_setzen(host, port, kennung)
        self._gefunden = {CONF_HOST: host, CONF_PORT: port, **({CONF_KENNUNG: kennung} if kennung else {})}
        self.context["title_placeholders"] = {"host": host}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title=self._titel(), data=self._gefunden)
        self._set_confirm_only()
        return self.async_show_form(step_id="zeroconf_confirm", description_placeholders={"host": self._gefunden[CONF_HOST]})

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change address or port of the board."""
        eintrag = self._get_reconfigure_entry()
        if (form := await self._formular("reconfigure", user_input, dict(eintrag.data))) is not None:
            return form
        assert user_input is not None and self.source == SOURCE_RECONFIGURE
        return self.async_update_reload_and_abort(eintrag, data_updates=user_input)
