"""Painel de Energia dentro do Home Assistant.

Recebe as leituras do medidor IE Tecnologia (SM-3W Lite / SM-W Lite) direto no Home Assistant,
guarda em um banco próprio, cria os sensores e mostra o painel na barra lateral.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from functools import partial
from typing import Any, Callable, Dict, List, Optional

from aiohttp import web
from yarl import URL

from homeassistant.components import frontend, panel_custom, webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, __version__ as HA_VERSION
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .api import PainelApiView
from .const import (CONF_COUNTER_UNIT, CONF_LISTEN_PORT, CONF_LOCAL_ONLY, CONF_MAX_POWER_KW, CONF_MQTT_TOPIC,
                    CONF_OFFLINE_AFTER, CONF_RETENTION_DAYS, CONF_SIDEBAR, CONF_WEBHOOK_ID, DATA_DIR, DEFAULTS,
                    DOMAIN, PANEL_URL, SIGNAL_DEVICE, SIGNAL_REMOVED, STATIC_URL, TITLE, signal_update)
from .core import __version__ as VERSION
from .core.ha_discovery import state_payload
from .core.service import MAX_BODY, Core
from .core.store import Store
from .core.timeutil import Clock

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]
KEY_HTTP_READY = DOMAIN + "_http_ready"


class Hub:
    """Liga o núcleo do painel (banco, gravação e API) ao Home Assistant."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        # O banco (SQLite) é acessado só por estas threads: poucas conexões e nada bloqueia o Home Assistant.
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="painel_energia")
        self.core: Optional[Core] = None
        self.snapshots: Dict[str, Dict[str, Any]] = {}
        self._payloads: Dict[str, Dict[str, Any]] = {}
        self._unsubs: List[Callable[[], None]] = []
        self._runner: Optional[web.AppRunner] = None
        self._panel = False
        self.listen_port = 0
        self.mqtt_topic = ""
        self.mqtt_active = False

    # ------------------------------------------------------------------ utilidades
    def opt(self, key: str) -> Any:
        return self.entry.options.get(key, DEFAULTS[key])

    @property
    def webhook_id(self) -> str:
        return self.entry.data[CONF_WEBHOOK_ID]

    async def run(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Executa uma função do núcleo fora do laço de eventos."""
        return await self.hass.loop.run_in_executor(self.pool, partial(fn, *args, **kwargs))

    def payload(self, device_id: str) -> Dict[str, Any]:
        """Valores dos sensores de um medidor, calculados uma vez por leitura."""
        data = self._payloads.get(device_id)
        if data is None:
            snap = self.snapshots.get(device_id)
            data = state_payload(snap) if snap else {}
            self._payloads[device_id] = data
        return data

    # ------------------------------------------------------------------ ciclo de vida
    def _open(self) -> Any:
        path = self.hass.config.path(DATA_DIR, "energia.db")
        clock = Clock(self.hass.config.time_zone)
        store = Store(path, clock, max_power_kw=float(self.opt(CONF_MAX_POWER_KW)),
                      offline_after_s=int(self.opt(CONF_OFFLINE_AFTER)))
        core = Core(store, counter_unit=self.opt(CONF_COUNTER_UNIT), raw_retention_days=int(self.opt(CONF_RETENTION_DAYS)))
        store.add_listener(self._on_store_event)
        return core, {d["id"]: store.snapshot(d["id"]) for d in store.devices()}

    async def async_start(self) -> None:
        hass = self.hass
        self.core, self.snapshots = await self.run(self._open)

        try:
            webhook.async_register(hass, DOMAIN, TITLE, self.webhook_id, self._handle_webhook,
                                   local_only=bool(self.opt(CONF_LOCAL_ONLY)), allowed_methods=("POST", "PUT", "GET"))
        except TypeError:      # versões antigas do Home Assistant, sem allowed_methods
            webhook.async_register(hass, DOMAIN, TITLE, self.webhook_id, self._handle_webhook)

        await _async_register_http(hass)
        if self.opt(CONF_SIDEBAR):
            await self._async_register_panel()
        port = int(self.opt(CONF_LISTEN_PORT) or 0)
        if port:
            await self._async_start_listener(port)
        topic = str(self.opt(CONF_MQTT_TOPIC) or "").strip()
        if topic:
            await self._async_subscribe_mqtt(topic)
        self._unsubs.append(async_track_time_interval(hass, self._async_tick, timedelta(seconds=15)))
        _LOGGER.info("Painel de Energia %s iniciado; medidor envia para /api/webhook/%s", VERSION, self.webhook_id)

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []
        webhook.async_unregister(self.hass, self.webhook_id)
        if self._panel:
            frontend.async_remove_panel(self.hass, PANEL_URL)
            self._panel = False
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        if self.core is not None:
            await self.run(self.core.store.close)
        self.pool.shutdown(wait=False)

    async def _async_register_panel(self) -> None:
        try:
            await panel_custom.async_register_panel(
                self.hass, frontend_url_path=PANEL_URL, webcomponent_name="painel-energia-panel",
                sidebar_title=TITLE, sidebar_icon="mdi:lightning-bolt",
                module_url="%s/js/ha-panel.js?v=%s" % (STATIC_URL, VERSION),
                embed_iframe=False, require_admin=False,
                config={"version": VERSION, "static_url": STATIC_URL})
            self._panel = True
        except Exception:  # o painel é um extra: sem ele os sensores continuam funcionando
            _LOGGER.exception("Não foi possível registrar o painel na barra lateral")

    # ------------------------------------------------------------------ recepção do medidor
    async def _handle_webhook(self, hass: HomeAssistant, webhook_id: str, request: web.Request) -> web.Response:
        return await self.async_ingest(request)

    async def async_ingest(self, request: web.Request) -> web.Response:
        """Recebe uma mensagem do medidor por HTTP (POST com JSON ou GET com parâmetros)."""
        try:
            body = (await request.read())[:MAX_BODY]
        except Exception:  # conexão interrompida no meio do envio
            body = b""
        query = request.query_string or ""
        meta = {"method": request.method, "path": request.path[:120], "remote": request.remote,
                "ctype": (request.headers.get("Content-Type") or "")[:80]}
        if not body and not query:
            if request.method == "GET":
                return web.json_response({"ok": True, "info": "Endereço de recepção do Painel de Energia. Configure o "
                                                              "medidor para enviar (POST ou GET) para este endereço."})
            await self.run(self.core.reject, "http", "mensagem vazia (sem corpo e sem parâmetros)", **meta)
            return web.Response(status=400, text="ERRO: mensagem vazia")
        res = await self.run(self.core.ingest_raw, body, query, "http", **meta)
        if res["ok"]:
            return web.Response(text="OK")
        if res.get("pending"):
            return web.Response(text="RECEBIDA, MAS NAO GRAVADA: " + res["error"])
        return web.Response(status=400, text="ERRO: " + res["error"])

    async def _async_start_listener(self, port: int) -> None:
        """Porta dedicada (opcional): HTTP simples que aceita qualquer caminho."""
        app = web.Application(client_max_size=MAX_BODY * 2)
        app.router.add_route("*", "/{tail:.*}", self.async_ingest)
        runner = web.AppRunner(app, access_log=None)
        try:
            await runner.setup()
            await web.TCPSite(runner, None, port, reuse_address=True).start()
        except OSError as exc:
            _LOGGER.error("Não foi possível abrir a porta dedicada %s para o medidor: %s", port, exc)
            await runner.cleanup()
            return
        self._runner = runner
        self.listen_port = port
        _LOGGER.info("Porta dedicada para o medidor aberta: %s", port)

    async def _async_subscribe_mqtt(self, topic: str) -> None:
        self.mqtt_topic = topic
        if "mqtt" not in self.hass.config.components:
            _LOGGER.warning("Tópico MQTT definido (%s), mas a integração MQTT do Home Assistant não está configurada", topic)
            return
        from homeassistant.components import mqtt  # só quando a integração MQTT existe

        @callback
        def received(msg: Any) -> None:
            if getattr(msg, "retain", False):
                return                      # mensagem antiga guardada pelo broker: não é leitura nova
            payload = msg.payload if isinstance(msg.payload, (bytes, bytearray)) else str(msg.payload).encode("utf-8")
            self.hass.async_create_task(self.run(self.core.ingest_raw, bytes(payload), "", "mqtt", topic=msg.topic))

        try:
            self._unsubs.append(await mqtt.async_subscribe(self.hass, topic, received, qos=1, encoding=None))
            self.mqtt_active = True
        except Exception:
            _LOGGER.exception("Não foi possível assinar o tópico MQTT %s", topic)

    # ------------------------------------------------------------------ eventos do núcleo
    def _on_store_event(self, event: Dict[str, Any]) -> None:
        """Chamado pelas threads do banco; repassa para o laço de eventos."""
        self.hass.loop.call_soon_threadsafe(self._async_store_event, event)

    @callback
    def _async_store_event(self, event: Dict[str, Any]) -> None:
        kind, dev = event.get("type"), event.get("device")
        if kind == "reading":
            self.snapshots[dev] = event["snapshot"]
            self._payloads.pop(dev, None)
            async_dispatcher_send(self.hass, SIGNAL_DEVICE, dev)
            async_dispatcher_send(self.hass, signal_update(dev))
        elif kind == "offline":
            snap = self.snapshots.get(dev)
            if snap:
                snap["device"]["online"] = False
                async_dispatcher_send(self.hass, signal_update(dev))
        elif kind == "device_updated":
            self.hass.async_create_task(self._async_device_updated(dev))
        elif kind == "device_deleted":
            self.snapshots.pop(dev, None)
            self._payloads.pop(dev, None)
            registry = dr.async_get(self.hass)
            device = registry.async_get_device(identifiers={(DOMAIN, dev)})
            if device is not None:
                registry.async_remove_device(device.id)
            async_dispatcher_send(self.hass, SIGNAL_REMOVED, dev)

    async def _async_device_updated(self, dev: str) -> None:
        snap = await self.run(self.core.store.snapshot, dev)
        if not snap:
            return
        self.snapshots[dev] = snap
        self._payloads.pop(dev, None)
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers={(DOMAIN, dev)})
        if device is not None and device.name != snap["device"]["name"]:
            registry.async_update_device(device.id, name=snap["device"]["name"])
        async_dispatcher_send(self.hass, signal_update(dev))

    async def _async_tick(self, _now: Any) -> None:
        try:
            await self.run(self.core.maintenance)
        except Exception:
            _LOGGER.exception("Erro na manutenção periódica")

    # ------------------------------------------------------------------ dados para o painel
    async def async_status(self, request: web.Request) -> Dict[str, Any]:
        out = await self.run(self.core.status)
        try:
            base = URL(get_url(self.hass, allow_external=False, allow_cloud=False, prefer_external=False))
        except NoURLAvailableError:
            base = request.url
        connected = False
        if self.mqtt_active:
            try:
                from homeassistant.components import mqtt
                connected = bool(mqtt.is_connected(self.hass))
            except Exception:
                connected = False
        entities = er.async_entries_for_config_entry(er.async_get(self.hass), self.entry.entry_id)
        out.update({
            "host": "ha",
            "demo": "off",
            "auth": True,
            "mqtt": {"enabled": bool(self.mqtt_topic), "available": self.mqtt_active, "connected": connected,
                     "auth": False, "topics_in": [self.mqtt_topic] if self.mqtt_topic else []},
            "ingest": {
                "scheme": base.scheme, "host": base.host or "", "http_port": base.port or 8123,
                "path": "/api/webhook/" + self.webhook_id, "token": False,
                "local_only": bool(self.opt(CONF_LOCAL_ONLY)), "alt_port": self.listen_port or None,
                "mqtt_topic": self.mqtt_topic or None, "mqtt_port": None, "mqtt_no_login": False,
            },
            "ha": {"version": HA_VERSION, "entities": len(entities)},
        })
        return out


async def _async_register_http(hass: HomeAssistant) -> None:
    """Registra, uma única vez, os arquivos do painel e a API (não dá para remover rotas depois)."""
    if hass.data.get(KEY_HTTP_READY):
        return
    hass.data[KEY_HTTP_READY] = True
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
    try:
        from homeassistant.components.http import StaticPathConfig
        await hass.http.async_register_static_paths([StaticPathConfig(STATIC_URL, static_dir, False)])
    except ImportError:    # Home Assistant anterior a 2024.7
        hass.http.register_static_path(STATIC_URL, static_dir, False)
    hass.http.register_view(PainelApiView(hass))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hub = Hub(hass, entry)
    await hub.async_start()
    hass.data[DOMAIN] = hub
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hub: Optional[Hub] = hass.data.pop(DOMAIN, None)
        if hub is not None:
            await hub.async_stop()
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(hass: HomeAssistant, entry: ConfigEntry, device: dr.DeviceEntry) -> bool:
    """Permite excluir pelo Home Assistant só dispositivos que não existem mais no painel."""
    hub: Optional[Hub] = hass.data.get(DOMAIN)
    ids = {ident[1] for ident in device.identifiers if ident[0] == DOMAIN}
    return hub is None or not any(i in hub.snapshots for i in ids)
