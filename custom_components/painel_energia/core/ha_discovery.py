"""Integração com o Home Assistant via MQTT Discovery.

Para cada medidor são publicadas mensagens de configuração (retidas) em

    <prefixo>/sensor/painel_energia_<id>/<chave>/config

e o estado, a cada leitura, em

    <base>/<id>/state            (JSON com todas as grandezas, já como números)
    <base>/<id>/availability     ("online" / "offline", retido)
    <base>/bridge/status         ("online" / "offline", retido, com last will)

O Home Assistant cria sozinho o dispositivo e os sensores, com unidade e classe certas.
Os sensores de energia (kWh, total_increasing) podem ser usados direto no painel Energia.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from . import __version__
from .fields import COUNTER_COL, COUNTER_FIELDS, PHASES

_SLUG = re.compile(r"[^a-z0-9_]+")


def slug(text: str) -> str:
    return _SLUG.sub("_", str(text).lower()).strip("_") or "x"


VOLTAGE_STATES = ["adequada", "precaria", "critica", "ausente"]


def _ent(key, name, dclass=None, unit=None, sclass="measurement", prec=None, enabled=True, diag=False, icon=None,
         options=None):
    return {"key": key, "name": name, "device_class": dclass, "unit": unit, "state_class": sclass,
            "precision": prec, "enabled": enabled, "diagnostic": diag, "icon": icon, "options": options}


def entities(phases: int = 3) -> List[Dict[str, Any]]:
    """Lista dos sensores publicados para um medidor com 1 ou 3 fases."""
    three = phases >= 3
    out: List[Dict[str, Any]] = []
    out.append(_ent("pt", "Potência ativa total" if three else "Potência ativa", "power", "W", prec=0))
    if three:
        for p in PHASES:
            out.append(_ent("p" + p, "Potência fase %s" % p.upper(), "power", "W", prec=0))
    for p in PHASES[: 3 if three else 1]:
        suffix = " fase %s" % p.upper() if three else ""
        out.append(_ent("u%srms" % p, "Tensão" + suffix, "voltage", "V", prec=1))
        out.append(_ent("i%srms" % p, "Corrente" + suffix, "current", "A", prec=2))
    if three:
        out.append(_ent("itrms", "Corrente total", "current", "A", prec=2))
    out.append(_ent("pft_pct", "Fator de potência", "power_factor", "%", prec=1))
    out.append(_ent("freq", "Frequência", "frequency", "Hz", prec=2))
    out.append(_ent("qt", "Potência reativa" + (" total" if three else ""), "reactive_power", "var", prec=0))
    out.append(_ent("st", "Potência aparente" + (" total" if three else ""), "apparent_power", "VA", prec=0))

    # Energia: totais acumulados pelo próprio painel (não zeram quando o contador do medidor zera).
    out.append(_ent("e_c_t", "Energia consumida", "energy", "kWh", "total_increasing", 2))
    out.append(_ent("e_g_t", "Energia injetada", "energy", "kWh", "total_increasing", 2))
    out.append(_ent("hoje_c", "Energia consumida hoje", "energy", "kWh", "total_increasing", 2))
    out.append(_ent("hoje_g", "Energia injetada hoje", "energy", "kWh", "total_increasing", 2))
    if three:
        for p in PHASES:
            out.append(_ent("e_c_" + p, "Energia consumida fase %s" % p.upper(), "energy", "kWh",
                            "total_increasing", 2))
        for p in PHASES:
            out.append(_ent("e_g_" + p, "Energia injetada fase %s" % p.upper(), "energy", "kWh",
                            "total_increasing", 2, enabled=False))
        for p in PHASES:
            out.append(_ent("pf%s_pct" % p, "Fator de potência fase %s" % p.upper(), "power_factor", "%",
                            prec=1, enabled=False))
            out.append(_ent("q" + p, "Potência reativa fase %s" % p.upper(), "reactive_power", "var",
                            prec=0, enabled=False))
            out.append(_ent("s" + p, "Potência aparente fase %s" % p.upper(), "apparent_power", "VA",
                            prec=0, enabled=False))
            out.append(_ent("pg" + p, "Ângulo tensão-corrente fase %s" % p.upper(), None, "°",
                            prec=1, enabled=False, icon="mdi:angle-acute"))
        for key, label in (("yuaub", "A-B"), ("yuauc", "A-C"), ("yubuc", "B-C")):
            out.append(_ent(key, "Ângulo entre tensões %s" % label, None, "°", prec=1, enabled=False,
                            icon="mdi:angle-acute"))
    else:
        out.append(_ent("pga", "Ângulo tensão-corrente", None, "°", prec=1, enabled=False, icon="mdi:angle-acute"))
    # Situação da tensão em relação às faixas de referência (para automações e avisos).
    for p in PHASES[: 3 if three else 1]:
        out.append(_ent("sit_u" + p, "Situação da tensão" + (" fase %s" % p.upper() if three else ""), "enum", None,
                        sclass=None, icon="mdi:sine-wave", options=list(VOLTAGE_STATES)))
    out.append(_ent("tpsd", "Temperatura do medidor", "temperature", "°C", prec=1, diag=True))
    out.append(_ent("rssi_wifi", "Sinal Wi-Fi", "signal_strength", "dBm", prec=0, diag=True))
    return out


def topics(base: str, device_id: str) -> Dict[str, str]:
    dev = slug(device_id)
    return {
        "state": "%s/%s/state" % (base, dev),
        "availability": "%s/%s/availability" % (base, dev),
        "bridge": "%s/bridge/status" % base,
    }


def discovery_messages(prefix: str, base: str, device: Dict[str, Any], public_url: str = "",
                       only_keys: Optional[List[str]] = None) -> List[Tuple[str, Dict[str, Any]]]:
    """Mensagens de configuração (tópico, payload) de todos os sensores de um medidor."""
    dev = slug(device["id"])
    t = topics(base, device["id"])
    dev_block: Dict[str, Any] = {
        "identifiers": ["painel_energia_%s" % dev],
        "name": device.get("name") or "Medidor de energia",
        "manufacturer": "IE Tecnologia",
        "model": device.get("model") or "SM-3W Lite",
    }
    if public_url:
        dev_block["configuration_url"] = public_url
    msgs: List[Tuple[str, Dict[str, Any]]] = []
    for e in entities(int(device.get("phases") or 3)):
        if only_keys is not None and e["key"] not in only_keys:
            continue
        payload: Dict[str, Any] = {
            "name": e["name"],
            "unique_id": "painel_energia_%s_%s" % (dev, e["key"]),
            "state_topic": t["state"],
            "value_template": "{{ value_json.%s | default(none) }}" % e["key"],
            "availability": [{"topic": t["bridge"]}, {"topic": t["availability"]}],
            "availability_mode": "all",
            "device": dev_block,
            "origin": {"name": "Painel de Energia", "sw_version": __version__},
        }
        if e["device_class"]:
            payload["device_class"] = e["device_class"]
        if e["unit"]:
            payload["unit_of_measurement"] = e["unit"]
        if e["state_class"]:
            payload["state_class"] = e["state_class"]
        if e["precision"] is not None:
            payload["suggested_display_precision"] = e["precision"]
        if not e["enabled"]:
            payload["enabled_by_default"] = False
        if e["diagnostic"]:
            payload["entity_category"] = "diagnostic"
        if e["icon"]:
            payload["icon"] = e["icon"]
        if e["options"]:
            payload["options"] = e["options"]
        msgs.append(("%s/sensor/painel_energia_%s/%s/config" % (prefix, dev, e["key"]), payload))
    return msgs


def discovery_topics(prefix: str, device_id: str, phases: int = 3) -> List[str]:
    """Tópicos de configuração de um medidor (para limpar quando ele é excluído)."""
    dev = slug(device_id)
    keys = {e["key"] for e in entities(3)} | {e["key"] for e in entities(1)}
    return ["%s/sensor/painel_energia_%s/%s/config" % (prefix, dev, k) for k in sorted(keys)]


def _r(v: Optional[float], nd: int) -> Optional[float]:
    return None if v is None else round(v, nd)


def state_payload(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """JSON de estado publicado a cada leitura (o mesmo para todos os sensores do medidor)."""
    r = snapshot.get("reading") or {}
    out: Dict[str, Any] = {"ts": snapshot.get("ts")}
    for k, v in r.items():
        if k in COUNTER_FIELDS:
            continue
        if k.startswith(("pf",)):
            out[k] = _r(v, 3)
        elif k.startswith(("u", "i", "freq", "pg", "y")):
            out[k] = _r(v, 2)
        else:
            out[k] = _r(v, 1)
    for k in ("pfa", "pfb", "pfc", "pft"):
        v = r.get(k)
        out[k + "_pct"] = None if v is None else round(abs(v) * 100.0, 1)
    totals = snapshot.get("totals") or {}
    for col in COUNTER_COL.values():
        out["e_" + col] = _r(totals.get(col), 3)
    today = snapshot.get("today") or {}
    out["hoje_c"] = _r(today.get("c_t"), 3)
    out["hoje_g"] = _r(today.get("g_t"), 3)
    for p, cat in (snapshot.get("voltage") or {}).items():
        out["sit_u" + p] = cat
    # leitura bruta dos contadores do medidor, para conferência
    out["medidor_ept_c"] = r.get("ept_c")
    out["medidor_ept_g"] = r.get("ept_g")
    return out
