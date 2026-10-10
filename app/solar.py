"""Geração solar vinda do DTU Hoymiles (add-on "Hoymiles DTU API") e o fluxo de energia instantâneo.

O medidor e o DTU medem coisas diferentes, em ritmos diferentes:

- o medidor manda a potência a cada 30 s; na entrada da rede ela é o saldo (consumo da casa menos o solar),
  positiva quando compra e negativa quando injeta;
- o DTU só recebe dados novos dos microinversores de tempos em tempos (em geral a cada 1 a 5 min) e,
  quando os microinversores desligam no fim do dia, pode continuar mostrando o último valor.

Por isso o painel não soma os dois às cegas:

- mede a idade real do dado do solar (desde quando o DTU recebeu a leitura dos microinversores, não desde
  quando o add-on consultou o DTU) e aprende o intervalo de atualização do DTU;
- dado do solar velho demais deixa de ser usado; no lugar entra o mínimo que o medidor garante (se a casa
  está injetando X W, o solar gera pelo menos X W);
- se o solar informado for menor do que a injeção medida agora (o sol aumentou depois da última leitura do
  DTU), o solar é corrigido para cima e o consumo da casa fica em zero, em vez de negativo;
- com todos os microinversores desligados (noite), o solar é zero, mesmo que o DTU repita a última potência.
"""
from __future__ import annotations

import json
import logging
import statistics
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence

from .timeutil import Clock

log = logging.getLogger("painel.solar")

FRESH_S = 90               # até aqui o dado do solar é tratado como atual
EXPIRE_MIN_S = 20 * 60     # dado mais velho que isto (ou 3 intervalos do DTU) não é mais usado
SOURCE_DOWN_S = 90         # sem conseguir consultar a API do DTU por este tempo = fonte fora do ar
MIN_FLOW_W = 10            # abaixo disto o fluxo é desenhado como parado
NOTE_TOLERANCE_W = 60      # diferenças menores que isto são corrigidas sem aviso


def status_url(base: str) -> str:
    """Aceita http://host:8099, http://host:8099/ ou a rota completa; devolve a rota /api/status."""
    u = base.strip().rstrip("/")
    if not u:
        return ""
    if "://" not in u:
        u = "http://" + u
    for suffix in ("/api/status", "/api/summary"):
        if u.endswith(suffix):
            return u[: -len(suffix)] + "/api/status"
    return u + "/api/status"


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def parse_dtu_status(doc: Dict[str, Any], clock: Clock) -> Dict[str, Any]:
    """Resposta de /api/status do add-on Hoymiles DTU API -> amostra normalizada."""
    if not isinstance(doc, dict) or not isinstance(doc.get("summary"), dict):
        raise ValueError("resposta sem 'summary': não parece ser a API do DTU")
    s = doc["summary"]
    meta = doc.get("meta") or {}
    panels = [p for p in (doc.get("panels") or []) if isinstance(p, dict)]
    p_kw = _num(s.get("p_total_kw"))
    if p_kw is not None:
        p_w = p_kw * 1000.0
    elif panels:
        p_w = sum(_num(p.get("p_grid")) or 0.0 for p in panels)
    else:
        raise ValueError("resposta sem a potência do solar")
    online = s.get("panels_online")
    if not isinstance(online, int):
        online = sum(1 for p in panels if p.get("online")) if panels else None
    # horário do DTU (local, sem fuso) da leitura mais recente de um microinversor
    dtu_time = None
    stamps = [p.get("last_update") for p in panels if p.get("last_update")]
    if stamps:
        try:
            newest = max(datetime.fromisoformat(str(x)) for x in stamps)
            dtu_time = newest.replace(tzinfo=clock.tz).timestamp()
        except (TypeError, ValueError):
            dtu_time = None
    return {
        "p_w": max(0.0, p_w),
        "e_today_kwh": _num(s.get("e_today_kwh")),
        "e_total_kwh": _num(s.get("e_total_kwh")),
        "panels_online": online,
        "panel_count": s.get("panel_count") if isinstance(s.get("panel_count"), int) else (len(panels) or None),
        "dtu_time": dtu_time,
        "dtu_stale": bool(meta.get("stale")),
        "poll_age_s": _num(meta.get("age_s")) or 0.0,
        "dtu_error": meta.get("last_error"),
        "sig": json.dumps([p_kw, s.get("e_today_kwh"), max(stamps) if stamps else None], separators=(",", ":")),
    }


