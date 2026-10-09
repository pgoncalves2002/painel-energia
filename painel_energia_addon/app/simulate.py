"""Simulador de um medidor trifásico (SM-3W Lite) numa casa típica.

Serve para duas coisas:

  * modo DEMO do painel (variável DEMO=1): preenche um medidor "DEMO" com histórico e
    leituras ao vivo, para conhecer o painel antes de ligar o medidor de verdade;
  * tools/simulador.py: finge ser o medidor e envia por HTTP ou MQTT, para testar a
    instalação de ponta a ponta.

O perfil é determinístico: o mesmo instante sempre gera a mesma carga.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from .timeutil import Clock

_MASK = 0xFFFFFFFFFFFFFFFF


def _h(*xs: int) -> float:
    """Número pseudoaleatório em [0, 1) determinado pelos argumentos."""
    x = 0x9E3779B97F4A7C15
    for v in xs:
        x ^= (int(v) + 0x9E3779B97F4A7C15 + ((x << 6) & _MASK) + (x >> 2)) & _MASK
        x = (x * 0xBF58476D1CE4E5B9) & _MASK
        x ^= x >> 31
    return (x & _MASK) / float(1 << 64)


class HouseSim:
    """Casa com rede 127/220 V trifásica; opcionalmente com geração solar."""

    def __init__(self, clock: Optional[Clock] = None, seed: int = 7, v_nom: float = 127.0,
                 solar_kwp: float = 0.0, device_id: str = "DEMO"):
        self.clock = clock or Clock("America/Sao_Paulo")
        self.seed = seed
        self.v_nom = v_nom
        self.solar_kwp = solar_kwp
        self.device_id = device_id
        self._day_cache: Dict[int, List[Tuple[int, int, Dict[str, Tuple[float, float]], int]]] = {}
        # contadores acumulados (kWh): consumo e geração por fase e total
        self.counters = {k: 0.0 for k in ("epa_c", "epb_c", "epc_c", "ept_c", "epa_g", "epb_g", "epc_g", "ept_g")}
        self._last_ts: Optional[int] = None
        self._last_p: Optional[Tuple[float, float, float]] = None

    # ------------------------------------------------------------ agenda do dia
    def _events(self, day_start: int):
        """Aparelhos ligados em horários do dia: (início_s, duração_s, {fase: (W, fp)}, ciclo_s)."""
        ev = self._day_cache.get(day_start)
        if ev is not None:
            return ev
        d = day_start // 86400
        s = self.seed
        weekday = self.clock.local(day_start + 43200).weekday()
        hot = _h(s, d, 1) < 0.55
        ev = []

        def add(hour: float, jitter_min: float, dur_min: float, loads, cycle=0, key=0):
            start = int(hour * 3600 + (_h(s, d, key, 11) - 0.5) * 2 * jitter_min * 60)
            dur = int(dur_min * 60 * (0.8 + 0.4 * _h(s, d, key, 12)))
            ev.append((start, dur, loads, cycle))

        # chuveiro 220 V entre as fases A e B (5,5 kW)
        add(6.9, 25, 8, {"a": (2750, 1.0), "b": (2750, 1.0)}, key=20)
        add(19.6, 50, 10, {"a": (2750, 1.0), "b": (2750, 1.0)}, key=21)
        if _h(s, d, 22) < 0.5:
            add(22.3, 30, 7, {"a": (2750, 1.0), "b": (2750, 1.0)}, key=22)
        # micro-ondas (fase A)
        add(12.3, 20, 4, {"a": (1300, 0.95)}, key=30)
        add(20.1, 30, 3, {"a": (1300, 0.95)}, key=31)
        # forno elétrico / air fryer (fase B), com termostato
        if _h(s, d, 40) < 0.6:
            add(19.0, 30, 22, {"b": (1500, 1.0)}, cycle=90, key=40)
        # máquina de lavar (fase C), alguns dias
        if _h(s, d, 50) < 0.4:
            add(10.0, 60, 55, {"c": (380, 0.7)}, cycle=240, key=50)
        # ferro de passar (fase A), uma vez por semana
        if weekday == 5:
            add(15.0, 60, 35, {"a": (1000, 1.0)}, cycle=60, key=60)
        # ar-condicionado 220 V entre B e C nos dias quentes
        if hot:
            add(13.5, 40, 200, {"b": (560, 0.92), "c": (560, 0.92)}, cycle=720, key=70)
            add(22.0, 20, 115, {"b": (480, 0.92), "c": (480, 0.92)}, cycle=720, key=71)
        # home office (fase C) em dias úteis
        if weekday < 5:
            add(9.0, 20, 510, {"c": (170, 0.92)}, key=80)
        # TV e eletrônicos (fase B) à noite
        add(19.2, 30, 230, {"b": (160, 0.9)}, key=90)
        # quedas e elevações de tensão ocasionais ficam em self._sag
        if len(self._day_cache) > 6:
            self._day_cache.pop(next(iter(self._day_cache)))
        self._day_cache[day_start] = ev
        return ev

    def _sag(self, day_start: int, sec: int) -> Tuple[Optional[str], float]:
        """Afundamento/elevação de tensão ocasional: devolve (fase, variação em volts)."""
        d = day_start // 86400
        s = self.seed
        r = _h(s, d, 100)
        if r < 0.10:        # afundamento em uma fase por alguns minutos
            start = int(_h(s, d, 101) * 80000)
            dur = int(150 + _h(s, d, 102) * 240)
            if start <= sec < start + dur:
                ph = "abc"[int(_h(s, d, 103) * 3) % 3]
                return ph, -(0.085 + 0.07 * _h(s, d, 104)) * self.v_nom
        elif r > 0.975:     # elevação
            start = int(_h(s, d, 105) * 80000)
            if start <= sec < start + 200:
                return "abc"[int(_h(s, d, 106) * 3) % 3], 0.06 * self.v_nom
        return None, 0.0

    # ------------------------------------------------------------ carga instantânea
    def loads(self, ts: int) -> Dict[str, List[float]]:
        """Potência ativa e reativa (W, VAr) por fase no instante ts, sem a parte solar."""
        day0 = self.clock.day_start(ts)
        sec = ts - day0
        hour = sec / 3600.0
        s = self.seed
        d = day0 // 86400
        pq = {"a": [0.0, 0.0], "b": [0.0, 0.0], "c": [0.0, 0.0]}

        def put(ph: str, w: float, pf: float):
            pq[ph][0] += w
            pq[ph][1] += w * math.tan(math.acos(max(min(pf, 1.0), 0.05)))

        # consumo permanente (roteador, stand-by)
        put("a", 46 + 6 * _h(s, ts // 300, 1), 0.93)
        put("b", 34 + 5 * _h(s, ts // 300, 2), 0.95)
        put("c", 24 + 4 * _h(s, ts // 300, 3), 0.94)
        # geladeira (fase A): liga e desliga o dia todo
        period = 1500 + int(300 * _h(s, d, 4))
        if (ts + int(900 * _h(s, 5))) % period < 0.38 * period:
            put("a", 138 + 8 * _h(s, ts // 60, 6), 0.86)
        # iluminação
        if 17.7 <= hour < 23.4:
            ramp = min(1.0, (hour - 17.7) / 0.8) * min(1.0, (23.4 - hour) / 0.6)
            put("a", 110 * ramp, 0.95)
            put("c", 75 * ramp, 0.95)
        elif 5.8 <= hour < 7.2:
            put("a", 55, 0.95)
        # aparelhos agendados (inclui os que começaram ontem e passam da meia-noite)
        for base_day, base_sec in ((day0, sec), (self.clock.add_days(ts, -1), sec + (day0 - self.clock.add_days(ts, -1)))):
            for start, dur, loads, cycle in self._events(base_day):
                if start <= base_sec < start + dur:
                    if cycle and ((base_sec - start) % cycle) >= 0.68 * cycle:
                        continue
                    for ph, (w, pf) in loads.items():
                        put(ph, w * (0.97 + 0.06 * _h(s, ts // 20, start)), pf)
        return pq

    def solar(self, ts: int) -> float:
        """Geração fotovoltaica total (W)."""
        if self.solar_kwp <= 0:
            return 0.0
        day0 = self.clock.day_start(ts)
        hour = (ts - day0) / 3600.0
        if not 5.8 < hour < 18.2:
            return 0.0
        sun = math.sin(math.pi * (hour - 5.8) / 12.4) ** 1.3
        d = day0 // 86400
        sky = 0.35 + 0.65 * _h(self.seed, d, 200)                   # dia mais ou menos nublado
        cloud = 1.0 - 0.35 * (1.0 - sky) * _h(self.seed, ts // 240, 201)
        return self.solar_kwp * 1000.0 * 0.82 * sun * sky * cloud

    # ------------------------------------------------------------ leitura completa
    def reading(self, ts: int) -> Dict[str, Any]:
        """Gera a leitura do instante ts e avança os contadores desde a leitura anterior."""
        ts = int(ts)
        s = self.seed
        day0 = self.clock.day_start(ts)
        sec = ts - day0
        hour = sec / 3600.0
        pq = self.loads(ts)
        gen = self.solar(ts) / 3.0
        sag_ph, sag_dv = self._sag(day0, sec)
        diurnal = -2.2 * math.exp(-((hour - 19.5) ** 2) / 4.0) + 1.3 * math.exp(-((hour - 3.5) ** 2) / 8.0)
        offs = {"a": 1.1, "b": -0.9, "c": 0.4}
        out: Dict[str, Any] = {"id": self.device_id}
        ps: List[float] = []
        tot_q = tot_s = tot_i = 0.0
        for i, ph in enumerate("abc"):
            p = pq[ph][0] - gen
            q = pq[ph][1] + (0.04 * gen if gen else 0.0)
            sapp = math.hypot(p, q)
            u = self.v_nom * (1 + offs[ph] / 127.0) + diurnal * self.v_nom / 127.0
            u += (_h(s, ts // 10, 300 + i) - 0.5) * 0.9
            u -= 0.045 * (sapp / self.v_nom)                         # queda na fiação com a carga
            if sag_ph == ph:
                u += sag_dv
            cur = sapp / u if u > 1 else 0.0
            pf = (p / sapp) if sapp > 0.5 else 1.0
            ang = math.degrees(math.atan2(q, p)) if sapp > 0.5 else 0.0
            out["p" + ph] = p
            out["q" + ph] = q
            out["s" + ph] = sapp
            out["u%srms" % ph] = u
            out["i%srms" % ph] = cur
            out["pf" + ph] = pf
            out["pg" + ph] = ang
            ps.append(p)
            tot_q += q
            tot_s += sapp
            tot_i += cur
        pt = sum(ps)
        out.update({
            "pt": pt, "qt": tot_q, "st": tot_s, "itrms": tot_i,
            "pft": (pt / tot_s) if tot_s > 0.5 else 1.0,
            "freq": 60.0 + 0.018 * math.sin(ts / 97.0) + (_h(s, ts // 5, 400) - 0.5) * 0.02,
            "yuaub": 120.0 + (_h(s, ts // 30, 401) - 0.5) * 0.5,
            "yuauc": 240.0 + (_h(s, ts // 30, 402) - 0.5) * 0.5,
            "yubuc": 120.0 + (_h(s, ts // 30, 403) - 0.5) * 0.5,
            "tpsd": 29.0 + 5.0 * math.sin(2 * math.pi * (hour - 9.0) / 24.0) + 0.35 * abs(pt) / 1000.0,
            "rssi_wifi": float(round(-61 + (_h(s, ts // 120, 404) - 0.5) * 6)),
        })
        self._advance(ts, (ps[0], ps[1], ps[2]))
        for k, v in self.counters.items():
            out[k] = math.floor(v * 100.0 + 1e-6) / 100.0       # o medidor informa com 2 casas
        return out

    def _advance(self, ts: int, ps: Tuple[float, float, float]) -> None:
        if self._last_ts is not None and self._last_p is not None and ts > self._last_ts:
            dt = ts - self._last_ts
            if dt <= 3600:
                for i, ph in enumerate("abc"):
                    e = (ps[i] + self._last_p[i]) / 2.0 * dt / 3.6e6
                    self.counters["ep%s_%s" % (ph, "c" if e >= 0 else "g")] += abs(e)
                e = (sum(ps) + sum(self._last_p)) / 2.0 * dt / 3.6e6
                self.counters["ept_c" if e >= 0 else "ept_g"] += abs(e)
        self._last_ts, self._last_p = ts, ps

    def resume(self, counters: Dict[str, Optional[float]], last_ts: Optional[int]) -> None:
        """Continua de onde o medidor simulado parou (após reiniciar o painel)."""
        for k in self.counters:
            if counters.get(k) is not None:
                self.counters[k] = float(counters[k])
        self._last_ts, self._last_p = last_ts, None


def as_meter_payload(reading: Dict[str, Any]) -> Dict[str, str]:
    """Formata a leitura como o medidor envia: tudo texto, com duas casas decimais."""
    return {k: (str(v) if k == "id" else "%.2f" % v) for k, v in reading.items()}
