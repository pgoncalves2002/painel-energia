"""Núcleo comum aos dois modos de uso do painel.

O mesmo código atende o servidor independente (app.server) e a integração do Home Assistant
(custom_components/painel_energia): gravação das leituras, diagnóstico das mensagens recebidas
e a API de consulta, sem depender de como o HTTP é servido.
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta
from typing import Any, Deque, Dict, Iterator, List, Mapping, Optional, Tuple

from . import __version__, analytics, voltage
from . import solar as solar_flow
from .fields import ALL_FIELDS, ENERGY_COLS, FIELD_META, GROUP_LABELS, INSTANT_FIELDS
from .parser import ParseError, parse_message
from .store import Store

log = logging.getLogger("painel.nucleo")

MAX_BODY = 64 * 1024          # maior mensagem aceita do medidor
PURGE_EVERY_S = 6 * 3600


class ApiError(Exception):
    """Erro com código HTTP e mensagem para quem chamou a API."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class Diagnostics:
    """Últimas mensagens recebidas (aceitas ou não), para conferir a ligação com o medidor."""

    def __init__(self, size: int = 60):
        self.items: Deque[Dict[str, Any]] = deque(maxlen=size)
        self.ok = 0
        self.failed = 0
        self.by_source: Dict[str, int] = {}
        self._lock = threading.Lock()

    def add(self, ok: bool, source: str, **info: Any) -> None:
        with self._lock:
            if ok:
                self.ok += 1
                self.by_source[source] = self.by_source.get(source, 0) + 1
            else:
                self.failed += 1
            item = {"ts": time.time(), "ok": ok, "source": source}
            item.update(info)
            self.items.appendleft(item)

    def dump(self) -> Dict[str, Any]:
        with self._lock:
            return {"ok": self.ok, "failed": self.failed, "by_source": dict(self.by_source),
                    "recent": list(self.items)}