class SolarSource:
    """Consulta a API do DTU de tempos em tempos e sabe dizer quão confiável está o último dado."""

    kind = "dtu"

    def __init__(self, urls: Sequence[str], clock: Clock, poll_s: float = 10.0, timeout: float = 4.0,
                 auto: bool = False, fetch: Optional[Callable[[str, float], Dict[str, Any]]] = None):
        seen: List[str] = []
        for u in urls:
            su = status_url(u)
            if su and su not in seen:
                seen.append(su)
        self.urls = seen
        self.clock = clock
        self.poll_s = max(2.0, float(poll_s))
        self.timeout = timeout
        self.auto = auto                 # endereços adivinhados: sem DTU, o painel só não mostra o solar
        self._fetch = fetch or self._http_get
        self._lock = threading.Lock()
        self.url: Optional[str] = None   # endereço que respondeu por último
        self.sample: Optional[Dict[str, Any]] = None
        self.ok_at: Optional[float] = None
        self.error: Optional[str] = None
        self.error_at: Optional[float] = None
        self.data_at: Optional[float] = None      # quando o DTU recebeu o dado atual dos microinversores
        self.data_age_known = False
        self._sig: Optional[str] = None
        self._changes: List[float] = []           # instantes em que chegou dado novo (para o intervalo do DTU)
        self._skew: Optional[float] = None        # relógio do DTU menos o nosso, aprendido nas trocas
        self.found = False                        # a API do DTU já respondeu alguma vez

    # ------------------------------------------------------------------ coleta
    @staticmethod
    def _http_get(url: str, timeout: float) -> Dict[str, Any]:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "PainelEnergia"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read(2_000_000).decode("utf-8"))

    def poll_once(self, now: Optional[float] = None) -> bool:
        order = ([self.url] if self.url else []) + [u for u in self.urls if u != self.url]
        errors = []
        for url in order:
            try:
                doc = self._fetch(url, self.timeout)
                sample = parse_dtu_status(doc, self.clock)
            except urllib.error.HTTPError as exc:
                if exc.code == 503:          # o add-on do DTU respondeu, mas ainda não leu o DTU
                    errors.append("%s: o add-on do DTU ainda não tem leitura" % url)
                else:
                    errors.append("%s: HTTP %s" % (url, exc.code))
                continue
            except Exception as exc:
                errors.append("%s: %s" % (url, getattr(exc, "reason", None) or exc))
                continue
            self._accept(url, sample, time.time() if now is None else now)
            return True
        with self._lock:
            self.error = errors[-1] if len(errors) == 1 else "; ".join(errors[:3]) or "sem endereço configurado"
            self.error_at = time.time() if now is None else now
        return False

    def _accept(self, url: str, sample: Dict[str, Any], now: float) -> None:
        with self._lock:
            if not self.found or url != self.url:
                log.info("Solar: lendo o DTU por %s", url)
            self.url, self.found = url, True
            observed = now - (sample.get("poll_age_s") or 0.0)     # quando o add-on do DTU leu o DTU
            dtu_t = sample.get("dtu_time")
            if sample["sig"] != self._sig:
                first = self._sig is None
                self._sig = sample["sig"]
                if first:
                    # na partida não dá para saber desde quando o valor está lá: usa o relógio do DTU, se plausível
                    if dtu_t is not None and observed - 86400 < dtu_t <= observed + 120:
                        self.data_at, self.data_age_known = min(dtu_t, observed), True
                    else:
                        self.data_at, self.data_age_known = observed, False
                else:
                    self.data_at, self.data_age_known = observed, True
                    self._changes = (self._changes + [observed])[-12:]
                    if dtu_t is not None:
                        self._skew = dtu_t - observed
            elif self.data_at is not None and not self.data_age_known and dtu_t is not None and self._skew is not None:
                self.data_at, self.data_age_known = min(dtu_t - self._skew, observed), True
            self.sample = sample
            self.ok_at = now
            self.error = sample.get("dtu_error") if sample.get("dtu_stale") else None

    def run(self, stop: threading.Event) -> None:
        warned = False
        while not stop.is_set():
            try:
                ok = self.poll_once()
                if not ok and not warned and (self.found or not self.auto):
                    log.warning("Solar: sem resposta da API do DTU (%s)", self.error)
                    warned = True
                elif ok:
                    warned = False
            except Exception:
                log.exception("Solar: erro ao consultar o DTU")
            # endereço adivinhado que nunca respondeu: tenta com calma
            stop.wait(self.poll_s if (self.found or not self.auto) else 60)

    # ------------------------------------------------------------------ estado
    def interval(self) -> Optional[float]:
        gaps = [b - a for a, b in zip(self._changes, self._changes[1:]) if b - a > 0]
        return round(statistics.median(gaps)) if len(gaps) >= 2 else None

    def state(self, now: Optional[float] = None) -> Dict[str, Any]:
        """Situação do solar. state: ok | atrasado | expirado | noite | fora | procurando."""
        now = time.time() if now is None else now
        with self._lock:
            s = self.sample
            out: Dict[str, Any] = {
                "configured": True, "source": self.kind, "found": self.found, "url": self.url or (self.urls[0] if self.urls else None),
                "urls": list(self.urls), "auto": self.auto, "error": self.error,
                "interval_s": self.interval(), "poll_s": self.poll_s,
                "p_w": None, "age_s": None, "age_known": self.data_age_known, "data_at": self.data_at,
                "e_today_kwh": None, "e_total_kwh": None, "panels_online": None, "panel_count": None,
            }
            if s is None:
                out["state"] = "procurando" if self.auto else "fora"
                return out
            out.update({k: s.get(k) for k in ("e_today_kwh", "e_total_kwh", "panels_online", "panel_count")})
            out["p_w"] = round(s["p_w"], 1)
            age = None if self.data_at is None else max(0.0, now - self.data_at)
            out["age_s"] = None if age is None else round(age, 1)
            interval = out["interval_s"]
            expire = max(EXPIRE_MIN_S, 3 * interval) if interval else EXPIRE_MIN_S
            out["expire_s"] = expire
            down = self.ok_at is None or now - self.ok_at > max(SOURCE_DOWN_S, 3 * self.poll_s) or s.get("dtu_stale")
            if s.get("panels_online") == 0:
                out["state"] = "noite"           # microinversores desligados: geração zero, com certeza
            elif down:
                out["state"] = "fora"
            elif age is not None and age > expire:
                out["state"] = "expirado"
            elif age is None or age > FRESH_S:
                out["state"] = "atrasado"
            else:
                out["state"] = "ok"
            return out


