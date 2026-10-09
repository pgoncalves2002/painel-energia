#!/usr/bin/env python3
"""Finge ser o medidor: envia leituras simuladas para o painel, do mesmo jeito que o SM-3W Lite.

Serve para testar a instalação de ponta a ponta antes de configurar o medidor de verdade.

Exemplos:

    # HTTP POST com JSON, a cada 30 s (como o medidor no método "Padrão / HTTP POST")
    python3 tools/simulador.py --url http://192.168.0.10:8080/api/ingest

    # HTTP GET com os valores na URL
    python3 tools/simulador.py --modo get --url http://192.168.0.10:8080/api/ingest

    # MQTT, publicando no tópico que o painel escuta
    python3 tools/simulador.py --modo mqtt --mqtt-host 192.168.0.10 --topico medidor/energia

    # casa com energia solar, leituras a cada 5 s, 20 envios
    python3 tools/simulador.py --solar 3.6 --intervalo 5 --vezes 20
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.simulate import HouseSim, as_meter_payload  # noqa: E402
from app.timeutil import Clock  # noqa: E402

MONO_KEYS = ("id", "pa", "qa", "sa", "uarms", "iarms", "pft", "pga", "freq", "epa_c", "epa_g", "tpsd", "rssi_wifi")


def build_payload(sim: HouseSim, ts: int, mono: bool) -> dict:
    payload = as_meter_payload(sim.reading(ts))
    if mono:
        payload["pft"] = payload["pfa"]
        return {k: payload[k] for k in MONO_KEYS}
    return payload


def send_http(url: str, modo: str, payload: dict, timeout: float) -> str:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # rede local: sem proxy
    if modo == "get":
        sep = "&" if "?" in url else "?"
        req = urllib.request.Request(url + sep + urllib.parse.urlencode(payload), method="GET")
    else:
        ctype = "application/json" if modo == "post" else "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=json.dumps(payload, separators=(",", ":")).encode(),
                                     headers={"Content-Type": ctype}, method="POST")
    try:
        with opener.open(req, timeout=timeout) as resp:
            return "%s %s" % (resp.status, resp.read(80).decode("utf-8", "replace").strip())
    except urllib.error.HTTPError as exc:
        return "ERRO HTTP %s %s" % (exc.code, exc.read(120).decode("utf-8", "replace").strip())
    except Exception as exc:  # conexão recusada, tempo esgotado...
        return "ERRO %s" % exc


def main() -> int:
    ap = argparse.ArgumentParser(description="Simulador do medidor IE Tecnologia (SM-3W Lite / SM-W Lite)")
    ap.add_argument("--modo", choices=("post", "form", "get", "mqtt"), default="post",
                    help="post = JSON; form = JSON com Content-Type de formulário; get = valores na URL; mqtt")
    ap.add_argument("--url", default="http://127.0.0.1:8080/api/ingest", help="endereço de ingestão do painel")
    ap.add_argument("--id", default="SIMULADOR", help="ID do equipamento (campo 'id' da mensagem)")
    ap.add_argument("--intervalo", type=float, default=30.0, help="segundos entre envios (o medidor real: mín. 30)")
    ap.add_argument("--vezes", type=int, default=0, help="número de envios (0 = até interromper com Ctrl+C)")
    ap.add_argument("--solar", type=float, default=0.0, help="potência do sistema solar em kWp (0 = sem solar)")
    ap.add_argument("--tensao", type=float, default=127.0, help="tensão nominal fase-neutro (127 ou 220)")
    ap.add_argument("--mono", action="store_true", help="envia como o SM-W Lite monofásico (13 campos)")
    ap.add_argument("--fuso", default=os.environ.get("TZ") or "America/Sao_Paulo")
    ap.add_argument("--mqtt-host", default="127.0.0.1")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--mqtt-user", default="")
    ap.add_argument("--mqtt-pass", default="")
    ap.add_argument("--topico", default="medidor/energia")
    args = ap.parse_args()

    sim = HouseSim(Clock(args.fuso), device_id=args.id, solar_kwp=args.solar, v_nom=args.tensao)
    now = int(time.time())
    sim.counters.update({"epa_c": 512.4, "epb_c": 431.7, "epc_c": 389.2, "ept_c": 1333.3})   # medidor já em uso
    sim.reading(now - int(args.intervalo))

    client = None
    if args.modo == "mqtt":
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            print("Para o modo mqtt instale a biblioteca: pip install paho-mqtt", file=sys.stderr)
            return 2
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="simulador-medidor")
        if args.mqtt_user:
            client.username_pw_set(args.mqtt_user, args.mqtt_pass or None)
        client.connect(args.mqtt_host, args.mqtt_port, keepalive=60)
        client.loop_start()
        print("MQTT %s:%s  tópico %s" % (args.mqtt_host, args.mqtt_port, args.topico))
    else:
        print("HTTP %s  %s" % (args.modo.upper(), args.url))

    sent = 0
    try:
        while True:
            payload = build_payload(sim, int(time.time()), args.mono)
            if client is not None:
                info = client.publish(args.topico, json.dumps(payload, separators=(",", ":")), qos=0)
                info.wait_for_publish(timeout=5)
                result = "publicado" if info.is_published() else "NÃO publicado"
            else:
                result = send_http(args.url, args.modo, payload, timeout=8)
            sent += 1
            print("%s  pt=%8s W  ept_c=%9s kWh  -> %s" % (time.strftime("%H:%M:%S"), payload.get("pt", payload.get("pa")),
                                                         payload.get("ept_c", payload.get("epa_c")), result), flush=True)
            if args.vezes and sent >= args.vezes:
                break
            time.sleep(max(0.2, args.intervalo))
    except KeyboardInterrupt:
        print("\nInterrompido.")
    finally:
        if client is not None:
            client.loop_stop()
            client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
