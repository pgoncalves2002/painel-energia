"""Sensores do medidor: potência, tensão, corrente, energia e situação da tensão."""
from __future__ import annotations

from typing import Any, Dict, Set

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

try:
    from homeassistant.helpers.device_registry import DeviceInfo
except ImportError:  # Home Assistant anterior a 2023.9
    from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, PANEL_URL, SIGNAL_DEVICE, SIGNAL_REMOVED, signal_update
from .core.ha_discovery import entities as entity_table, slug


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: Any) -> None:
    hub = hass.data[DOMAIN]
    added: Dict[str, Set[str]] = {}

    @callback
    def sync_device(device_id: str) -> None:
        """Cria os sensores de um medidor na primeira leitura (ou quando ele passa a enviar mais fases)."""
        snap = hub.snapshots.get(device_id)
        if not snap:
            return
        have = added.setdefault(device_id, set())
        new = [MeterSensor(hub, device_id, spec) for spec in entity_table(int(snap["device"].get("phases") or 3))
               if spec["key"] not in have]
        if new:
            have.update(e.spec["key"] for e in new)
            async_add_entities(new)

    @callback
    def forget_device(device_id: str) -> None:
        added.pop(device_id, None)

    for device_id in list(hub.snapshots):
        sync_device(device_id)
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_DEVICE, sync_device))
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_REMOVED, forget_device))


class MeterSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: Any, device_id: str, spec: Dict[str, Any]) -> None:
        self._hub = hub
        self._device_id = device_id
        self.spec = spec
        self._attr_unique_id = "%s_%s" % (slug(device_id), spec["key"])
        self._attr_name = spec["name"]
        if spec["device_class"]:
            self._attr_device_class = SensorDeviceClass(spec["device_class"])
        if spec["unit"]:
            self._attr_native_unit_of_measurement = spec["unit"]
        if spec["state_class"]:
            self._attr_state_class = SensorStateClass(spec["state_class"])
        if spec["precision"] is not None:
            self._attr_suggested_display_precision = spec["precision"]
        if not spec["enabled"]:
            self._attr_entity_registry_enabled_default = False
        if spec["diagnostic"]:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if spec["icon"]:
            self._attr_icon = spec["icon"]
        if spec["options"]:
            self._attr_options = list(spec["options"])
            self._attr_translation_key = "situacao_tensao"

    @property
    def device_info(self) -> DeviceInfo:
        info = (self._hub.snapshots.get(self._device_id) or {}).get("device") or {}
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=info.get("name") or "Medidor de energia",
            manufacturer="IE Tecnologia",
            model=info.get("model") or "SM-3W Lite",
            configuration_url="homeassistant://" + PANEL_URL,
        )

    @property
    def available(self) -> bool:
        snap = self._hub.snapshots.get(self._device_id)
        return bool(snap and snap["device"].get("online"))

    @property
    def native_value(self) -> Any:
        return self._hub.payload(self._device_id).get(self.spec["key"])

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, signal_update(self._device_id), self._async_updated))

    @callback
    def _async_updated(self) -> None:
        self.async_write_ha_state()
