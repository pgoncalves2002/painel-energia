"""Ponte MQTT: recebe as leituras do medidor (opcional) e publica para o Home Assistant."""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from . import ha_discovery
from .config import Config

log = logging.getLogger("painel.mqtt")

try:  # a aplicação funciona sem MQTT se a biblioteca não estiver instalada
    import paho.mqtt.client as mqtt
    HAVE_PAHO = True
except Exception:  # pragma: no cover
    mqtt = None  # type: ignore
    HAVE_PAHO = False


def topic_matches(pattern: str, topic: str) -> bool:
    """Compara um tópico com um filtro MQTT (aceita + e #)."""
    pp, tt = pattern.split("/"), topic.split("/")
    for i, p in enumerate(pp):
        if p == "#":
            return True
        if i >= len(tt):
            return False
        if p != "+" and p != tt[i]:
            return False
    return len(pp) == len(tt)


class MqttBridge:
    def __init__(self, cfg: Config, on_meter_message: Callable[[bytes, str], None],
                 list_devices: Callable[[], List[Dict[str, Any]]],
                 get_snapshot: Callable[[str], Optional[Dict[str, Any]]]):
        self.cfg = cfg
        self.on_meter_message = on_meter_message
        self.list_devices = list_devices
        self.get_snapshot = get_snapshot
        self.client = None
        self.connected = False
        self.last_error: Optional[str] = None
        self.connected_since: Optional[float] = None
        self.rx_count = 0
        self.tx_count = 0
        self.discovered: Dict[str, int] = {}     # id do medidor -> número de fases já anunciado
        self._lock = threading.Lock()
        self._stopping = False

    # ------------------------------------------------------------------ ciclo de vida
    def start(self) -> bool:
        if not self.cfg.mqtt_enabled:
            return False
        if not HAVE_PAHO:
            self.last_error = "biblioteca paho-mqtt não instalada"
            log.warning("MQTT configurado, mas a biblioteca paho-mqtt não está instalada.")
            return False
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.cfg.mqtt_client_id, clean_session=True)
        if self.cfg.mqtt_username:
            c.username_pw_set(self.cfg.mqtt_username, self.cfg.mqtt_password or None)
        c.will_set(self._bridge_topic(), "offline", qos=1, retain=True)
        c.on_connect = self._on_connect
        c.on_connect_fail = self._on_connect_fail
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        c.reconnect_delay_set(min_delay=1, max_delay=60)
        self.client = c
        try:
            c.connect_async(self.cfg.mqtt_host, self.cfg.mqtt_port, keepalive=60)
        except Exception as exc:
            self.last_error = str(exc)
            log.error("MQTT: endereço do broker inválido (%s:%s): %s", self.cfg.mqtt_host, self.cfg.mqtt_port, exc)
            return False
        c.loop_start()
        log.info("MQTT: conectando a %s:%s", self.cfg.mqtt_host, self.cfg.mqtt_port)
        return True

    def stop(self) -> None:
        c = self.client
        if c is None:
            return
        self._stopping = True
        try:
            if self.connected:
                for d in self.list_devices():
                    c.publish(ha_discovery.topics(self.cfg.mqtt_base_topic, d["id"])["availability"],
                              "offline", qos=1, retain=True)
                info = c.publish(self._bridge_topic(), "offline", qos=1, retain=True)
                info.wait_for_publish(timeout=2)
            c.disconnect()
        except Exception:
            pass
        finally:
            c.loop_stop()

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.cfg.mqtt_enabled,
            "available": HAVE_PAHO,
            "host": self.cfg.mqtt_host,
            "port": self.cfg.mqtt_port,
            "auth": bool(self.cfg.mqtt_username),
            "connected": self.connected,
            "since": self.connected_since,
            "error": self.last_error,
            "topics_in": list(self.cfg.mqtt_topics_in),
            "base_topic": self.cfg.mqtt_base_topic,
            "ha_discovery": self.cfg.ha_discovery,
            "ha_prefix": self.cfg.ha_prefix,
            "rx": self.rx_count,
            "tx": self.tx_count,
            "discovered": sorted(self.discovered),
        }

    def _bridge_topic(self) -> str:
        return "%s/bridge/status" % self.cfg.mqtt_base_topic

    # ------------------------------------------------------------------ callbacks do paho
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if getattr(reason_code, "is_failure", False):
            self.connected = False
            self.last_error = "conexão recusada pelo broker: %s" % reason_code
            log.error("MQTT: %s", self.last_error)
            return
        self.connected = True
        self.connected_since = time.time()
        self.last_error = None
        log.info("MQTT: conectado a %s:%s", self.cfg.mqtt_host, self.cfg.mqtt_port)
        for t in self.cfg.mqtt_topics_in:
            client.subscribe(t, qos=1)
        if self.cfg.ha_discovery:
            client.subscribe("%s/status" % self.cfg.ha_prefix, qos=1)
        client.publish(self._bridge_topic(), "online", qos=1, retain=True)
        self.discovered.clear()
        self.announce_all()

    def _on_connect_fail(self, client, userdata):
        # o broker não respondeu (endereço errado, broker parado, rede); o paho tenta de novo sozinho
        msg = "sem resposta do broker em %s:%s" % (self.cfg.mqtt_host, self.cfg.mqtt_port)
        if self.last_error != msg and not self._stopping:
            log.warning("MQTT: %s; tentando de novo a cada minuto, no máximo", msg)
        self.connected = False
        self.last_error = msg

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        if self.connected and not self._stopping:
            log.warning("MQTT: desconectado (%s); tentando reconectar", reason_code)
        self.connected = False
        self.last_error = None if self._stopping else "desconectado: %s" % reason_code

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            if topic == "%s/status" % self.cfg.ha_prefix:
                if msg.payload.strip().lower() == b"online":     # o Home Assistant reiniciou
                    log.info("MQTT: Home Assistant ficou online; reenviando descoberta")
                    self.discovered.clear()
                    self.announce_all()
                return
            # nunca reprocessa o que a própria ponte publica
            if topic.startswith(self.cfg.mqtt_base_topic + "/") or topic.startswith(self.cfg.ha_prefix + "/"):
                return
            if getattr(msg, "retain", False):
                return        # mensagem antiga guardada pelo broker: não é leitura nova
            self.rx_count += 1
            self.on_meter_message(bytes(msg.payload), topic)
        except Exception:
            log.exception("MQTT: erro ao tratar mensagem de %s", topic)

    # ------------------------------------------------------------------ publicação
    def _publish(self, topic: str, payload: Any, retain: bool = False, qos: int = 0) -> None:
        c = self.client
        if c is None or not self.connected:
            return
        if not isinstance(payload, (str, bytes)):
            payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        c.publish(topic, payload, qos=qos, retain=retain)
        self.tx_count += 1

    def announce_all(self) -> None:
        for d in self.list_devices():
            self.announce(d)
            snap = self.get_snapshot(d["id"])
            if snap and d.get("online"):
                self.publish_state(snap)

    def announce(self, device: Dict[str, Any], force: bool = False) -> None:
        """Publica a descoberta (config) e a disponibilidade de um medidor."""
        if not self.connected:
            return
        t = ha_discovery.topics(self.cfg.mqtt_base_topic, device["id"])
        with self._lock:
            if self.cfg.ha_discovery and (force or self.discovered.get(device["id"]) != device.get("phases")):
                for topic, payload in ha_discovery.discovery_messages(
                        self.cfg.ha_prefix, self.cfg.mqtt_base_topic, device, self.cfg.public_url):
                    self._publish(topic, payload, retain=True, qos=1)
                self.discovered[device["id"]] = device.get("phases")
                log.info("MQTT: descoberta do Home Assistant publicada para o medidor %s", device["id"])
        self._publish(t["availability"], "online" if device.get("online") else "offline", retain=True, qos=1)

    def publish_state(self, snapshot: Dict[str, Any]) -> None:
        device = snapshot["device"]
        t = ha_discovery.topics(self.cfg.mqtt_base_topic, device["id"])
        self._publish(t["state"], ha_discovery.state_payload(snapshot))

    def set_availability(self, device_id: str, online: bool) -> None:
        t = ha_discovery.topics(self.cfg.mqtt_base_topic, device_id)
        self._publish(t["availability"], "online" if online else "offline", retain=True, qos=1)

    def forget(self, device_id: str) -> None:
        """Remove do Home Assistant os sensores de um medidor excluído."""
        if self.cfg.ha_discovery:
            for topic in ha_discovery.discovery_topics(self.cfg.ha_prefix, device_id):
                self._publish(topic, "", retain=True, qos=1)
        t = ha_discovery.topics(self.cfg.mqtt_base_topic, device_id)
        self._publish(t["availability"], "", retain=True, qos=1)
        self.discovered.pop(device_id, None)

    # ------------------------------------------------------------------ eventos do banco
    def on_store_event(self, event: Dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "reading":
            snap = event["snapshot"]
            device = snap["device"]
            if event.get("new_device") or event.get("came_online") or \
                    self.discovered.get(device["id"]) != device.get("phases"):
                self.announce(device)
            self.publish_state(snap)
        elif kind == "offline":
            self.set_availability(event["device"], False)
        elif kind == "device_updated":
            for d in self.list_devices():
                if d["id"] == event["device"]:
                    self.announce(d, force=True)
        elif kind == "device_deleted":
            self.forget(event["device"])