class DemoSolar(SolarSource):
    """Solar do modo demonstração: a mesma geração do medidor simulado, mas com o atraso típico do DTU."""

    kind = "demo"

    def __init__(self, clock: Clock, power: Callable[[int], float], step_s: int = 300, lag_s: int = 20):
        super().__init__([], clock)
        self.power = power
        self.step_s = step_s
        self.lag_s = lag_s
        self.found = True
        self.url = "demonstração"

    def poll_once(self, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        t = int((now - self.lag_s) // self.step_s * self.step_s)       # última "leitura dos microinversores"
        p = max(0.0, float(self.power(t)))
        sample = {"p_w": p, "e_today_kwh": None, "e_total_kwh": None, "panels_online": 18 if p > 0 else 0,
                  "panel_count": 18, "dtu_time": float(t), "dtu_stale": False, "poll_age_s": 0.0,
                  "dtu_error": None, "sig": str(t)}
        with self._lock:
            if sample["sig"] != self._sig:
                self._changes = (self._changes + [t + self.lag_s])[-12:] if self._sig else self._changes
                self._sig = sample["sig"]
            self.data_at, self.data_age_known = float(t), True
            self.sample, self.ok_at, self.error = sample, now, None
        return True

    def interval(self) -> Optional[float]:
        return float(self.step_s)


# ---------------------------------------------------------------------------------------------
def meter_ref(setting: str, mode: str) -> str:
    """Onde o medidor está em relação ao solar: 'rede' (entrada, vê o saldo) ou 'cargas' (só a casa)."""
    if setting in ("rede", "cargas"):
        return setting
    return "rede"          # o caso comum; no modo bidirecional é a única possibilidade


def _r(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(v, 1)


def compute_flow(meter_w: Optional[float], meter_ok: bool, solar: Dict[str, Any], ref: str,
                 mode: str = "auto") -> Dict[str, Any]:
    """Junta a potência do medidor e a do solar num fluxo coerente (W).

    meter_w: potência ativa total do medidor (positiva = consumindo; negativa = injetando).
    Devolve os três nós (solar, rede, casa), os três fluxos e avisos sobre o que foi estimado.
    """
    notes: List[Dict[str, str]] = []
    st = solar.get("state")
    if mode == "geracao":
        return {"available": False, "reason": "geracao"}

    # ---- solar informado (ou o que dá para dizer dele)
    s_raw: Optional[float] = None
    if st in ("ok", "atrasado"):
        s_raw = float(solar.get("p_w") or 0.0)
    elif st == "noite":
        s_raw = 0.0
    s_known = s_raw is not None
    s_est = st == "atrasado"
    if st == "expirado":
        notes.append({"code": "solar_expirado", "level": "warning",
                      "text": "O DTU não traz dado novo dos microinversores há mais de %d min; o solar mostrado é o "
                              "mínimo que o medidor indica." % round((solar.get("expire_s") or EXPIRE_MIN_S) / 60)})
    elif st in ("fora", "procurando"):
        notes.append({"code": "solar_fora", "level": "warning",
                      "text": "Sem resposta da API do DTU; o solar mostrado é o mínimo que o medidor indica."})

    # ---- medidor
    g: Optional[float] = float(meter_w) if (meter_ok and meter_w is not None) else None
    if g is None:
        notes.append({"code": "medidor_fora", "level": "serious",
                      "text": "O medidor não está enviando; rede e casa ficam sem valor."})

    ref = meter_ref(ref, mode)
    if ref == "cargas" and g is not None and g < -NOTE_TOLERANCE_W:
        notes.append({"code": "ref_rede", "level": "warning",
                      "text": "O medidor está registrando injeção, então ele está na entrada da rede: ajuste "
                              "\"O que o medidor mede\" em Sistema."})
        ref = "rede"

    s = s_raw
    h: Optional[float] = None
    h_est = h_min = g_est = False
    adjusted = 0.0
    if ref == "rede":
        if g is not None:
            if s is None:
                s = max(0.0, -g)                 # injetando X: o solar gera pelo menos X
                s_est = True
                h = max(0.0, g) if g >= 0 else None
                h_est = h_min = h is not None    # comprando G: a casa consome pelo menos G
            else:
                h = g + s
                if h < 0:                        # solar informado menor que a injeção: o DTU está atrasado
                    adjusted = -h
                    s, h = s - h, 0.0
                    s_est = True
                elif s_est:
                    h_est = True
            if adjusted > NOTE_TOLERANCE_W:
                notes.append({"code": "solar_ajustado", "level": "info",
                              "text": "O medidor está injetando %d W a mais do que o solar informado pelo DTU, que "
                                      "atualiza com atraso; o solar foi corrigido para cima." % round(adjusted)})
    else:  # o medidor só vê as cargas da casa: a rede é a diferença
        if g is not None:
            h = max(0.0, g)
        g = None if (h is None or s is None) else h - s
        g_est = s_est

    # ---- fluxos
    flows = {"solar_home": 0.0, "solar_grid": 0.0, "grid_home": 0.0}
    if g is not None:
        flows["grid_home"] = max(0.0, g)
        flows["solar_grid"] = max(0.0, -g)
    if s is not None and h is not None:
        flows["solar_home"] = max(0.0, min(s, h))
    for k in flows:
        if flows[k] < MIN_FLOW_W:
            flows[k] = 0.0
    if s_est and st == "atrasado" and solar.get("age_s") is not None and not adjusted:
        notes.append({"code": "solar_atrasado", "level": "info",
                      "text": "O DTU recebe os dados dos microinversores de tempos em tempos; o solar é de %s atrás e "
                              "o consumo da casa é aproximado." % _ago(solar["age_s"])})

    share = None
    if h and h > 0 and s is not None:
        share = max(0.0, min(1.0, flows["solar_home"] / h))
    out = {
        "available": True, "ref": ref,
        "solar": {"w": _r(s), "raw_w": _r(s_raw), "est": bool(s_est), "min": not s_known and s is not None,
                  "state": st, "age_s": solar.get("age_s")},
        "grid": {"w": _r(g), "est": bool(g_est)},
        "home": {"w": _r(h), "est": bool(h_est), "min": bool(h_min),
                 "solar_share": None if share is None else round(share, 3)},
        "flows": {k: round(v, 1) for k, v in flows.items()},
        "notes": notes,
    }
    out["sig"] = json.dumps([out["solar"]["w"], out["grid"]["w"], out["home"]["w"], st, [n["code"] for n in notes]])
    return out


def _ago(seconds: float) -> str:
    m = int(round(seconds / 60.0))
    return "%d s" % round(seconds) if seconds < 90 else "%d min" % m