class Reply:
    """Resposta que é um arquivo para baixar, em vez de JSON.

    Vem de uma de duas formas: `chunks` (pedaços gerados aos poucos, a consumir na mesma thread
    em que foram criados) ou `file` (arquivo temporário já pronto, que quem envia deve apagar).
    """

    def __init__(self, ctype: str, filename: str, chunks: Optional[Iterator[bytes]] = None,
                 file: Optional[str] = None, size: Optional[int] = None):
        self.ctype = ctype
        self.filename = filename
        self.chunks = chunks
        self.file = file
        self.size = size

    def to_file(self) -> "Reply":
        """Grava os pedaços em um arquivo temporário (para hospedeiros que enviam de outra thread)."""
        if self.chunks is not None:
            fd, path = tempfile.mkstemp(prefix="painel-energia-", suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as fh:
                    for chunk in self.chunks:
                        fh.write(chunk)
            except Exception:
                discard(path)
                raise
            self.chunks = None
            self.file = path
            self.size = os.path.getsize(path)
        return self


def discard(path: Optional[str]) -> None:
    if path:
        try:
            os.remove(path)
        except OSError:
            pass


class Query:
    """Parâmetros de uma consulta (?a=1&b=2), já com um valor por nome."""

    def __init__(self, params: Optional[Mapping[str, Any]] = None):
        self._p = dict(params or {})

    def get(self, name: str, default: Optional[str] = None) -> Optional[str]:
        val = self._p.get(name)
        if isinstance(val, (list, tuple)):
            val = val[0] if val else None
        return default if val is None else str(val)

    def integer(self, name: str, default: Optional[int] = None) -> Optional[int]:
        raw = self.get(name)
        if raw is None or raw == "":
            return default
        try:
            return int(float(raw))
        except ValueError:
            raise ApiError(400, "parâmetro %s inválido" % name)

    def time_range(self, default_span: int = 86400, max_span: int = 366 * 86400 * 6) -> Tuple[int, int]:
        now = int(time.time())
        t1 = self.integer("to", now + 1)
        t0 = self.integer("from", t1 - default_span)
        if t1 <= t0:
            raise ApiError(400, "intervalo inválido")
        if t1 - t0 > max_span:
            t0 = t1 - max_span
        return t0, t1


class Core:
    """Gravação das leituras e API de consulta sobre um banco (Store)."""

    def __init__(self, store: Store, counter_unit: str = "kwh", raw_retention_days: int = 400,
                 protected_device: Optional[str] = None):
        self.store = store
        self.clock = store.clock
        self.diag = Diagnostics()
        self.started = time.time()
        self.counter_unit = "wh" if str(counter_unit).strip().lower() == "wh" else "kwh"
        self.raw_retention_days = max(0, int(raw_retention_days))
        self.protected_device = protected_device       # medidor que não pode ser excluído (demonstração)
        self.solar = None                              # SolarSource opcional (geração do DTU Hoymiles)
        self._last_purge = 0.0

    @property
    def counter_scale(self) -> float:
        """Fator que leva os contadores do medidor para kWh."""
        return 0.001 if self.counter_unit == "wh" else 1.0

    # ------------------------------------------------------------------ ingestão
    def ingest_raw(self, body: bytes, query: str, source: str, **meta: Any) -> Dict[str, Any]:
        """Interpreta e grava uma mensagem do medidor. Nunca levanta exceção."""
        body = (body or b"")[:MAX_BODY]
        snippet = (body[:600].decode("utf-8", errors="replace") if body else "") or (query or "")[:600]
        try:
            reading = parse_message(body, query, counter_scale=self.counter_scale)
        except ParseError as exc:
            self.diag.add(False, source, error=str(exc), snippet=snippet, size=len(body), **meta)
            return {"ok": False, "error": str(exc)}
        try:
            res = self.store.ingest(reading, source=source)
        except Exception as exc:
            log.exception("Erro ao gravar leitura")
            self.diag.add(False, source, error="erro ao gravar: %s" % exc, snippet=snippet, **meta)
            return {"ok": False, "error": "erro interno ao gravar"}
        if res.get("skipped"):
            msg = ("o relógio do servidor está atrás da última leitura gravada; aguardando o acerto da hora"
                   if res.get("reason") == "relogio" else "leitura repetida ou fora de ordem")
            self.diag.add(False, source, error=msg, device=reading["id"], snippet=snippet, **meta)
            return {"ok": False, "error": msg, "pending": True}
        self.diag.add(True, source, device=reading["id"], snippet=snippet, size=len(body),
                      fields=sum(1 for f in ALL_FIELDS if reading.get(f) is not None),
                      extra=sorted(reading.get("_extra") or {}), **meta)
        return {"ok": True, "device": res["device"], "ts": res["ts"], "new_device": res.get("new_device", False)}

    def reject(self, source: str, error: str, **meta: Any) -> None:
        """Registra uma tentativa recusada antes de chegar à leitura (token errado, mensagem vazia...)."""
        self.diag.add(False, source, error=error, **meta)

    # ------------------------------------------------------------------ manutenção periódica
    def maintenance(self) -> List[str]:
        """Marca medidores parados como offline e aplica a retenção. Devolve os que ficaram offline."""
        offline = self.store.check_online()
        if time.time() - self._last_purge > PURGE_EVERY_S:
            self._last_purge = time.time()
            n = self.store.purge(self.raw_retention_days)
            if n:
                log.info("Retenção: %d leituras individuais antigas removidas", n)
        return offline

    # ------------------------------------------------------------------ API: leitura
    def status(self) -> Dict[str, Any]:
        """Parte do /api/status que não depende do hospedeiro."""
        now = time.time()
        return {
            "app": "Painel de Energia", "version": __version__, "now": now, "uptime": now - self.started,
            "tz": self.clock.name, "tz_offset": self.clock.offset(now),
            "devices": self.store.devices(),
            "settings": self.store.settings(),
            "db": {"size": self.store.db_size(), "retention_days": self.raw_retention_days},
            "counter_unit": self.counter_unit,
            "solar": self.solar.state() if self.solar is not None else None,
        }

    def _device(self, q: Query) -> str:
        dev = q.get("device") or self.store.default_device()
        if not dev or self.store.state(dev) is None:
            raise ApiError(404, "nenhum medidor com esse id (ou nenhum dado recebido ainda)")
        return dev

    def api_get(self, route: str, params: Optional[Mapping[str, Any]] = None) -> Any:
        """Atende um GET da API. Devolve um objeto para virar JSON ou um Reply (arquivo)."""
        store = self.store
        q = params if isinstance(params, Query) else Query(params)
        low = (route.rstrip("/") or "/").lower()

        if low == "/api/fields":
            return {"fields": [FIELD_META[f] for f in ALL_FIELDS], "groups": GROUP_LABELS,
                    "instant": list(INSTANT_FIELDS)}
        if low == "/api/devices":
            return {"devices": store.devices()}
        if low == "/api/live":
            dev = self._device(q)
            snap = store.snapshot(dev)
            snap["now"] = time.time()
            snap["settings"] = store.settings()
            v_nom = snap["device"].get("v_nom")
            snap["limits"] = voltage.limits(v_nom) if v_nom else None
            now = int(snap["now"])
            last30 = store.energy_sum(dev, self.clock.add_days(now, -29), self.clock.add_days(now, 1))
            snap["mode"] = analytics.effective_mode(snap["settings"], last30)
            if self.solar is not None:
                sol = self.solar.state()
                snap["solar"] = sol
                if sol["found"] or not sol["auto"]:
                    r = snap.get("reading") or {}
                    snap["flow"] = solar_flow.compute_flow(r.get("pt"), bool(snap["device"].get("online")), sol,
                                                           snap["settings"].get("solar_ref", "auto"), snap["mode"])
            return snap
        if low == "/api/summary":
            return analytics.summary(store, self._device(q))
        if low == "/api/series":
            dev = self._device(q)
            t0, t1 = q.time_range()
            fields = [f.strip().lower() for f in (q.get("fields") or "pt").split(",") if f.strip()]
            return store.series(dev, fields, t0, t1, q.integer("points", 800) or 800, q.integer("res"))
        if low == "/api/energy":
            dev = self._device(q)
            t0, t1 = q.time_range(default_span=86400)
            group = q.get("group", "hour")
            if group not in ("15min", "hour", "day", "month"):
                raise ApiError(400, "grupo inválido")
            limit = {"15min": 40 * 86400, "hour": 120 * 86400}.get(group)
            if limit and t1 - t0 > limit:
                t0 = t1 - limit
            out = store.energy(dev, t0, t1, group)
            s = store.settings()
            out["tariff"], out["credit"] = s["tariff"], s["credit"]
            return out
        if low == "/api/consumo":
            return self.consumo(self._device(q), q.get("period", "day") or "day", q.get("date"))
        if low == "/api/check":
            out = analytics.counter_check(store, self._device(q))
            out["counter_unit"] = self.counter_unit
            return out
        if low == "/api/quality":
            dev = self._device(q)
            t0, t1 = q.time_range(default_span=7 * 86400, max_span=92 * 86400)
            return analytics.quality(store, dev, t0, t1)
        if low == "/api/events":
            return {"events": store.events(q.get("device") or None, q.integer("limit", 100) or 100,
                                           q.integer("from"), q.integer("to"))}
        if low == "/api/readings":
            dev = self._device(q)
            now = int(time.time())
            limit = max(1, min(q.integer("limit", 60) or 60, 2000))
            rows = store.raw_rows(dev, q.integer("from", 0) or 0, q.integer("to", now + 1) or now + 1,
                                  limit=limit, desc=True)
            return {"fields": list(ALL_FIELDS), "rows": [list(r) for r in rows]}
        if low == "/api/diagnostics":
            return self.diag.dump()
        if low == "/api/export.csv":
            return self.export_csv(q)
        if low == "/api/backup":
            return self.backup()
        raise ApiError(404, "rota não encontrada")

    # ------------------------------------------------------------------ API: alterações
    def api_write(self, method: str, route: str, data: Optional[Dict[str, Any]] = None) -> Any:
        """Atende POST/DELETE da API (configurações e medidores)."""
        store = self.store
        data = data or {}
        if not isinstance(data, dict):
            raise ApiError(400, "JSON inválido")
        route = route.rstrip("/") or "/"
        low = route.lower()
        if method == "POST" and low == "/api/settings":
            try:
                return {"settings": store.update_settings(data)}
            except (ValueError, TypeError) as exc:
                raise ApiError(400, str(exc))
        if low.startswith("/api/devices/"):
            rest = route[len("/api/devices/"):].split("/")
            dev = rest[0]
            action = rest[1].lower() if len(rest) > 1 else ""
            if store.state(dev) is None:
                raise ApiError(404, "medidor não encontrado")
            if method == "DELETE" or (method == "POST" and action == "delete"):
                if self.protected_device and dev == self.protected_device:
                    raise ApiError(409, "o medidor de demonstração volta enquanto DEMO estiver ligado")
                store.delete_device(dev)
                return {"ok": True}
            if method == "POST" and not action:
                return {"device": store.update_device(dev, name=data.get("name"), labels=data.get("labels"))}
        raise ApiError(404, "rota não encontrada")

    # ------------------------------------------------------------------ consumo por período
    def resolve_period(self, period: str, date_str: Optional[str], now: Optional[float] = None) -> Dict[str, Any]:
        """Converte (período, data âncora) em limites no calendário local do servidor."""
        clock = self.clock
        now = int(now or time.time())
        today = clock.local(now).date()
        try:
            anchor = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else today
        except ValueError:
            raise ApiError(400, "data inválida (use AAAA-MM-DD)")
        if anchor > today:
            anchor = today
        if period == "day":
            a, b = anchor, anchor + timedelta(days=1)
            pa, pb = anchor - timedelta(days=1), anchor
            group, step = "hour", timedelta(days=1)
            nxt, prv = anchor + step, anchor - step
        elif period == "week":
            a, b = anchor - timedelta(days=6), anchor + timedelta(days=1)
            pa, pb = a - timedelta(days=7), a
            group = "day"
            nxt, prv = anchor + timedelta(days=7), anchor - timedelta(days=7)
        elif period == "month":
            a = anchor.replace(day=1)
            b = (a.replace(year=a.year + 1, month=1) if a.month == 12 else a.replace(month=a.month + 1))
            pb = a
            pa = (a.replace(year=a.year - 1, month=12) if a.month == 1 else a.replace(month=a.month - 1))
            group = "day"
            nxt, prv = b, pa
        elif period == "year":
            a, b = date(anchor.year, 1, 1), date(anchor.year + 1, 1, 1)
            pa, pb = date(anchor.year - 1, 1, 1), a
            group = "month"
            nxt, prv = b, pa
        else:
            raise ApiError(400, "período inválido (day, week, month ou year)")
        t0, t1 = clock.epoch(a), clock.epoch(b)
        return {
            "period": period, "date": anchor.isoformat(), "group": group,
            "from": t0, "to": t1, "from_date": a.isoformat(), "to_date": (b - timedelta(days=1)).isoformat(),
            "prev_from": clock.epoch(pa), "prev_to": clock.epoch(pb),
            "prev_date": prv.isoformat(), "next_date": min(nxt, today).isoformat(),
            "is_current": t0 <= now < t1, "has_next": b <= today, "now": now,
        }

    def consumo(self, dev: str, period: str, date_str: Optional[str]) -> Dict[str, Any]:
        p = self.resolve_period(period, date_str)
        s = self.store.settings()
        data = self.store.energy(dev, p["from"], p["to"], p["group"])
        prev = self.store.energy_sum(dev, p["prev_from"], p["prev_to"])
        out: Dict[str, Any] = {"period": p, "rows": data["rows"], "totals": data["totals"],
                               "prev_totals": prev, "tariff": s["tariff"], "credit": s["credit"]}
        if p["is_current"]:
            elapsed = p["now"] - p["from"]
            out["prev_same_time"] = analytics.energy_until(self.store, dev, p["prev_from"],
                                                           min(p["prev_from"] + elapsed, p["prev_to"]))
        st = self.store.state(dev)
        out["first_data"] = st.first_seen if st else None
        return out

    # ------------------------------------------------------------------ exportações
    def export_csv(self, params: Any) -> Reply:
        """CSV das leituras individuais (kind=bruto) ou da energia por 15 min, hora, dia ou mês."""
        q = params if isinstance(params, Query) else Query(params)
        dev = self._device(q)
        t0, t1 = q.time_range(default_span=86400)
        kind = (q.get("kind", "bruto") or "bruto").lower()
        br = (q.get("fmt", "br") or "br").lower() != "en"
        name = "energia_%s_%s_%s.csv" % (dev, kind, self.clock.local(t0).strftime("%Y%m%d"))
        return Reply("text/csv; charset=utf-8", name, chunks=self._csv_chunks(dev, t0, t1, kind, br))

    def _csv_chunks(self, dev: str, t0: int, t1: int, kind: str, br: bool) -> Iterator[bytes]:
        store, clock = self.store, self.clock
        sep, dec = (";", ",") if br else (",", ".")

        def num(v: Any) -> str:
            if v is None:
                return ""
            text = ("%.4f" % float(v)).rstrip("0").rstrip(".")
            if text in ("", "-", "-0"):
                text = "0"
            return text.replace(".", dec) if dec != "." else text

        yield b"\xef\xbb\xbf"          # BOM: o Excel reconhece UTF-8
        if kind == "bruto":
            yield (sep.join(["data_hora", "epoch"] + list(ALL_FIELDS)) + "\r\n").encode("utf-8")
            buf: List[str] = []
            for r in store.raw_rows(dev, t0, t1):
                buf.append(sep.join([clock.iso(r[0]), str(r[0])] + [num(v) for v in r[1:]]))
                if len(buf) >= 500:
                    yield ("\r\n".join(buf) + "\r\n").encode("utf-8")
                    buf = []
            if buf:
                yield ("\r\n".join(buf) + "\r\n").encode("utf-8")
            return
        group = {"15min": "15min", "hora": "hour", "hour": "hour", "dia": "day", "day": "day",
                 "mes": "month", "month": "month"}.get(kind)
        if not group:
            yield "tipo de exportação desconhecido\r\n".encode("utf-8")
            return
        head = ["inicio", "epoch", "consumo_a_kwh", "consumo_b_kwh", "consumo_c_kwh", "consumo_total_kwh",
                "geracao_a_kwh", "geracao_b_kwh", "geracao_c_kwh", "geracao_total_kwh", "estimado"]
        yield (sep.join(head) + "\r\n").encode("utf-8")
        for row in store.energy(dev, t0, t1, group)["rows"]:
            if not row["n"]:
                continue
            yield (sep.join([clock.iso(row["start"]), str(row["start"])] + [num(row[c]) for c in ENERGY_COLS]
                            + ["1" if row["est"] else "0"]) + "\r\n").encode("utf-8")

    def backup(self) -> Reply:
        """Cópia consistente do banco em um arquivo temporário (quem envia apaga depois)."""
        fd, path = tempfile.mkstemp(prefix="painel-energia-", suffix=".db")
        os.close(fd)
        try:
            self.store.backup_to(path)
        except Exception:
            discard(path)
            raise
        name = "painel-energia-%s.db" % self.clock.local(time.time()).strftime("%Y%m%d-%H%M")
        return Reply("application/octet-stream", name, file=path, size=os.path.getsize(path))
