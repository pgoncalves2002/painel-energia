"""Configuração da integração pela interface do Home Assistant."""
from __future__ import annotations

import re
import secrets
from typing import Any, Dict, Optional

import voluptuous as vol
from yarl import URL

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import (CONF_COUNTER_UNIT, CONF_LISTEN_PORT, CONF_LOCAL_ONLY, CONF_MAX_POWER_KW, CONF_MQTT_TOPIC,
                    CONF_OFFLINE_AFTER, CONF_RETENTION_DAYS, CONF_SIDEBAR, CONF_WEBHOOK_ID, DEFAULTS, DOMAIN, TITLE)

_ID_OK = re.compile(r"^[A-Za-z0-9_-]{4,48}$")


def _address(hass: Any) -> Dict[str, str]:
    """Endereço do Home Assistant na rede local, que é o que o medidor deve usar."""
    try:
        url = URL(get_url(hass, allow_external=False, allow_cloud=False, prefer_external=False))
        return {"server": "http://%s" % url.host, "port": str(url.port or 8123)}
    except NoURLAvailableError:
        return {"server": "http://IP-DO-HOME-ASSISTANT", "port": "8123"}


class PainelEnergiaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None) -> Any:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors: Dict[str, str] = {}
        suggested = "medidor-" + secrets.token_hex(4)
        if user_input is not None:
            webhook_id = str(user_input.get(CONF_WEBHOOK_ID, "")).strip()
            if _ID_OK.match(webhook_id):
                placeholders = _address(self.hass)
                placeholders["path"] = "/api/webhook/" + webhook_id
                return self.async_create_entry(title=TITLE, data={CONF_WEBHOOK_ID: webhook_id},
                                               description_placeholders=placeholders)
            errors[CONF_WEBHOOK_ID] = "invalid_id"
            suggested = webhook_id or suggested
        return self.async_show_form(
            step_id="user", errors=errors,
            data_schema=vol.Schema({vol.Required(CONF_WEBHOOK_ID, default=suggested): str}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        flow = PainelEnergiaOptionsFlow()
        flow.entry_ref = config_entry
        return flow


class PainelEnergiaOptionsFlow(config_entries.OptionsFlow):
    entry_ref: Optional[config_entries.ConfigEntry] = None

    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None) -> Any:
        if user_input is not None:
            user_input[CONF_MQTT_TOPIC] = str(user_input.get(CONF_MQTT_TOPIC) or "").strip()
            return self.async_create_entry(title="", data=user_input)
        cur = dict(DEFAULTS)
        cur.update(self.entry_ref.options if self.entry_ref else {})
        schema = vol.Schema({
            vol.Required(CONF_SIDEBAR, default=cur[CONF_SIDEBAR]): bool,
            vol.Required(CONF_LOCAL_ONLY, default=cur[CONF_LOCAL_ONLY]): bool,
            vol.Required(CONF_LISTEN_PORT, default=cur[CONF_LISTEN_PORT]): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
            vol.Optional(CONF_MQTT_TOPIC, description={"suggested_value": cur[CONF_MQTT_TOPIC]}): str,
            vol.Required(CONF_COUNTER_UNIT, default=cur[CONF_COUNTER_UNIT]): vol.In({"kwh": "kWh", "wh": "Wh"}),
            vol.Required(CONF_OFFLINE_AFTER, default=cur[CONF_OFFLINE_AFTER]): vol.All(vol.Coerce(int), vol.Range(min=30, max=3600)),
            vol.Required(CONF_RETENTION_DAYS, default=cur[CONF_RETENTION_DAYS]): vol.All(vol.Coerce(int), vol.Range(min=0, max=36500)),
            vol.Required(CONF_MAX_POWER_KW, default=cur[CONF_MAX_POWER_KW]): vol.All(vol.Coerce(float), vol.Range(min=1, max=10000)),
        })
        return self.async_show_form(step_id="init", data_schema=schema)
