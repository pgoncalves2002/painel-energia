"""Partida do add-on: lê as opções do Home Assistant, descobre o broker MQTT e inicia o painel."""
from __future__ import annotations

import json
import os
import sys
import urllib.request

OPTIONS_FILE = os.environ.get("ADDON_OPTIONS", "/data/options.json")
SUPERVISOR = os.environ.get("SUPERVISOR_URL", "http://supervisor")
PANEL_PORT = 8099        # só o Home Assistant (ingress) fala com esta porta
METER_PORT = 8080        # porta publicada para o medidor
VIEW_PORT = 8081         # painel direto, sem o login do Home Assistant (opcional)
DTU_PORT = 8099          # porta do add-on Hoymiles DTU API
HOST_GATEWAY = "172.30.32.1"   # o próprio Home Assistant OS visto de dentro de um add-on


def log(msg: str) -> None:
    print("add-on: " + msg, flush=True)


def supervisor(path: str, quiet: bool = False):
    """Consulta a API do Supervisor; devolve None se não der (o painel funciona mesmo assim)."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    try:
        req = urllib.request.Request(SUPERVISOR + path, headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body.get("data") if body.get("result") == "ok" else None
    except Exception as exc:  # sem serviço MQTT, sem permissão, Supervisor ocupado...
        if not quiet:
            log("consulta %s sem resposta (%s)" % (path, exc))
        return None


def dtu_candidates(public_host=None) -> list:
    """Endereços prováveis do add-on Hoymiles DTU API, do mais direto ao mais genérico."""
    urls = []
    # a lista de add-ons pode exigir mais permissão do que este add-on tem; aí ficam os outros endereços
    for addon in (supervisor("/addons", quiet=True) or {}).get("addons") or []:
        slug = str(addon.get("slug") or "")
        if slug.endswith("hoymiles_dtu_api") and addon.get("state", "started") == "started":
            urls.append("http://%s:%d" % (slug.replace("_", "-"), DTU_PORT))
    urls.append("http://%s:%d" % (HOST_GATEWAY, DTU_PORT))
    if public_host:
        urls.append("http://%s:%d" % (public_host, DTU_PORT))
    return urls


def build_env(options: dict, env: dict) -> dict:
    out = dict(env)
    out.update({
        "ADDON": "1", "DATA_DIR": env.get("DATA_DIR", "/data"),
        "HTTP_PORT": str(PANEL_PORT), "INGEST_PORT": str(METER_PORT),
        "TARIFA_KWH": str(options.get("tarifa_kwh", 0.95)),
        "COUNTER_UNIT": str(options.get("unidade_contadores", "kwh")),
        "RAW_RETENTION_DAYS": str(options.get("dias_leituras", 400)),
        "OFFLINE_AFTER_S": str(options.get("segundos_offline", 180)),
        "MAX_POWER_KW": str(options.get("potencia_maxima_kw", 80)),
        "MQTT_TOPIC_IN": str(options.get("topico_mqtt") or "medidor/energia"),
        "DEMO": {"casa": "1", "solar": "solar"}.get(str(options.get("demonstracao", "desligada")), "0"),
        "HA_DISCOVERY": "true",
    })

    # painel direto: o mesmo painel numa porta própria, para abrir sem entrar no Home Assistant
    if options.get("painel_direto"):
        out["VIEW_PORT"] = str(VIEW_PORT)
        out["VIEW_READONLY"] = "false" if options.get("painel_direto_permite_alterar") else "true"
        user, pwd = str(options.get("painel_direto_usuario") or ""), str(options.get("painel_direto_senha") or "")
        if user and pwd:
            out["DASH_USER"], out["DASH_PASSWORD"] = user, pwd
        else:
            out.pop("DASH_USER", None)
            out.pop("DASH_PASSWORD", None)
    else:
        out["VIEW_PORT"] = "0"

    # fuso horário e endereço do Home Assistant na rede
    if not out.get("TZ"):
        info = supervisor("/info") or {}
        if info.get("timezone"):
            out["TZ"] = info["timezone"]
    me = supervisor("/addons/self/info") or {}
    port = (me.get("network") or {}).get("%d/tcp" % METER_PORT)
    out["PUBLIC_INGEST_PORT"] = str(port or METER_PORT)
    if not port and me:
        log("ATENÇÃO: a porta do medidor está desligada na aba Configuração (Rede) do add-on")
    net = supervisor("/network/info") or {}
    for iface in net.get("interfaces") or []:
        addrs = (iface.get("ipv4") or {}).get("address") or []
        if iface.get("primary") and addrs:
            out["PUBLIC_HOST"] = addrs[0].split("/")[0]
            break

    # geração solar: API do add-on Hoymiles DTU API (o endereço informado ou os prováveis)
    out.pop("SOLAR_URL", None)
    out.pop("SOLAR_AUTO", None)
    if options.get("solar_dtu", True):
        url = str(options.get("solar_dtu_url") or "").strip()
        if url:
            out["SOLAR_URL"] = url
            log("geração solar: API do DTU em %s" % url)
        else:
            out["SOLAR_URL"] = ",".join(dtu_candidates(out.get("PUBLIC_HOST")))
            out["SOLAR_AUTO"] = "1"

    # broker MQTT: o informado nas opções ou o do Home Assistant (add-on Mosquitto broker)
    if options.get("mqtt_host"):
        out.update({"MQTT_HOST": str(options["mqtt_host"]), "MQTT_PORT": str(options.get("mqtt_port") or 1883),
                    "MQTT_USERNAME": str(options.get("mqtt_usuario") or ""),
                    "MQTT_PASSWORD": str(options.get("mqtt_senha") or "")})
        log("broker MQTT das opções: %s" % options["mqtt_host"])
    else:
        mqtt = supervisor("/services/mqtt")
        if mqtt and mqtt.get("host"):
            out.update({"MQTT_HOST": str(mqtt["host"]), "MQTT_PORT": str(mqtt.get("port") or 1883),
                        "MQTT_USERNAME": str(mqtt.get("username") or ""), "MQTT_PASSWORD": str(mqtt.get("password") or "")})
            log("broker MQTT do Home Assistant encontrado: %s" % mqtt["host"])
        else:
            out["MQTT_HOST"] = ""
            log("nenhum broker MQTT encontrado: o painel funciona, mas os sensores não aparecem no Home Assistant. "
                "Instale o add-on Mosquitto broker e reinicie este add-on.")
    return out


def main() -> int:
    try:
        with open(OPTIONS_FILE, encoding="utf-8") as fh:
            options = json.load(fh)
    except (OSError, ValueError):
        options = {}
    os.environ.update(build_env(options, dict(os.environ)))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from app.__main__ import main as app_main
    return app_main()


if __name__ == "__main__":
    sys.exit(main())
