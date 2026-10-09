"""Fuso horário e limites de períodos (hora, dia, mês) no horário local do servidor."""
from __future__ import annotations

import logging
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import List

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None  # type: ignore

log = logging.getLogger("painel.time")

GROUPS = ("15min", "hour", "day", "month")


class Clock:
    """Converte instantes (epoch, UTC) para o calendário local configurado em TZ."""

    def __init__(self, tzname: str = "America/Sao_Paulo"):
        self.name = tzname
        try:
            self.tz = ZoneInfo(tzname) if ZoneInfo else timezone.utc
        except Exception:  # fuso desconhecido ou tzdata ausente
            log.warning("Fuso horário %r não encontrado; usando UTC.", tzname)
            self.tz = timezone.utc
            self.name = "UTC"

    # ---- conversões básicas -------------------------------------------------
    def local(self, ts: float) -> datetime:
        return datetime.fromtimestamp(ts, self.tz)

    def offset(self, ts: float) -> int:
        """Deslocamento em segundos em relação ao UTC no instante dado."""
        off = self.local(ts).utcoffset()
        return int(off.total_seconds()) if off else 0

    def epoch(self, d: date) -> int:
        """Epoch da meia-noite local do dia d."""
        return int(datetime.combine(d, dtime.min, tzinfo=self.tz).timestamp())

    # ---- inícios de período -------------------------------------------------
    def day_start(self, ts: float) -> int:
        return self.epoch(self.local(ts).date())

    def month_start(self, ts: float) -> int:
        return self.epoch(self.local(ts).date().replace(day=1))

    def add_days(self, ts: float, n: int) -> int:
        """Meia-noite local n dias depois (ou antes) do dia que contém ts."""
        return self.epoch(self.local(ts).date() + timedelta(days=n))

    def add_months(self, ts: float, n: int) -> int:
        """Primeiro dia do mês n meses depois (ou antes) do mês que contém ts."""
        d = self.local(ts).date().replace(day=1)
        idx = d.year * 12 + (d.month - 1) + n
        return self.epoch(date(idx // 12, idx % 12 + 1, 1))

    def days_in_month(self, ts: float) -> int:
        a = self.local(self.month_start(ts)).date()
        b = self.local(self.add_months(ts, 1)).date()
        return (b - a).days

    # ---- agrupamentos -------------------------------------------------------
    def boundaries(self, t0: int, t1: int, group: str) -> List[int]:
        """Inícios dos grupos que cobrem [t0, t1), seguidos do limite final.

        Ex.: para group="day" devolve [meia-noite d0, meia-noite d1, ..., meia-noite dN],
        onde o último valor é o fim (exclusivo) do último grupo.
        """
        t0, t1 = int(t0), int(t1)
        if t1 <= t0:
            t1 = t0 + 1
        out: List[int] = []
        if group == "15min":
            s = t0 - (t0 % 900)
            while s < t1:
                out.append(s)
                s += 900
            out.append(s)
        elif group == "hour":
            s = t0 - ((t0 + self.offset(t0)) % 3600)
            while s < t1:
                out.append(s)
                s += 3600
            out.append(s)
        elif group == "day":
            d = self.local(t0).date()
            s = self.epoch(d)
            while s < t1:
                out.append(s)
                d += timedelta(days=1)
                s = self.epoch(d)
            out.append(s)
        elif group == "month":
            s = self.month_start(t0)
            while s < t1:
                out.append(s)
                s = self.add_months(s, 1)
            out.append(s)
        else:
            raise ValueError("grupo inválido: %r" % (group,))
        return out

    def label(self, ts: int, group: str) -> str:
        """Rótulo ISO local do grupo iniciado em ts (o painel formata para exibição)."""
        dt = self.local(ts)
        if group == "month":
            return dt.strftime("%Y-%m")
        if group == "day":
            return dt.strftime("%Y-%m-%d")
        return dt.strftime("%Y-%m-%dT%H:%M")

    def iso(self, ts: float) -> str:
        return self.local(ts).strftime("%Y-%m-%d %H:%M:%S")
