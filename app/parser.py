"""Interpreta o que o medidor envia, em qualquer das formas conhecidas.

O firmware envia um JSON plano com os valores como texto. Dependendo do método e da
versão, esse JSON pode chegar de maneiras diferentes, e todas são aceitas aqui:

  * corpo JSON puro (HTTP POST ou MQTT);
  * corpo JSON enviado com Content-Type de formulário (o JSON vira a "chave" do form);
  * JSON dentro de um parâmetro (?json={...} ou data={...});
  * pares chave=valor na URL (HTTP GET) ou no corpo (form-urlencoded);
  * JSON embrulhado em {"payload": {...}} ou {"data": "..."}.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, unquote_plus

from .fields import ALL_FIELDS, COUNTER_FIELDS

KNOWN = frozenset(ALL_FIELDS)
# Basta um destes campos para reconhecer a mensagem como leitura do medidor.
MARKERS = ("uarms", "pt", "pa", "iarms", "ept_c", "epa_c")
THREE_PHASE_MARKERS = ("pb", "pc", "ubrms", "ucrms", "ibrms", "icrms", "qb", "qc", "epb_c", "epc_c")
ENVELOPES = ("payload", "data", "payload_raw", "dados", "json", "msg", "message", "value", "values")
ID_KEYS = ("id", "device_id", "deviceid", "device", "mac")

_ID_RE = re.compile(r"[^A-Za-z0-9_.\-]")


class ParseError(ValueError):
    """A mensagem não parece uma leitura do medidor."""


def to_float(v: Any) -> Optional[float]:
    """Converte "12.34", "12,34", 12.34 em float; devolve None para vazio/inválido."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
    elif isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        if "," in s and "." not in s:
            s = s.replace(",", ".")
        try:
            f = float(s)
        except ValueError:
            return None
    else:
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def sanitize_id(v: Any) -> str:
    s = _ID_RE.sub("_", str(v if v is not None else "").strip())[:48]
    return s or "1"


def _loads(s: str) -> Any:
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return None


def _find(obj: Any, depth: int = 0) -> Optional[Dict[str, Any]]:
    """Procura, em algo já decodificado, o dicionário que contém as grandezas."""
    if depth > 4 or obj is None:
        return None
    if isinstance(obj, str):
        s = obj.strip()
        if s[:1] in ("{", "["):
            return _find(_loads(s), depth + 1)
        return None
    if isinstance(obj, list):
        for item in obj[:20]:
            found = _find(item, depth + 1)
            if found:
                return found
        return None
    if isinstance(obj, dict):
        low = {str(k).strip().lower(): v for k, v in obj.items()}
        if any(m in low for m in MARKERS):
            return low
        for key in ENVELOPES:
            if key in low:
                found = _find(low[key], depth + 1)
                if found:
                    # preserva o id que veio fora do envelope
                    for ik in ID_KEYS:
                        if ik in low and "id" not in found:
                            found["id"] = low[ik]
                            break
                    return found
        # JSON usado como chave ou como valor de um formulário
        for k, v in list(low.items())[:20]:
            if k[:1] == "{":
                found = _find(k, depth + 1)
                if found:
                    return found
            if isinstance(v, (dict, list)) or (isinstance(v, str) and v.strip()[:1] in ("{", "[")):
                found = _find(v, depth + 1)
                if found:
                    return found
    return None


def _from_text(text: str) -> Optional[Dict[str, Any]]:
    s = text.strip()
    if not s:
        return None
    if s[:1] in ("{", "["):
        found = _find(_loads(s))
        if found:
            return found
    if "%7B" in s[:6].upper() or "%5B" in s[:6].upper():
        found = _find(_loads(unquote_plus(s).strip()))
        if found:
            return found
    if "=" in s or s[:1] == "{":
        pairs = parse_qsl(s, keep_blank_values=True)
        if pairs:
            found = _find(dict(pairs))
            if found:
                return found
    return None


def normalize(low: Dict[str, Any], counter_scale: float = 1.0) -> Dict[str, Any]:
    """Transforma o dicionário bruto (chaves minúsculas) em uma leitura normalizada.

    Resultado: {"id": str, <grandeza>: float|None, ..., "_phases": 1|3, "_extra": {...}}.
    Só entram grandezas que vieram na mensagem; as ausentes ficam fora do dicionário.
    counter_scale converte os contadores de energia para kWh (0.001 se o medidor enviar em Wh).
    """
    dev = None
    for ik in ID_KEYS:
        if low.get(ik) not in (None, ""):
            dev = low[ik]
            break
    out: Dict[str, Any] = {"id": sanitize_id(dev)}
    extra: Dict[str, Any] = {}
    for k, v in low.items():
        if k in ID_KEYS:
            continue
        if k in KNOWN:
            val = to_float(v)
            if val is not None and counter_scale != 1.0 and k in COUNTER_FIELDS:
                val = round(val * counter_scale, 6)
            out[k] = val
        elif isinstance(v, (str, int, float, bool)) and len(extra) < 24:
            extra[k[:40]] = v if not isinstance(v, str) else v[:120]

    three = any(k in low for k in THREE_PHASE_MARKERS)
    out["_phases"] = 3 if three else 1

    def fill(dst: str, src: str) -> None:
        if out.get(dst) is None and out.get(src) is not None:
            out[dst] = out[src]

    if three:
        if out.get("pt") is None:
            parts = [out.get(k) for k in ("pa", "pb", "pc") if out.get(k) is not None]
            if parts:
                out["pt"] = sum(parts)
        if out.get("itrms") is None:
            parts = [out.get(k) for k in ("iarms", "ibrms", "icrms") if out.get(k) is not None]
            if parts:
                out["itrms"] = sum(parts)
    else:
        # Monofásico: os totais são a própria fase A.
        fill("pt", "pa"); fill("qt", "qa"); fill("st", "sa"); fill("itrms", "iarms")
        fill("ept_c", "epa_c"); fill("ept_g", "epa_g")
        fill("pfa", "pft"); fill("pft", "pfa")
        fill("pa", "pt"); fill("epa_c", "ept_c"); fill("epa_g", "ept_g")

    if not any(out.get(m) is not None for m in MARKERS):
        raise ParseError("mensagem sem valores numéricos reconhecidos")
    out["_extra"] = extra
    return out


def parse_message(body: Any = b"", query: str = "", counter_scale: float = 1.0) -> Dict[str, Any]:
    """Interpreta corpo e/ou query string e devolve a leitura normalizada.

    Levanta ParseError se nada parecido com uma leitura for encontrado.
    """
    found: Optional[Dict[str, Any]] = None
    if isinstance(body, (dict, list)):
        found = _find(body)
    else:
        if isinstance(body, (bytes, bytearray)):
            text = bytes(body).decode("utf-8", errors="replace")
        else:
            text = str(body or "")
        found = _from_text(text.lstrip("﻿"))
    if not found and query:
        found = _from_text(query)
    if not found:
        raise ParseError("nenhuma grandeza do medidor encontrada na mensagem")
    return normalize(found, counter_scale)
