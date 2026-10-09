"""Banco de dados (SQLite) e pipeline de ingestão.

Tabelas principais:

  readings   uma linha por leitura recebida do medidor (dado bruto, com retenção configurável)
  stat15     média/mín./máx. de cada grandeza por intervalo de 15 min (guardado para sempre)
  energy15   energia consumida/gerada por intervalo de 15 min, calculada pela diferença dos
             contadores do medidor (guardado para sempre)
  events     ocorrências: tensão fora da faixa, falta de fase, medidor sem enviar, contador zerado

Toda escrita passa por um único lock; cada thread usa a própria conexão (modo WAL), então
as consultas do painel não bloqueiam a chegada de leituras.
"""
from __future__ import annotations

import bisect
import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import voltage
from .energy import BUCKET, GLITCH_KWH, counter_delta, integrate_power, split_buckets
from .fields import (ALL_FIELDS, COUNTER_COL, COUNTER_FIELDS, ENERGY_COLS, FIELD_META,
                     INSTANT_FIELDS, PHASE_FIELDS, PHASES, round_field)
from .timeutil import Clock

log = logging.getLogger("painel.store")

SCHEMA_VERSION = 1
GAP_EST_S = 300          # lacuna maior que isso: a energia repartida no intervalo é marcada como estimada
MAX_INTEGRATE_S = 300    # sem contadores, integra potência apenas entre leituras próximas
VOLT_DEBOUNCE = 2        # leituras consecutivas para confirmar mudança de faixa de tensão
CLOCK_PATIENCE = 10      # leituras toleradas com o relógio do servidor atrás da última gravação
FLAG_EVENT_EVERY_S = 3600   # no máximo uma ocorrência de "contador corrigido" por hora, por medidor
LADDER = (60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 604800)

PHASE_ENERGY = (("pa", "c_a", "g_a"), ("pb", "c_b", "g_b"), ("pc", "c_c", "g_c"), ("pt", "c_t", "g_t"))


def _schema() -> str:
    reading_cols = ", ".join("%s REAL" % f for f in ALL_FIELDS)
    stat_cols = ", ".join("{0}_avg REAL, {0}_min REAL, {0}_max REAL".format(f) for f in INSTANT_FIELDS)
    energy_cols = ", ".join("%s REAL NOT NULL DEFAULT 0" % c for c in ENERGY_COLS)
    return """
    CREATE TABLE IF NOT EXISTS devices (
      id TEXT PRIMARY KEY,
      name TEXT,
      model TEXT,
      phases INTEGER NOT NULL DEFAULT 3,
      labels TEXT,
      v_nom REAL,
      first_seen INTEGER,
      last_seen INTEGER,
      n_readings INTEGER NOT NULL DEFAULT 0,
      last_source TEXT
    );
    CREATE TABLE IF NOT EXISTS readings (
      device_id TEXT NOT NULL, ts INTEGER NOT NULL, %(reading_cols)s, extra TEXT,
      PRIMARY KEY (device_id, ts)
    ) WITHOUT ROWID;
    CREATE TABLE IF NOT EXISTS stat15 (
      device_id TEXT NOT NULL, ts INTEGER NOT NULL, n INTEGER NOT NULL, %(stat_cols)s,
      PRIMARY KEY (device_id, ts)
    ) WITHOUT ROWID;
    CREATE TABLE IF NOT EXISTS energy15 (
      device_id TEXT NOT NULL, ts INTEGER NOT NULL, %(energy_cols)s, est INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY (device_id, ts)
    ) WITHOUT ROWID;
    CREATE TABLE IF NOT EXISTS energy_state (
      device_id TEXT NOT NULL, k TEXT NOT NULL, base REAL, total REAL NOT NULL DEFAULT 0,
      PRIMARY KEY (device_id, k)
    ) WITHOUT ROWID;
    CREATE TABLE IF NOT EXISTS events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      device_id TEXT NOT NULL,
      ts INTEGER NOT NULL,
      end_ts INTEGER,
      kind TEXT NOT NULL,
      phase TEXT,
      level TEXT NOT NULL,
      msg TEXT NOT NULL,
      data TEXT
    );
    CREATE INDEX IF NOT EXISTS events_dev_ts ON events (device_id, ts);
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """ % {"reading_cols": reading_cols, "stat_cols": stat_cols, "energy_cols": energy_cols}


_SQL_INSERT_READING = "INSERT OR REPLACE INTO readings (device_id, ts, %s, extra) VALUES (?, ?, %s, ?)" % (
    ", ".join(ALL_FIELDS), ", ".join("?" for _ in ALL_FIELDS))

_SQL_UPSERT_ENERGY = (
    "INSERT INTO energy15 (device_id, ts, %s, est) VALUES (?, ?, %s, ?) "
    "ON CONFLICT(device_id, ts) DO UPDATE SET %s, est = MAX(est, excluded.est)"
) % (
    ", ".join(ENERGY_COLS),
    ", ".join("?" for _ in ENERGY_COLS),
    ", ".join("{0} = {0} + excluded.{0}".format(c) for c in ENERGY_COLS),
)

_SQL_STAT_SELECT = "SELECT COUNT(*), %s FROM readings WHERE device_id = ? AND ts >= ? AND ts < ?" % (
    ", ".join("AVG({0}), MIN({0}), MAX({0})".format(f) for f in INSTANT_FIELDS))

_SQL_STAT_INSERT = "INSERT OR REPLACE INTO stat15 (device_id, ts, n, %s) VALUES (?, ?, ?, %s)" % (
    ", ".join("{0}_avg, {0}_min, {0}_max".format(f) for f in INSTANT_FIELDS),
    ", ".join("?, ?, ?" for _ in INSTANT_FIELDS))

_SQL_ENERGY_SUM = "SELECT %s, MAX(est), COUNT(*) FROM energy15 WHERE device_id = ? AND ts >= ? AND ts < ?" % (
    ", ".join("SUM(%s)" % c for c in ENERGY_COLS))

