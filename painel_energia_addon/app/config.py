"""Configuração lida de variáveis de ambiente (veja .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Mapping


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "sim", "on", "s", "y")


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(str(env.get(name, "")).strip())
    except ValueError:
        return default


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        return float(str(env.get(name, "")).strip().replace(",", "."))
    except ValueError:
        return default


@dataclass
class Config:
    # Servidor web / ingestão HTTP
    http_host: str = "0.0.0.0"
    http_port: int = 8080
    data_dir: str = "./data"
    tz: str = "America/Sao_Paulo"
    ingest_token: str = ""          # se definido, o medidor precisa enviar para /api/ingest/<token>
    dash_user: str = ""             # login opcional do painel (HTTP Basic)
    dash_password: str = ""
    public_url: str = ""            # ex.: http://192.168.0.10:8080 (vira o link do dispositivo no HA)
    ingest_port: int = 0            # porta extra que só recebe o medidor (0 = desligada); usada pelo add-on
    addon: bool = False             # rodando como add-on do Home Assistant (painel atrás do ingress)
    public_host: str = ""           # endereço que o medidor deve usar, quando conhecido (add-on)
    public_ingest_port: int = 0     # porta que o medidor deve usar, vista de fora do contêiner (add-on)
    view_port: int = 0              # add-on: porta extra com o painel fora do Home Assistant (0 = desligada)
    view_readonly: bool = True      # nessa porta, só visualizar (não altera configurações nem exclui dados)

    # MQTT (opcional): leitura do tópico do medidor e publicação para o Home Assistant
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_client_id: str = "painel-energia"
    mqtt_public_port: int = 0       # porta do broker vista pelo medidor (0 = a mesma de mqtt_port)
    mqtt_topics_in: List[str] = field(default_factory=lambda: ["medidor/energia"])
    mqtt_base_topic: str = "painel-energia"
    mqtt_meter_anonymous: bool = True   # broker do docker-compose: aceita o medidor sem login (só envio de leituras)
    ha_discovery: bool = True
    ha_prefix: str = "homeassistant"

    # Dados
    raw_retention_days: int = 400   # leituras brutas; 0 = guardar para sempre. Agregados nunca são apagados.
    offline_after_s: int = 180      # sem dados por esse tempo = medidor offline
    max_power_kw: float = 80.0      # teto físico usado para descartar saltos absurdos do contador
    counter_unit: str = "kwh"       # unidade dos contadores de energia enviados pelo medidor: kwh ou wh
    tariff_default: float = 0.95    # R$/kWh inicial (editável no painel)

    # Geração solar (opcional): API do add-on Hoymiles DTU API; vários endereços separados por vírgula
    solar_urls: List[str] = field(default_factory=list)
    solar_auto: bool = False        # endereços adivinhados (add-on): sem resposta, o solar só não aparece
    solar_poll_s: float = 5.0

    demo: bool = False              # gera um medidor simulado ("DEMO") para conhecer o painel
    demo_solar_kwp: float = 0.0     # DEMO=solar simula também geração fotovoltaica
    log_level: str = "INFO"

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "energia.db")

    @property
    def counter_scale(self) -> float:
        """Fator que leva os contadores do medidor para kWh."""
        return 0.001 if self.counter_unit == "wh" else 1.0

    @property
    def mqtt_enabled(self) -> bool:
        return bool(self.mqtt_host.strip())

    @property
    def meter_needs_no_login(self) -> bool:
        """O medidor pode publicar sem usuário e senha, embora o broker tenha login?

        Só dá para afirmar isso do broker que acompanha o painel (serviço "mosquitto" do docker-compose),
        que é configurado pelas mesmas variáveis. De um broker externo o painel não sabe as regras.
        """
        return (self.mqtt_host.strip().lower() == "mosquitto" and bool(self.mqtt_username)
                and self.mqtt_meter_anonymous)

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Config":
        d = cls()
        topics = [t.strip() for t in env.get("MQTT_TOPIC_IN", "").split(",") if t.strip()]
        return cls(
            http_host=env.get("HTTP_HOST", d.http_host) or d.http_host,
            http_port=_int(env, "HTTP_PORT", d.http_port),
            data_dir=env.get("DATA_DIR", d.data_dir) or d.data_dir,
            tz=env.get("TZ", d.tz) or d.tz,
            ingest_token=env.get("INGEST_TOKEN", "").strip(),
            dash_user=env.get("DASH_USER", "").strip(),
            dash_password=env.get("DASH_PASSWORD", ""),
            public_url=env.get("PUBLIC_URL", "").strip().rstrip("/"),
            ingest_port=_int(env, "INGEST_PORT", 0),
            addon=_bool(env, "ADDON", False),
            public_host=env.get("PUBLIC_HOST", "").strip(),
            public_ingest_port=_int(env, "PUBLIC_INGEST_PORT", 0),
            view_port=_int(env, "VIEW_PORT", 0),
            view_readonly=_bool(env, "VIEW_READONLY", True),
            mqtt_host=env.get("MQTT_HOST", "").strip(),
            mqtt_port=_int(env, "MQTT_PORT", d.mqtt_port),
            mqtt_username=env.get("MQTT_USERNAME", "").strip(),
            mqtt_password=env.get("MQTT_PASSWORD", ""),
            mqtt_client_id=env.get("MQTT_CLIENT_ID", d.mqtt_client_id).strip() or d.mqtt_client_id,
            mqtt_public_port=_int(env, "MQTT_PUBLIC_PORT", 0),
            mqtt_topics_in=topics or list(d.mqtt_topics_in),
            mqtt_base_topic=(env.get("MQTT_BASE_TOPIC", d.mqtt_base_topic).strip().strip("/") or d.mqtt_base_topic),
            mqtt_meter_anonymous=_bool(env, "MQTT_METER_ANONYMOUS", d.mqtt_meter_anonymous),
            ha_discovery=_bool(env, "HA_DISCOVERY", d.ha_discovery),
            ha_prefix=(env.get("HA_DISCOVERY_PREFIX", d.ha_prefix).strip().strip("/") or d.ha_prefix),
            raw_retention_days=max(0, _int(env, "RAW_RETENTION_DAYS", d.raw_retention_days)),
            offline_after_s=max(30, _int(env, "OFFLINE_AFTER_S", d.offline_after_s)),
            max_power_kw=max(1.0, _float(env, "MAX_POWER_KW", d.max_power_kw)),
            counter_unit="wh" if env.get("COUNTER_UNIT", "").strip().lower() == "wh" else "kwh",
            tariff_default=max(0.0, _float(env, "TARIFA_KWH", d.tariff_default)),
            solar_urls=[u.strip() for u in env.get("SOLAR_URL", "").split(",") if u.strip()],
            solar_auto=_bool(env, "SOLAR_AUTO", False),
            solar_poll_s=max(2.0, _float(env, "SOLAR_POLL_S", d.solar_poll_s)),
            demo=_bool(env, "DEMO", d.demo) or env.get("DEMO", "").strip().lower() == "solar",
            demo_solar_kwp=3.6 if env.get("DEMO", "").strip().lower() == "solar" else 0.0,
            log_level=(env.get("LOG_LEVEL", d.log_level) or d.log_level).upper(),
        )
