"""Cálculo de energia a partir dos contadores acumulados do medidor.

O medidor envia contadores de kWh que só crescem (ept_c, ept_g e os de cada fase).
A energia de um intervalo é a diferença entre duas leituras consecutivas. Na prática
os contadores podem:

  * zerar (virada de mês no firmware, restauração de fábrica, troca do medidor);
  * dar um "soluço" (uma leitura levemente menor, ou zeros logo após religar);
  * saltar para um valor absurdo (leitura corrompida).

As regras abaixo evitam que qualquer um desses casos vire um pico falso de consumo.
São funções puras, fáceis de testar.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

BUCKET = 900  # agregação de energia em intervalos de 15 minutos

# Quedas de até este valor nunca são tratadas como reinício do contador.
GLITCH_KWH = 0.5
# Folga somada ao teto físico (arredondamento do contador, pequenas diferenças de relógio).
CAP_MARGIN_KWH = 0.05
# Janela mínima considerada para o teto, em segundos.
CAP_MIN_WINDOW_S = 60.0


def energy_cap(dt_s: Optional[float], max_kw: float) -> float:
    """Maior energia (kWh) fisicamente possível em dt_s segundos na potência máxima."""
    window = max(float(dt_s or 0.0), CAP_MIN_WINDOW_S)
    return max_kw * window / 3600.0 + CAP_MARGIN_KWH


def counter_delta(
    base: Optional[float],
    cur: Optional[float],
    dt_s: Optional[float],
    max_kw: float = 80.0,
) -> Tuple[float, Optional[float], str]:
    """Energia entre a leitura anterior (base) e a atual de um contador acumulado.

    Devolve (delta_kwh, nova_base, situação), em que situação é uma de:

      "primeira"  não havia base; a leitura vira a base e nada é contado
      "ausente"   o medidor não enviou este contador
      "ok"        diferença normal
      "salto"     aumento maior que o fisicamente possível no intervalo; descartado
      "reinicio"  o contador zerou e voltou a acumular; conta-se o valor atual
      "recuo"     o contador diminuiu sem ter zerado (ex.: voltou ao último valor salvo
                  depois de uma queda de energia); nada é contado

    Em caso de dúvida entre "reinicio" e "recuo" a função escolhe "recuo": perder um
    pouco de energia é um erro pequeno, somar o contador inteiro seria um pico falso.
    """
    if cur is None:
        return 0.0, base, "ausente"
    if base is None:
        return 0.0, cur, "primeira"
    cap = energy_cap(dt_s, max_kw)
    diff = cur - base
    if diff >= 0:
        if diff > cap:
            return 0.0, cur, "salto"
        return diff, cur, "ok"
    # o contador diminuiu
    if -diff > GLITCH_KWH and cur <= cap and cur <= 0.5 * base:
        return cur, cur, "reinicio"
    return 0.0, cur, "recuo"


def split_buckets(t0: Optional[int], t1: int, bucket: int = BUCKET) -> List[Tuple[int, float]]:
    """Reparte o intervalo (t0, t1] entre os intervalos de agregação que ele cruza.

    Devolve [(início_do_intervalo, fração)], com as frações somando 1. A energia é
    distribuída proporcionalmente ao tempo, o que é exato para leituras próximas e a
    melhor estimativa neutra quando houve uma lacuna.
    """
    t1 = int(t1)
    if t0 is None or t1 <= t0:
        return [(t1 - (t1 % bucket), 1.0)]
    t0 = int(t0)
    total = float(t1 - t0)
    out: List[Tuple[int, float]] = []
    start = t0
    while start < t1:
        b = start - (start % bucket)
        end = min(b + bucket, t1)
        out.append((b, (end - start) / total))
        start = end
    return out


def integrate_power(p0: Optional[float], p1: Optional[float], dt_s: Optional[float]) -> Tuple[float, float]:
    """Energia (consumida, gerada) em kWh integrando a potência entre duas leituras.

    Usado apenas quando o firmware não envia contadores. Potência positiva conta como
    consumo e negativa como geração/injeção.
    """
    if p1 is None or not dt_s or dt_s <= 0:
        return 0.0, 0.0
    if p0 is None:
        p0 = p1
    if (p0 >= 0) == (p1 >= 0):
        e = (p0 + p1) / 2.0 * dt_s / 3.6e6
        return (e, 0.0) if e >= 0 else (0.0, -e)
    # a potência cruzou o zero no intervalo: reparte pelo ponto de cruzamento (interpolação linear)
    frac = abs(p0) / (abs(p0) + abs(p1))
    e0 = p0 / 2.0 * (dt_s * frac) / 3.6e6
    e1 = p1 / 2.0 * (dt_s * (1.0 - frac)) / 3.6e6
    cons = max(e0, 0.0) + max(e1, 0.0)
    gen = max(-e0, 0.0) + max(-e1, 0.0)
    return cons, gen
