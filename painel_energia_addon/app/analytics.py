"""Resumos para o painel: consumo por período, custo, demanda, carga de base e qualidade da energia."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from . import voltage
from .energy import BUCKET
from .fields import ALL_FIELDS, ENERGY_COLS, PHASE_FIELDS, PHASES
from .store import Store


def _cost(e: Dict[str, Any], settings: Dict[str, Any]) -> float:
    return round((e.get("c_t") or 0.0) * settings["tariff"] - (e.get("g_t") or 0.0) * settings["credit"], 2)


def _pack(e: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    out = {c: e.get(c, 0.0) for c in ENERGY_COLS}
    out["cost"] = _cost(e, settings)
    out["net"] = round((e.get("c_t") or 0.0) - (e.get("g_t") or 0.0), 4)
    out["est"] = bool(e.get("est"))
    out["n"] = e.get("n", 0)
    return out


def energy_until(store: Store, dev: str, t0: int, t_cut: int) -> Dict[str, Any]:
    """Energia de t0 até t_cut, rateando o intervalo de 15 min que contém t_cut."""
    t_cut = max(int(t_cut), int(t0))
    floor = t_cut - t_cut % BUCKET
    full = store.energy_sum(dev, t0, floor)
    frac = (t_cut - floor) / float(BUCKET)
    if frac > 0:
        part = store.energy_sum(dev, floor, floor + BUCKET)
        for c in ENERGY_COLS:
            full[c] = round(full[c] + part[c] * frac, 4)
        full["n"] += part["n"]
        full["est"] = full["est"] or part["est"]
    return full


def effective_mode(settings: Dict[str, Any], last30: Dict[str, Any]) -> str:
    """Modo de instalação: o escolhido nas configurações ou deduzido dos dados."""
    mode = settings.get("mode", "auto")
    if mode != "auto":
        return mode
    c, g = last30.get("c_t") or 0.0, last30.get("g_t") or 0.0
    # 0,05 kWh injetados já bastam num medidor recém-ligado; depois vale a proporção sobre o consumo
    if g > max(0.05, 0.02 * c):
        # "só geração" (medidor na saída do inversor) exige um histórico mínimo: ao meio-dia, uma casa
        # com solar também passa horas só injetando
        return "geracao" if (g >= 5.0 and c < 0.05 * g) else "bidirecional"
    return "consumo"


def summary(store: Store, dev: str, now: Optional[float] = None) -> Dict[str, Any]:
    clock = store.clock
    now = int(now or time.time())
    s = store.settings()
    st = store.state(dev)

    day0, day1 = clock.day_start(now), clock.add_days(now, 1)
    yday0 = clock.add_days(now, -1)
    m0, m1 = clock.month_start(now), clock.add_months(now, 1)
    pm0 = clock.add_months(now, -1)
    week0 = clock.add_days(now, -6)
    prev_week0 = clock.add_days(now, -13)

    today = store.energy_sum(dev, day0, day1)
    yesterday = store.energy_sum(dev, yday0, day0)
    yesterday_same = energy_until(store, dev, yday0, yday0 + (now - day0))
    week = store.energy_sum(dev, week0, day1)
    prev_week = store.energy_sum(dev, prev_week0, week0)
    month = store.energy_sum(dev, m0, m1)
    last_month = store.energy_sum(dev, pm0, m0)
    last_month_same = energy_until(store, dev, pm0, min(pm0 + (now - m0), m0))
    last30 = store.energy_sum(dev, clock.add_days(now, -29), day1)

    # ---- projeção do mês: realizado + média diária recente x dias restantes
    days = store.energy(dev, clock.add_days(now, -7), day0, "day")["rows"]
    full_days = [d for d in days if d["n"] >= 90 and d["c_t"] is not None]
    avg_c = avg_g = None
    if full_days:
        avg_c = sum(d["c_t"] for d in full_days) / len(full_days)
        avg_g = sum(d["g_t"] for d in full_days) / len(full_days)
    elif now - day0 >= 3 * 3600 and today["n"] >= 8:
        scale = 86400.0 / (now - day0)
        avg_c, avg_g = today["c_t"] * scale, today["g_t"] * scale
    projection = None
    if avg_c is not None:
        remaining = (m1 - now) / 86400.0
        proj = {"c_t": round(month["c_t"] + avg_c * remaining, 2), "g_t": round(month["g_t"] + avg_g * remaining, 2)}
        projection = {"c_t": proj["c_t"], "g_t": proj["g_t"], "cost": _cost(proj, s),
                      "daily_avg": round(avg_c, 3), "based_on_days": len(full_days)}

    # ---- carga de base: mediana da potência média entre 1h e 5h, últimos 7 dias
    base_load = None
    rows = store.stat_rows(dev, ["pt"], clock.add_days(now, -7), now)
    night = sorted(r["pt_avg"] for r in rows if r["pt_avg"] is not None and 1 <= clock.local(r["ts"]).hour < 5)
    if len(night) >= 8:
        w = night[len(night) // 2]
        if w > 0:
            kwh_month = w * 24 * 30 / 1000.0
            base_load = {"w": round(w, 1), "kwh_month": round(kwh_month, 1),
                         "cost_month": round(kwh_month * s["tariff"], 2), "samples": len(night)}

    first = st.first_seen if st else None
    return {
        "device": dev,
        "now": now,
        "tz": clock.name,
        "tariff": s["tariff"],
        "credit": s["credit"],
        "mode": effective_mode(s, last30),
        "mode_setting": s.get("mode", "auto"),
        "first_data": first,
        "day_start": day0,
        "month_start": m0,
        "month_end": m1,
        "today": _pack(today, s),
        "yesterday": _pack(yesterday, s),
        "yesterday_same_time": _pack(yesterday_same, s),
        "week": _pack(week, s),
        "prev_week": _pack(prev_week, s),
        "month": _pack(month, s),
        "last_month": _pack(last_month, s),
        "last_month_same_time": _pack(last_month_same, s),
        "last30": _pack(last30, s),
        "projection": projection,
        "demand_today": store.demand_max(dev, day0, day1, now),
        "demand_month": store.demand_max(dev, m0, m1, now),
        "peak_today": store.stat_extreme(dev, "pt", day0, day1, "max"),
        "base_load": base_load,
    }


def _percentile(sorted_vals: List[float], p: float) -> Optional[float]:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def quality(store: Store, dev: str, t0: int, t1: int) -> Dict[str, Any]:
    """Indicadores de qualidade da energia no período (tensão, desequilíbrio, frequência, FP).

    As tensões são classificadas em janelas de 10 minutos, como no PRODIST. Quando o período
    já saiu da retenção das leituras brutas, usam-se as médias de 15 minutos.
    """
    st = store.state(dev)
    v_nom = st.v_nom if st else None
    phases = PHASES[: (st.phases if st else 3)]
    fields = [PHASE_FIELDS[p]["u"] for p in PHASES] + ["freq", "pa", "pb", "pc", "pt", "sa", "sb", "sc", "st"]
    window = 600
    raw = store.raw_grouped(dev, fields, t0, t1, window)
    windows: List[Dict[str, Any]] = []
    if len(raw) >= 3:
        for r in raw:
            if r[1] < 3:
                continue
            windows.append({"ts": r[0], **{f: r[i + 2] for i, f in enumerate(fields)}})
        source = "bruto"
    else:
        window = BUCKET
        for r in store.stat_rows(dev, fields, t0, t1):
            windows.append({"ts": r["ts"], **{f: r["%s_avg" % f] for f in fields}})
        source = "agregado"

    out: Dict[str, Any] = {
        "device": dev, "from": int(t0), "to": int(t1), "v_nom": v_nom, "window": window, "source": source,
        "n_windows": len(windows), "limits": voltage.limits(v_nom) if v_nom else None,
        "ref": {"drp": voltage.DRP_LIMIT, "drc": voltage.DRC_LIMIT, "freq_min": voltage.FREQ_MIN,
                "freq_max": voltage.FREQ_MAX, "pf": voltage.PF_REF},
        "phases": {}, "imbalance": None, "freq": None, "pf": {},
    }

    # ---- tensão por fase
    for ph in phases:
        uf = PHASE_FIELDS[ph]["u"]
        vals = [w[uf] for w in windows if w[uf] is not None]
        counts = {"adequada": 0, "precaria": 0, "critica": 0, "ausente": 0}
        hist: Dict[int, int] = {}
        for v in vals:
            cat = voltage.classify(v, v_nom) if v_nom else None
            if cat:
                counts[cat] += 1
            hist[int(round(v))] = hist.get(int(round(v)), 0) + 1
        n = len(vals)
        valid = n - counts["ausente"]
        srt = sorted(vals)
        item: Dict[str, Any] = {
            "n": n, "counts": counts,
            "drp": round(100.0 * counts["precaria"] / valid, 2) if valid and v_nom else None,
            "drc": round(100.0 * counts["critica"] / valid, 2) if valid and v_nom else None,
            "avg": round(sum(vals) / n, 2) if n else None,
            "p01": round(_percentile(srt, 0.01), 2) if n else None,
            "p99": round(_percentile(srt, 0.99), 2) if n else None,
            "hist": sorted(hist.items()),
        }
        if source == "bruto":
            item["min"] = store.raw_extreme(dev, uf, t0, t1, "min")
            item["max"] = store.raw_extreme(dev, uf, t0, t1, "max")
        else:
            item["min"] = store.stat_extreme(dev, uf, t0, t1, "min")
            item["max"] = store.stat_extreme(dev, uf, t0, t1, "max")
        if item["drp"] is not None:
            item["ok"] = item["drp"] <= voltage.DRP_LIMIT and item["drc"] <= voltage.DRC_LIMIT
        out["phases"][ph] = item

    # ---- desequilíbrio de tensão (maior desvio em relação à média das fases)
    if len(phases) == 3:
        imb = []
        for w in windows:
            us = [w[PHASE_FIELDS[p]["u"]] for p in phases]
            if any(u is None for u in us):
                continue
            mean = sum(us) / 3.0
            if v_nom and min(us) < 0.5 * v_nom:
                continue
            if mean > 0:
                imb.append(100.0 * max(abs(u - mean) for u in us) / mean)
        if imb:
            srt = sorted(imb)
            out["imbalance"] = {"avg": round(sum(imb) / len(imb), 2), "p95": round(_percentile(srt, 0.95), 2),
                                "max": round(srt[-1], 2), "n": len(imb)}

    # ---- frequência
    fr = [w["freq"] for w in windows if w["freq"] is not None and w["freq"] > 1]
    if fr:
        within = sum(1 for f in fr if voltage.FREQ_MIN <= f <= voltage.FREQ_MAX)
        fmin = store.stat_extreme(dev, "freq", t0, t1, "min")
        fmax = store.stat_extreme(dev, "freq", t0, t1, "max")
        out["freq"] = {"avg": round(sum(fr) / len(fr), 3), "min": fmin, "max": fmax,
                       "within": round(100.0 * within / len(fr), 2), "n": len(fr)}

    # ---- fator de potência (energia ativa / aparente no período)
    for key, pk, sk in [(p, PHASE_FIELDS[p]["p"], PHASE_FIELDS[p]["s"]) for p in phases] + [("t", "pt", "st")]:
        sp = ss = 0.0
        low = n = 0
        for w in windows:
            p, s_ = w[pk], w[sk]
            if p is None or s_ is None or s_ < 30:
                continue
            sp += abs(p)
            ss += s_
            n += 1
            if abs(p) / s_ < voltage.PF_REF:
                low += 1
        if n and ss > 0:
            out["pf"][key] = {"avg": round(min(sp / ss, 1.0), 3), "below": round(100.0 * low / n, 1), "n": n}
    return out


# ------------------------------------------------------------------ conferência dos contadores
CHECK_MIN_KWH = 0.3       # energia mínima no período para a comparação valer (os contadores andam de 0,01 em 0,01)
CHECK_MAX_GAP_S = 300     # só compara leituras vizinhas


def counter_check(store: Store, dev: str, now: Optional[float] = None, hours: int = 24) -> Dict[str, Any]:
    """Confere os contadores de energia do medidor com a energia que a potência medida indica.

    Para cada par de leituras vizinhas, compara o avanço líquido do contador total (consumo − geração)
    com a potência total integrada no mesmo intervalo. Com tudo certo a razão fica perto de 1; perto de
    1000 indica contadores em Wh. Usa as leituras como foram gravadas, sem passar pelo cálculo de energia
    do painel, por isso serve para validar o painel com um medidor real.
    """
    now = int(now or time.time())
    i_pt, i_c, i_g = (1 + ALL_FIELDS.index(k) for k in ("pt", "ept_c", "ept_g"))
    by_counter = by_power = 0.0
    pairs = 0
    with_counter = False
    prev = None
    for row in store.raw_rows(dev, now - hours * 3600, now + 1):
        cur = (row[0], row[i_pt], row[i_c], row[i_g])
        if cur[2] is not None:
            with_counter = True
        if prev is not None and cur[1] is not None and prev[1] is not None and cur[2] is not None and prev[2] is not None:
            dt = cur[0] - prev[0]
            dc = cur[2] - prev[2]
            dg = (cur[3] - prev[3]) if (cur[3] is not None and prev[3] is not None) else 0.0
            if 0 < dt <= CHECK_MAX_GAP_S and dc >= 0 and dg >= 0:      # fora lacunas e reinícios do contador
                by_counter += abs(dc - dg)
                by_power += abs((cur[1] + prev[1]) / 2.0 * dt / 3600000.0)
                pairs += 1
        prev = cur
    out: Dict[str, Any] = {"hours": hours, "pairs": pairs, "counter_kwh": round(by_counter, 3),
                           "power_kwh": round(by_power, 3), "ratio": None}
    if not with_counter:
        out["status"] = "sem_contador"        # este medidor não envia contadores: a energia vem da potência
    elif by_power < CHECK_MIN_KWH:
        out["status"] = "aguardando"          # pouca energia no período para comparar
    else:
        ratio = by_counter / by_power
        out["ratio"] = round(ratio, 3)
        if 0.8 <= ratio <= 1.25:
            out["status"] = "ok"
        elif 800 <= ratio <= 1250:
            out["status"] = "wh"              # contadores em Wh: defina COUNTER_UNIT=wh
        elif 0.0008 <= ratio <= 0.00125:
            out["status"] = "kwh"             # COUNTER_UNIT=wh definido, mas o medidor já envia kWh
        else:
            out["status"] = "divergente"
    return out