DEFAULT_SETTINGS = {
    "tariff": 0.95,        # R$/kWh consumido da rede
    "credit": 0.0,         # R$/kWh injetado (crédito), se quiser abater do custo
    "mode": "auto",        # auto | consumo | bidirecional | geracao
    "v_nominal": "auto",   # auto | 127 | 220 | outro valor em volts
}


class DevState:
    """Estado em memória de um medidor (espelha o que está no banco)."""

    def __init__(self, device_id: str):
        self.id = device_id
        self.name: Optional[str] = None
        self.model: Optional[str] = None
        self.phases = 3
        self.labels: Dict[str, str] = {}
        self.v_nom: Optional[float] = None
        self.first_seen: Optional[int] = None
        self.last_ts: Optional[int] = None
        self.last: Optional[Dict[str, Optional[float]]] = None
        self.last_source: Optional[str] = None
        self.n = 0
        self.base: Dict[str, Optional[float]] = {}
        self.total: Dict[str, float] = {k: 0.0 for k in COUNTER_FIELDS}
        self.interval: Optional[float] = None
        self.online = False
        self.day: Optional[int] = None
        self.today: Dict[str, float] = {c: 0.0 for c in ENERGY_COLS}
        self.vstate: Dict[str, Dict[str, Any]] = {}
        self.stat_bucket: Optional[int] = None
        self.flag_ts: Optional[int] = None    # última ocorrência de contador reiniciado/corrigido registrada
        self.regress = 0                      # leituras seguidas com o relógio do servidor atrasado
        self.mono: Optional[float] = None     # relógio monotônico da última gravação (mede tempo real decorrido)

    def display_name(self) -> str:
        return self.name or ("Medidor de energia" if self.id == "1" else "Medidor %s" % self.id)

    def info(self, now: Optional[float] = None) -> Dict[str, Any]:
        now = now or time.time()
        return {
            "id": self.id,
            "name": self.display_name(),
            "custom_name": self.name,
            "model": self.model,
            "phases": self.phases,
            "labels": {p: self.labels.get(p) or "Fase %s" % p.upper() for p in PHASES[: self.phases]},
            "v_nom": self.v_nom,
            "first_seen": self.first_seen,
            "last_seen": self.last_ts,
            "age": (now - self.last_ts) if self.last_ts else None,
            "online": self.online,
            "interval": round(self.interval, 1) if self.interval else None,
            "n_readings": self.n,
            "source": self.last_source,
        }


