"""Constantes da integração Painel de Energia."""

DOMAIN = "painel_energia"
TITLE = "Painel de Energia"

DATA_DIR = "painel_energia"            # pasta do banco, dentro da pasta de configuração do Home Assistant
STATIC_URL = "/painel_energia_static"  # arquivos do painel (sem dados; os dados vêm da API, com login)
PANEL_URL = "painel-energia"           # endereço do painel na barra lateral
API_URL = "/api/painel_energia"

CONF_WEBHOOK_ID = "webhook_id"
CONF_LOCAL_ONLY = "local_only"
CONF_LISTEN_PORT = "listen_port"
CONF_MQTT_TOPIC = "mqtt_topic"
CONF_OFFLINE_AFTER = "offline_after_s"
CONF_RETENTION_DAYS = "raw_retention_days"
CONF_MAX_POWER_KW = "max_power_kw"
CONF_COUNTER_UNIT = "counter_unit"
CONF_SIDEBAR = "sidebar"

DEFAULTS = {
    CONF_LOCAL_ONLY: True,
    CONF_LISTEN_PORT: 0,
    CONF_MQTT_TOPIC: "",
    CONF_OFFLINE_AFTER: 180,
    CONF_RETENTION_DAYS: 400,
    CONF_MAX_POWER_KW: 80.0,
    CONF_COUNTER_UNIT: "kwh",
    CONF_SIDEBAR: True,
}

SIGNAL_DEVICE = DOMAIN + "_device"           # um medidor apareceu ou mudou (argumento: id)
SIGNAL_REMOVED = DOMAIN + "_removed"         # um medidor foi excluído (argumento: id)


def signal_update(device_id: str) -> str:
    """Sinal enviado a cada leitura nova de um medidor."""
    return "%s_update_%s" % (DOMAIN, device_id)