class Store:
    def __init__(self, path: str, clock: Clock, max_power_kw: float = 80.0, offline_after_s: int = 180,
                 tariff_default: float = 0.95):
        self.path = path
        self.clock = clock
        self.max_power_kw = max_power_kw
        self.offline_after_s = offline_after_s
        self._tariff_default = tariff_default
        self._tl = threading.local()
        self._wlock = threading.RLock()
        self._state: Dict[str, DevState] = {}
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._settings_cache: Optional[Dict[str, Any]] = None
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self._memory_conn: Optional[sqlite3.Connection] = None
        self._conns: List[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        self._init_db()
        self._load_states()

    # ------------------------------------------------------------------ conexões
    def conn(self) -> sqlite3.Connection:
        if self.path == ":memory:":
            if self._memory_conn is None:
                self._memory_conn = self._open(check_same_thread=False)
            return self._memory_conn
        c = getattr(self._tl, "conn", None)
        if c is None:
            # cada thread usa só a própria conexão; check_same_thread=False existe para close() poder fechar todas
            c = self._open(check_same_thread=False)
            self._tl.conn = c
            with self._conns_lock:
                self._conns.append(c)
        return c

    def close(self) -> None:
        """Fecha todas as conexões (ao encerrar). O banco não deve estar em uso por outras threads."""
        with self._wlock, self._conns_lock:
            conns, self._conns = self._conns, []
            for c in conns:
                try:
                    c.close()
                except sqlite3.Error:
                    pass
            self._tl = threading.local()
            if self._memory_conn is not None:
                try:
                    self._memory_conn.close()
                except sqlite3.Error:
                    pass
                self._memory_conn = None

    def _open(self, check_same_thread: bool = True) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=check_same_thread)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout = 30000")
        c.execute("PRAGMA journal_mode = WAL")
        c.execute("PRAGMA synchronous = NORMAL")
        c.execute("PRAGMA temp_store = MEMORY")
        return c

    def close_thread(self) -> None:
        """Fecha a conexão da thread atual (chamado ao fim de cada conexão HTTP)."""
        c = getattr(self._tl, "conn", None)
        if c is not None:
            try:
                c.close()
            except sqlite3.Error:
                pass
            self._tl.conn = None
            with self._conns_lock:
                if c in self._conns:
                    self._conns.remove(c)

    def _init_db(self) -> None:
        c = self.conn()
        with self._wlock:
            version = c.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                # só tem efeito em banco recém-criado; permite devolver espaço ao disco aos poucos
                c.execute("PRAGMA auto_vacuum = INCREMENTAL")
            c.executescript(_schema())
            if version < SCHEMA_VERSION:
                c.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)

    # ------------------------------------------------------------------ estado
    def _load_states(self) -> None:
        c = self.conn()
        now = time.time()
        for row in c.execute("SELECT * FROM devices").fetchall():
            st = DevState(row["id"])
            st.name = row["name"]
            st.model = row["model"]
            st.phases = row["phases"] or 3
            try:
                st.labels = json.loads(row["labels"]) if row["labels"] else {}
            except ValueError:
                st.labels = {}
            st.v_nom = row["v_nom"]
            st.first_seen = row["first_seen"]
            st.n = row["n_readings"] or 0
            st.last_source = row["last_source"]
            for er in c.execute("SELECT k, base, total FROM energy_state WHERE device_id = ?", (st.id,)):
                if er["k"] in st.total:
                    st.base[er["k"]] = er["base"]
                    st.total[er["k"]] = er["total"] or 0.0
            last = c.execute("SELECT * FROM readings WHERE device_id = ? ORDER BY ts DESC LIMIT 1", (st.id,)).fetchone()
            if last is not None:
                st.last_ts = last["ts"]
                st.last = {f: last[f] for f in ALL_FIELDS}
            elif row["last_seen"]:
                st.last_ts = row["last_seen"]
            prev = c.execute("SELECT ts FROM readings WHERE device_id = ? ORDER BY ts DESC LIMIT 6", (st.id,)).fetchall()
            gaps = [prev[i]["ts"] - prev[i + 1]["ts"] for i in range(len(prev) - 1)]
            gaps = [g for g in gaps if 0 < g < 900]
            if gaps:
                st.interval = sum(gaps) / len(gaps)
            if st.last_ts:
                st.day = self.clock.day_start(st.last_ts)
                st.today = self._energy_sum(c, st.id, st.day, self.clock.add_days(st.last_ts, 1))
                st.online = (now - st.last_ts) <= self._offline_threshold(st)
                st.stat_bucket = st.last_ts - st.last_ts % BUCKET
            # eventos de tensão ainda abertos continuam abertos após reiniciar
            for ev in c.execute(
                    "SELECT id, phase, kind, level, data FROM events WHERE device_id = ? AND end_ts IS NULL "
                    "AND kind IN ('tensao', 'falta_fase')", (st.id,)):
                try:
                    data = json.loads(ev["data"]) if ev["data"] else {}
                except ValueError:
                    data = {}
                st.vstate[ev["phase"]] = {
                    "cat": data.get("cat", "precaria"), "pending": None, "count": 0, "p_ts": None,
                    "p_min": None, "p_max": None, "event_id": ev["id"],
                    "min": data.get("min"), "max": data.get("max"), "worst": data.get("cat", "precaria"),
                }
            self._state[st.id] = st

    def _offline_threshold(self, st: DevState) -> float:
        return max(float(self.offline_after_s), 4.0 * (st.interval or 30.0))

    def add_listener(self, fn: Callable[[Dict[str, Any]], None]) -> None:
        self._listeners.append(fn)

    def _notify(self, event: Dict[str, Any]) -> None:
        for fn in list(self._listeners):
            try:
                fn(event)
            except Exception:  # um ouvinte com problema não pode derrubar a ingestão
                log.exception("Erro em ouvinte de eventos")

    # ------------------------------------------------------------------ configurações
    def settings(self) -> Dict[str, Any]:
        if self._settings_cache is None:
            out = dict(DEFAULT_SETTINGS)
            out["tariff"] = self._tariff_default
            for row in self.conn().execute("SELECT key, value FROM settings"):
                try:
                    out[row["key"]] = json.loads(row["value"])
                except ValueError:
                    continue
            self._settings_cache = out
        return dict(self._settings_cache)

    def update_settings(self, changes: Dict[str, Any]) -> Dict[str, Any]:
        clean: Dict[str, Any] = {}
        if "tariff" in changes:
            v = float(changes["tariff"])
            if not (0 <= v <= 100):
                raise ValueError("tarifa fora do intervalo esperado")
            clean["tariff"] = round(v, 5)
        if "credit" in changes:
            v = float(changes["credit"])
            if not (0 <= v <= 100):
                raise ValueError("crédito fora do intervalo esperado")
            clean["credit"] = round(v, 5)
        if "mode" in changes:
            if changes["mode"] not in ("auto", "consumo", "bidirecional", "geracao"):
                raise ValueError("modo inválido")
            clean["mode"] = changes["mode"]
        if "v_nominal" in changes:
            v = changes["v_nominal"]
            if v in ("auto", None, ""):
                clean["v_nominal"] = "auto"
            else:
                v = float(v)
                if not (50 <= v <= 500):
                    raise ValueError("tensão nominal fora do intervalo esperado")
                clean["v_nominal"] = v
        with self._wlock:
            c = self.conn()
            for k, v in clean.items():
                c.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                          "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (k, json.dumps(v)))
            self._settings_cache = None
            if "v_nominal" in clean:
                for st in self._state.values():
                    self._refresh_nominal(c, st, force=True)
        return self.settings()

    def _refresh_nominal(self, c: sqlite3.Connection, st: DevState, force: bool = False) -> None:
        setting = self.settings().get("v_nominal", "auto")
        new = st.v_nom
        if setting != "auto":
            new = float(setting)
        elif force or st.v_nom is None:
            new = None
            if st.last:
                for ph in PHASES:
                    new = voltage.detect_nominal(st.last.get(PHASE_FIELDS[ph]["u"]))
                    if new:
                        break
        if new != st.v_nom:
            st.v_nom = new
            c.execute("UPDATE devices SET v_nom = ? WHERE id = ?", (new, st.id))

    # ------------------------------------------------------------------ ingestão
    def ingest(self, reading: Dict[str, Any], ts: Optional[int] = None, source: str = "http") -> Dict[str, Any]:
        """Grava uma leitura normalizada (saída de parser.parse_message)."""
        dev = reading["id"]
        with self._wlock:
            c = self.conn()
            st = self._state.get(dev)
            is_new = st is None
            if is_new:
                st = DevState(dev)
            now = int(time.time()) if ts is None else int(ts)
            mono = time.monotonic()
            cap_dt = None
            if st.last_ts is not None and now <= st.last_ts:
                if ts is not None:
                    # leitura com data/hora explícita repetida ou fora de ordem
                    return {"device": dev, "ts": now, "skipped": True, "reason": "ordem", "new_device": False}
                if st.last_ts - now <= 5:
                    now = st.last_ts + 1     # duas leituras no mesmo segundo
                else:
                    # O relógio do servidor está atrás da última leitura gravada. O caso comum é um computador
                    # sem bateria de relógio (Raspberry Pi) que acabou de ligar e ainda não acertou a hora:
                    # espera algumas leituras. Se persistir, o relógio antigo é que estava errado; segue daqui.
                    st.regress += 1
                    if st.regress <= CLOCK_PATIENCE:
                        if st.regress == 1:
                            log.warning("O relógio do servidor está %d s atrás da última leitura gravada do medidor %s. "
                                        "Aguardando o acerto da hora...", st.last_ts - now, dev)
                        return {"device": dev, "ts": now, "skipped": True, "reason": "relogio", "new_device": False}
                    log.warning("O relógio do servidor continua %d s atrás da última leitura do medidor %s; "
                                "continuando a partir do horário atual.", st.last_ts - now, dev)
                    cap_dt = (mono - st.mono) if st.mono is not None else 86400.0
                    st.last_ts = None
            c.execute("BEGIN IMMEDIATE")
            try:
                flags = self._apply(c, st, reading, now, source, live=True, cap_dt=cap_dt)
                self._flush_state(c, st, source)
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                # o estado em memória pode ter ficado adiantado: recarrega do banco
                self._state.pop(dev, None)
                self._reload_state(dev)
                raise
            self._state[dev] = st
            st.regress = 0
            st.mono = mono
            was_online = st.online
            st.online = True
            snapshot = self._snapshot(st)
        if is_new:
            log.info("Novo medidor detectado: id=%s (%s fase(s)) via %s", dev, st.phases, source)
        self._notify({"type": "reading", "device": dev, "ts": now, "new_device": is_new,
                      "came_online": not was_online, "snapshot": snapshot})
        return {"device": dev, "ts": now, "skipped": False, "new_device": is_new, "flags": flags}

    def ingest_many(self, items: Iterable[Tuple[int, Dict[str, Any]]], source: str = "import",
                    chunk: int = 2000, events: bool = True) -> int:
        """Grava várias leituras com data/hora explícita (importação, simulação).

        As leituras de cada medidor devem vir em ordem crescente de tempo; as que estiverem
        fora de ordem são ignoradas. Não dispara publicação MQTT. Use events=False quando as
        leituras forem esparsas (ex.: histórico de 30 em 30 min), para não gerar eventos falsos.
        """
        count = 0
        touched: Dict[str, DevState] = {}
        with self._wlock:
            c = self.conn()
            c.execute("BEGIN IMMEDIATE")
            try:
                for ts, reading in items:
                    dev = reading["id"]
                    st = touched.get(dev) or self._state.get(dev)
                    if st is None:
                        st = DevState(dev)
                    ts = int(ts)
                    if st.last_ts is not None and ts <= st.last_ts:
                        continue
                    self._apply(c, st, reading, ts, source, live=False, events=events)
                    touched[dev] = st
                    self._state[dev] = st
                    count += 1
                    if count % chunk == 0:
                        for s in touched.values():
                            self._flush_state(c, s, source)
                        c.execute("COMMIT")
                        c.execute("BEGIN IMMEDIATE")
                for s in touched.values():
                    if s.stat_bucket is not None:
                        self._recompute_stat(c, s.id, s.stat_bucket)
                    self._flush_state(c, s, source)
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                for dev in touched:
                    self._state.pop(dev, None)
                    self._reload_state(dev)
                raise
            now = time.time()
            for s in touched.values():
                s.online = bool(s.last_ts) and (now - s.last_ts) <= self._offline_threshold(s)
        return count

    def _reload_state(self, dev: str) -> None:
        saved = self._state
        self._state = {}
        try:
            self._load_states()
            if dev in self._state:
                saved[dev] = self._state[dev]
        finally:
            self._state = saved

    def _apply(self, c: sqlite3.Connection, st: DevState, r: Dict[str, Any], ts: int, source: str,
               live: bool, events: bool = True, cap_dt: Optional[float] = None) -> List[Dict[str, Any]]:
        dt = (ts - st.last_ts) if st.last_ts is not None else None
        # intervalo usado só para o teto físico dos contadores, quando o relógio não é confiável
        window = dt if dt is not None else cap_dt
        flags: List[Dict[str, Any]] = []

        phases = int(r.get("_phases") or 3)
        if st.first_seen is None:
            st.first_seen = ts
            st.phases = phases
            st.model = "SM-3W Lite" if phases == 3 else "SM-W Lite"
        elif phases > st.phases:
            st.phases = phases
            st.model = "SM-3W Lite"

        # ---- energia: diferença dos contadores, com proteção contra reinício/salto
        deltas = {col: 0.0 for col in ENERGY_COLS}
        have_counter = False
        for k in COUNTER_FIELDS:
            cur = r.get(k)
            prev_base = st.base.get(k)
            d, base, status = counter_delta(prev_base, cur, window, self.max_power_kw)
            if cur is not None:
                have_counter = True
            if status in ("reinicio", "salto") or (
                    status == "recuo" and prev_base is not None and prev_base - cur > GLITCH_KWH):
                flags.append({"k": k, "status": status, "de": prev_base, "para": cur})
            st.base[k] = base
            deltas[COUNTER_COL[k]] = d
        est = 1 if (dt is not None and dt > GAP_EST_S) else 0
        if not have_counter:
            # firmware sem contadores: integra a potência entre leituras próximas
            if st.last is not None and dt is not None and 0 < dt <= MAX_INTEGRATE_S:
                for pk, col_c, col_g in PHASE_ENERGY:
                    cons, gen = integrate_power(st.last.get(pk), r.get(pk), dt)
                    deltas[col_c], deltas[col_g] = cons, gen
                est = 1
        else:
            if r.get("ept_c") is None:
                deltas["c_t"] = deltas["c_a"] + deltas["c_b"] + deltas["c_c"]
            if r.get("ept_g") is None:
                deltas["g_t"] = deltas["g_a"] + deltas["g_b"] + deltas["g_c"]

        # Grava também os intervalos com energia zero: "zero" é diferente de "sem dados".
        for b, frac in split_buckets(st.last_ts, ts):
            c.execute(_SQL_UPSERT_ENERGY, (st.id, b, *[deltas[col] * frac for col in ENERGY_COLS], est))
        for k in COUNTER_FIELDS:
            st.total[k] += deltas[COUNTER_COL[k]]

        # ---- leitura bruta
        extra = json.dumps(r["_extra"], ensure_ascii=False) if r.get("_extra") else None
        c.execute(_SQL_INSERT_READING, (st.id, ts, *[r.get(f) for f in ALL_FIELDS], extra))

        # ---- estatísticas de 15 min
        bucket = ts - ts % BUCKET
        if live:
            self._recompute_stat(c, st.id, bucket)
        elif st.stat_bucket is not None and st.stat_bucket != bucket:
            self._recompute_stat(c, st.id, st.stat_bucket)
        st.stat_bucket = bucket

        # ---- eventos
        if events and dt is not None and dt > self._offline_threshold(st):
            self._add_event(c, st.id, st.last_ts, ts, "offline", None, "aviso",
                            "Sem leituras do medidor", {"segundos": dt})
        if events and flags and (st.flag_ts is None or abs(ts - st.flag_ts) >= FLAG_EVENT_EVERY_S):
            # Um contador que salta a cada leitura (unidade errada, defeito) não pode encher a lista de ocorrências.
            st.flag_ts = ts
            self._add_event(c, st.id, ts, ts, "contador", None, "info",
                            "Contador de energia do medidor foi reiniciado ou corrigido", {"contadores": flags})
        if dt is not None and 0 < dt < 900:
            st.interval = dt if st.interval is None else 0.8 * st.interval + 0.2 * dt

        # ---- estado
        st.last_ts = ts
        st.last = {f: r.get(f) for f in ALL_FIELDS}
        st.last_source = source
        st.n += 1
        if st.v_nom is None:
            self._refresh_nominal_inline(st)
        if events:
            self._track_voltage(c, st, r, ts)

        day = self.clock.day_start(ts)
        if st.day != day:
            st.day = day
            st.today = self._energy_sum(c, st.id, day, self.clock.add_days(ts, 1))
        else:
            for col in ENERGY_COLS:
                st.today[col] += deltas[col]
        return flags

    def _refresh_nominal_inline(self, st: DevState) -> None:
        setting = self.settings().get("v_nominal", "auto")
        if setting != "auto":
            st.v_nom = float(setting)
            return
        if st.last:
            for ph in PHASES:
                nom = voltage.detect_nominal(st.last.get(PHASE_FIELDS[ph]["u"]))
                if nom:
                    st.v_nom = nom
                    return

    def _flush_state(self, c: sqlite3.Connection, st: DevState, source: str) -> None:
        c.execute(
            "INSERT INTO devices (id, name, model, phases, labels, v_nom, first_seen, last_seen, n_readings, last_source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET model = excluded.model, phases = excluded.phases, v_nom = excluded.v_nom, "
            "first_seen = COALESCE(devices.first_seen, excluded.first_seen), last_seen = excluded.last_seen, "
            "n_readings = excluded.n_readings, last_source = excluded.last_source",
            (st.id, st.name, st.model, st.phases, json.dumps(st.labels, ensure_ascii=False) if st.labels else None,
             st.v_nom, st.first_seen, st.last_ts, st.n, source))
        c.executemany(
            "INSERT INTO energy_state (device_id, k, base, total) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(device_id, k) DO UPDATE SET base = excluded.base, total = excluded.total",
            [(st.id, k, st.base.get(k), st.total.get(k, 0.0)) for k in COUNTER_FIELDS])

    def _recompute_stat(self, c: sqlite3.Connection, dev: str, bucket: int) -> None:
        row = c.execute(_SQL_STAT_SELECT, (dev, bucket, bucket + BUCKET)).fetchone()
        if row is None or not row[0]:
            return
        c.execute(_SQL_STAT_INSERT, (dev, bucket, row[0], *row[1:]))

    def _energy_sum(self, c: sqlite3.Connection, dev: str, t0: int, t1: int) -> Dict[str, float]:
        row = c.execute(_SQL_ENERGY_SUM, (dev, int(t0), int(t1))).fetchone()
        return {col: (row[i] or 0.0) for i, col in enumerate(ENERGY_COLS)}

    def _snapshot(self, st: DevState) -> Dict[str, Any]:
        return {
            "device": st.info(),
            "ts": st.last_ts,
            "reading": dict(st.last or {}),
            "totals": {COUNTER_COL[k]: round(st.total[k], 4) for k in COUNTER_FIELDS},
            "today": {c: round(v, 4) for c, v in st.today.items()},
            # situação da tensão de cada fase (adequada, precaria, critica, ausente); None antes da primeira leitura
            "voltage": {p: (st.vstate.get(p) or {}).get("cat") for p in PHASES[: st.phases]},
        }

    # ------------------------------------------------------------------ eventos
    def _add_event(self, c: sqlite3.Connection, dev: str, ts: int, end_ts: Optional[int], kind: str,
                   phase: Optional[str], level: str, msg: str, data: Optional[Dict[str, Any]] = None) -> int:
        cur = c.execute(
            "INSERT INTO events (device_id, ts, end_ts, kind, phase, level, msg, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (dev, int(ts), int(end_ts) if end_ts is not None else None, kind, phase, level, msg,
             json.dumps(data, ensure_ascii=False) if data else None))
        return int(cur.lastrowid)

    def _track_voltage(self, c: sqlite3.Connection, st: DevState, r: Dict[str, Any], ts: int) -> None:
        """Abre e fecha eventos quando a tensão de uma fase sai da faixa adequada."""
        if not st.v_nom:
            return
        rank = {"precaria": 1, "critica": 2, "ausente": 3}

        def describe(cat: str, ph: str) -> Tuple[str, str, str]:
            if cat == "ausente":
                return "falta_fase", "critico", "Fase %s sem tensão" % ph.upper()
            return "tensao", ("aviso" if cat == "precaria" else "critico"), (
                "Tensão da fase %s fora da faixa adequada" % ph.upper())

        for ph in PHASES[: st.phases]:
            u = r.get(PHASE_FIELDS[ph]["u"])
            cat = voltage.classify(u, st.v_nom)
            if cat is None:
                continue
            vs = st.vstate.get(ph)
            if vs is None:
                # uma fase só passa a ser acompanhada depois de aparecer com tensão
                if cat == "ausente":
                    continue
                vs = {"cat": "adequada", "pending": None, "count": 0, "p_ts": None, "p_min": None,
                      "p_max": None, "event_id": None, "min": None, "max": None, "worst": None}
                st.vstate[ph] = vs
            u = round(u, 2)
            if vs["event_id"] is not None and cat != "adequada":
                vs["min"] = u if vs["min"] is None else min(vs["min"], u)
                vs["max"] = u if vs["max"] is None else max(vs["max"], u)
            if cat == vs["cat"]:
                vs["pending"], vs["count"] = None, 0
                continue
            if vs["pending"] == cat:
                vs["count"] += 1
                vs["p_min"], vs["p_max"] = min(vs["p_min"], u), max(vs["p_max"], u)
            else:
                vs["pending"], vs["count"], vs["p_ts"] = cat, 1, ts
                vs["p_min"] = vs["p_max"] = u
            if vs["count"] < VOLT_DEBOUNCE:
                continue
            # mudança de faixa confirmada (vale desde a primeira leitura na nova faixa)
            since = vs["p_ts"] or ts
            vs["cat"], vs["pending"], vs["count"] = cat, None, 0
            if cat == "adequada":
                if vs["event_id"] is not None:
                    c.execute("UPDATE events SET end_ts = ?, data = ? WHERE id = ?",
                              (since, json.dumps({"cat": vs["worst"], "min": vs["min"], "max": vs["max"],
                                                  "v_nom": st.v_nom}), vs["event_id"]))
                vs.update({"event_id": None, "min": None, "max": None, "worst": None})
                continue
            if vs["event_id"] is None:
                vs["min"], vs["max"], vs["worst"] = vs["p_min"], vs["p_max"], cat
                kind, level, msg = describe(cat, ph)
                vs["event_id"] = self._add_event(
                    c, st.id, since, None, kind, ph, level, msg,
                    {"cat": cat, "min": vs["min"], "max": vs["max"], "v_nom": st.v_nom})
                continue
            if rank.get(cat, 0) > rank.get(vs["worst"] or "", 0):
                vs["worst"] = cat
                kind, level, msg = describe(cat, ph)
                c.execute("UPDATE events SET kind = ?, level = ?, msg = ? WHERE id = ?",
                          (kind, level, msg, vs["event_id"]))
            c.execute("UPDATE events SET data = ? WHERE id = ?",
                      (json.dumps({"cat": vs["worst"], "min": vs["min"], "max": vs["max"],
                                   "v_nom": st.v_nom}), vs["event_id"]))

    # ------------------------------------------------------------------ manutenção
    def check_online(self, now: Optional[float] = None) -> List[str]:
        """Marca como offline os medidores que pararam de enviar. Devolve os ids afetados."""
        now = now or time.time()
        changed: List[str] = []
        with self._wlock:
            for st in self._state.values():
                if st.online and st.last_ts and (now - st.last_ts) > self._offline_threshold(st):
                    st.online = False
                    changed.append(st.id)
        for dev in changed:
            log.warning("Medidor %s sem enviar dados há mais de %.0f s", dev, self._offline_threshold(self._state[dev]))
            self._notify({"type": "offline", "device": dev})
        return changed

    def purge(self, retention_days: int, now: Optional[float] = None) -> int:
        """Apaga leituras brutas mais antigas que a retenção (agregados permanecem)."""
        if retention_days <= 0:
            return 0
        cutoff = int((now or time.time()) - retention_days * 86400)
        with self._wlock:
            c = self.conn()
            cur = c.execute("DELETE FROM readings WHERE ts < ?", (cutoff,))
            n = cur.rowcount or 0
            if n:
                c.execute("PRAGMA incremental_vacuum(2000)")
        return n

    def db_size(self) -> int:
        try:
            size = os.path.getsize(self.path)
            wal = self.path + "-wal"
            if os.path.exists(wal):
                size += os.path.getsize(wal)
            return size
        except OSError:
            return 0

    def backup_to(self, dest: str) -> None:
        """Cópia consistente do banco, mesmo com o sistema em uso."""
        out = sqlite3.connect(dest)
        try:
            self.conn().backup(out)
        finally:
            out.close()

    # ------------------------------------------------------------------ medidores
    def devices(self) -> List[Dict[str, Any]]:
        now = time.time()
        items = [st.info(now) for st in list(self._state.values())]
        items.sort(key=lambda d: (-(d["last_seen"] or 0), d["id"]))
        return items

    def device_ids(self) -> List[str]:
        return [d["id"] for d in self.devices()]

    def default_device(self) -> Optional[str]:
        ids = self.device_ids()
        return ids[0] if ids else None

    def state(self, dev: str) -> Optional[DevState]:
        return self._state.get(dev)

    def snapshot(self, dev: str) -> Optional[Dict[str, Any]]:
        st = self._state.get(dev)
        if st is None:
            return None
        with self._wlock:
            return self._snapshot(st)

    def update_device(self, dev: str, name: Optional[str] = None, labels: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        with self._wlock:
            st = self._state.get(dev)
            if st is None:
                raise KeyError(dev)
            if name is not None:
                st.name = name.strip()[:60] or None
            if labels is not None:
                st.labels = {p: str(v).strip()[:30] for p, v in labels.items() if p in PHASES and str(v).strip()}
            self.conn().execute("UPDATE devices SET name = ?, labels = ? WHERE id = ?",
                                (st.name, json.dumps(st.labels, ensure_ascii=False) if st.labels else None, dev))
            info = st.info()
        self._notify({"type": "device_updated", "device": dev})
        return info

    def delete_device(self, dev: str) -> bool:
        with self._wlock:
            if dev not in self._state:
                return False
            c = self.conn()
            c.execute("BEGIN IMMEDIATE")
            try:
                for table in ("readings", "stat15", "energy15", "energy_state", "events"):
                    c.execute("DELETE FROM %s WHERE device_id = ?" % table, (dev,))
                c.execute("DELETE FROM devices WHERE id = ?", (dev,))
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
            st = self._state.pop(dev)
            phases = st.phases
        self._notify({"type": "device_deleted", "device": dev, "phases": phases})
        return True

    # ------------------------------------------------------------------ consultas
    def series(self, dev: str, fields: Sequence[str], t0: int, t1: int, max_points: int = 800,
               res: Optional[int] = None) -> Dict[str, Any]:
        """Série temporal de uma ou mais grandezas, já reduzida para caber em max_points.

        Resoluções abaixo de 15 min saem das leituras brutas; as demais, dos agregados.
        """
        fields = [f for f in fields if f in INSTANT_FIELDS][:16]
        t0, t1 = int(t0), int(t1)
        span = max(1, t1 - t0)
        st = self._state.get(dev)
        interval = (st.interval if st and st.interval else 30.0)
        max_points = max(50, min(int(max_points), 5000))
        if res is None:
            if span / interval <= max_points:
                res = 0
            else:
                res = next((r for r in LADDER if span / r <= max_points), LADDER[-1])
        res = int(res)
        out: Dict[str, Any] = {"device": dev, "from": t0, "to": t1, "res": res, "t": [],
                               "series": {f: {} for f in fields}}
        if not fields:
            return out
        c = self.conn()
        if res < BUCKET:
            # Se parte do período pedido já saiu da retenção das leituras brutas, usa os agregados.
            oldest = c.execute("SELECT MIN(ts) FROM readings WHERE device_id = ?", (dev,)).fetchone()[0]
            if oldest is None or t1 <= oldest:
                res = BUCKET
            elif t0 < oldest - BUCKET and c.execute(
                    "SELECT 1 FROM stat15 WHERE device_id = ? AND ts >= ? AND ts < ? LIMIT 1",
                    (dev, t0 - t0 % BUCKET, oldest - BUCKET)).fetchone():
                res = BUCKET
            out["res"] = res
        if res == 0:
            out["source"] = "bruto"
            rows = c.execute("SELECT ts, %s FROM readings WHERE device_id = ? AND ts >= ? AND ts < ? ORDER BY ts"
                             % ", ".join(fields), (dev, t0, t1)).fetchall()
            out["t"] = [r[0] for r in rows]
            for i, f in enumerate(fields):
                out["series"][f] = {"avg": [round_field(f, r[i + 1]) for r in rows]}
            return out
        if res < BUCKET:
            out["source"] = "bruto"
            cols = ", ".join("AVG({0}), MIN({0}), MAX({0})".format(f) for f in fields)
            rows = c.execute(
                "SELECT (ts / ?) * ? AS b, %s FROM readings WHERE device_id = ? AND ts >= ? AND ts < ? "
                "GROUP BY b ORDER BY b" % cols, (res, res, dev, t0, t1)).fetchall()
        else:
            out["source"] = "agregado"
            off = self.clock.offset(t1) if res >= 3600 else 0
            cols = ", ".join(
                "SUM({0}_avg * n) / SUM(CASE WHEN {0}_avg IS NOT NULL THEN n END), MIN({0}_min), MAX({0}_max)".format(f)
                for f in fields)
            rows = c.execute(
                "SELECT ((ts + ?) / ?) * ? - ? AS b, %s FROM stat15 WHERE device_id = ? AND ts >= ? AND ts < ? "
                "GROUP BY b ORDER BY b" % cols, (off, res, res, off, dev, t0 - t0 % BUCKET, t1)).fetchall()
        out["t"] = [r[0] for r in rows]
        for i, f in enumerate(fields):
            j = 1 + 3 * i
            out["series"][f] = {
                "avg": [round_field(f, r[j]) for r in rows],
                "min": [round_field(f, r[j + 1]) for r in rows],
                "max": [round_field(f, r[j + 2]) for r in rows],
            }
        return out

    def energy(self, dev: str, t0: int, t1: int, group: str) -> Dict[str, Any]:
        """Energia consumida/gerada por 15 min, hora, dia ou mês (calendário local)."""
        bounds = self.clock.boundaries(int(t0), int(t1), group)
        c = self.conn()
        rows = c.execute(
            "SELECT ts, %s, est FROM energy15 WHERE device_id = ? AND ts >= ? AND ts < ? ORDER BY ts"
            % ", ".join(ENERGY_COLS), (dev, bounds[0], bounds[-1])).fetchall()
        n_groups = len(bounds) - 1
        acc = [[0.0] * len(ENERGY_COLS) for _ in range(n_groups)]
        cnt = [0] * n_groups
        est = [0] * n_groups
        for r in rows:
            i = bisect.bisect_right(bounds, r[0]) - 1
            if i < 0 or i >= n_groups:
                continue
            a = acc[i]
            for j in range(len(ENERGY_COLS)):
                a[j] += r[j + 1] or 0.0
            cnt[i] += 1
            est[i] = max(est[i], r[len(ENERGY_COLS) + 1] or 0)
        out_rows = []
        totals = [0.0] * len(ENERGY_COLS)
        for i in range(n_groups):
            item: Dict[str, Any] = {"start": bounds[i], "end": bounds[i + 1],
                                    "label": self.clock.label(bounds[i], group), "n": cnt[i], "est": bool(est[i])}
            for j, col in enumerate(ENERGY_COLS):
                item[col] = round(acc[i][j], 4) if cnt[i] else None
                totals[j] += acc[i][j]
            out_rows.append(item)
        return {"device": dev, "group": group, "from": bounds[0], "to": bounds[-1], "rows": out_rows,
                "totals": {col: round(totals[j], 4) for j, col in enumerate(ENERGY_COLS)}}

    def energy_sum(self, dev: str, t0: int, t1: int) -> Dict[str, Any]:
        row = self.conn().execute(_SQL_ENERGY_SUM, (dev, int(t0), int(t1))).fetchone()
        out: Dict[str, Any] = {col: round(row[i] or 0.0, 4) for i, col in enumerate(ENERGY_COLS)}
        out["est"] = bool(row[len(ENERGY_COLS)])
        out["n"] = row[len(ENERGY_COLS) + 1] or 0
        return out

    def demand_max(self, dev: str, t0: int, t1: int, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Maior demanda média de 15 min (kW) no período, ignorando o intervalo ainda em curso."""
        now = now or time.time()
        closed = int(now) - int(now) % BUCKET
        row = self.conn().execute(
            "SELECT ts, c_t FROM energy15 WHERE device_id = ? AND ts >= ? AND ts < ? AND ts < ? AND est = 0 "
            "ORDER BY c_t DESC LIMIT 1", (dev, int(t0), int(t1), closed)).fetchone()
        if row is None or row[1] is None:
            return None
        return {"kw": round(row[1] * 3600.0 / BUCKET, 3), "ts": row[0]}

    def stat_extreme(self, dev: str, field: str, t0: int, t1: int, kind: str = "max") -> Optional[Dict[str, Any]]:
        if field not in INSTANT_FIELDS or kind not in ("max", "min"):
            return None
        col = "%s_%s" % (field, kind)
        order = "DESC" if kind == "max" else "ASC"
        row = self.conn().execute(
            "SELECT ts, %s FROM stat15 WHERE device_id = ? AND ts >= ? AND ts < ? AND %s IS NOT NULL "
            "ORDER BY %s %s LIMIT 1" % (col, col, col, order), (dev, int(t0), int(t1))).fetchone()
        if row is None:
            return None
        return {"value": round_field(field, row[1]), "ts": row[0]}

    def stat_rows(self, dev: str, fields: Sequence[str], t0: int, t1: int) -> List[sqlite3.Row]:
        fields = [f for f in fields if f in INSTANT_FIELDS]
        cols = ", ".join("{0}_avg, {0}_min, {0}_max".format(f) for f in fields)
        return self.conn().execute(
            "SELECT ts, n, %s FROM stat15 WHERE device_id = ? AND ts >= ? AND ts < ? ORDER BY ts" % cols,
            (dev, int(t0), int(t1))).fetchall()

    def raw_grouped(self, dev: str, fields: Sequence[str], t0: int, t1: int, window: int) -> List[sqlite3.Row]:
        """Médias das leituras brutas em janelas de `window` segundos."""
        fields = [f for f in fields if f in ALL_FIELDS]
        cols = ", ".join("AVG(%s)" % f for f in fields)
        return self.conn().execute(
            "SELECT (ts / ?) * ? AS b, COUNT(*), %s FROM readings WHERE device_id = ? AND ts >= ? AND ts < ? "
            "GROUP BY b ORDER BY b" % cols, (window, window, dev, int(t0), int(t1))).fetchall()

    def raw_extreme(self, dev: str, field: str, t0: int, t1: int, kind: str = "max") -> Optional[Dict[str, Any]]:
        if field not in ALL_FIELDS or kind not in ("max", "min"):
            return None
        order = "DESC" if kind == "max" else "ASC"
        row = self.conn().execute(
            "SELECT ts, %s FROM readings WHERE device_id = ? AND ts >= ? AND ts < ? AND %s IS NOT NULL "
            "ORDER BY %s %s LIMIT 1" % (field, field, field, order), (dev, int(t0), int(t1))).fetchone()
        if row is None:
            return None
        return {"value": round_field(field, row[1]), "ts": row[0]}

    def raw_rows(self, dev: str, t0: int, t1: int, limit: Optional[int] = None, desc: bool = False):
        sql = "SELECT ts, %s FROM readings WHERE device_id = ? AND ts >= ? AND ts < ? ORDER BY ts %s" % (
            ", ".join(ALL_FIELDS), "DESC" if desc else "ASC")
        if limit:
            sql += " LIMIT %d" % int(limit)
        return self.conn().execute(sql, (dev, int(t0), int(t1)))

    def data_range(self, dev: str) -> Dict[str, Optional[int]]:
        c = self.conn()
        a = c.execute("SELECT MIN(ts), MAX(ts) FROM stat15 WHERE device_id = ?", (dev,)).fetchone()
        b = c.execute("SELECT MIN(ts), COUNT(*) FROM readings WHERE device_id = ?", (dev,)).fetchone()
        return {"first": a[0], "last": (a[1] + BUCKET) if a[1] is not None else None,
                "raw_first": b[0], "raw_count": b[1]}

    def events(self, dev: Optional[str] = None, limit: int = 100, t0: Optional[int] = None,
               t1: Optional[int] = None) -> List[Dict[str, Any]]:
        sql = "SELECT id, device_id, ts, end_ts, kind, phase, level, msg, data FROM events WHERE 1 = 1"
        args: List[Any] = []
        if dev:
            sql += " AND device_id = ?"
            args.append(dev)
        if t0 is not None:
            sql += " AND COALESCE(end_ts, ts) >= ?"
            args.append(int(t0))
        if t1 is not None:
            sql += " AND ts < ?"
            args.append(int(t1))
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        args.append(max(1, min(int(limit), 1000)))
        out = []
        for r in self.conn().execute(sql, args):
            try:
                data = json.loads(r["data"]) if r["data"] else None
            except ValueError:
                data = None
            out.append({"id": r["id"], "device": r["device_id"], "ts": r["ts"], "end_ts": r["end_ts"],
                        "kind": r["kind"], "phase": r["phase"], "level": r["level"], "msg": r["msg"],
                        "data": data, "open": r["end_ts"] is None})
        return out
