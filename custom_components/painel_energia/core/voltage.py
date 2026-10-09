"""Classificação da tensão em regime permanente (referência: PRODIST, Módulo 8).

Para pontos de conexão até 1 kV, o PRODIST classifica a tensão de leitura em relação
à tensão de referência (TR):

    adequada   0,92 TR <= V <= 1,05 TR
    precária   0,87 TR <= V < 0,92 TR   ou   1,05 TR < V <= 1,06 TR
    crítica    V < 0,87 TR              ou   V > 1,06 TR

Nas tabelas oficiais esses limites aparecem arredondados para as tensões padrão:
127 V -> 117/133 (adequada) e 110/135 (crítica); 220 V -> 202/231 e 191/233.

Os indicadores calculados aqui são estimativas a partir das leituras deste medidor.
Não substituem a medição oficial da distribuidora.
"""
from __future__ import annotations

from typing import Dict, Optional

# nominal: (limite crítico inferior, adequada mín., adequada máx., limite crítico superior)
_TABLE = {
    127.0: (110.0, 117.0, 133.0, 135.0),
    220.0: (191.0, 202.0, 231.0, 233.0),
}

DRP_LIMIT = 3.0   # % máximo de leituras na faixa precária
DRC_LIMIT = 0.5   # % máximo de leituras na faixa crítica
FREQ_MIN, FREQ_MAX = 59.9, 60.1  # Hz, operação normal em regime permanente
PF_REF = 0.92     # fator de potência de referência

ABSENT_FRACTION = 0.3  # abaixo de 30% do nominal considera-se fase sem tensão


def limits(v_nom: float) -> Dict[str, float]:
    row = _TABLE.get(float(v_nom))
    if row is None:
        row = (round(0.87 * v_nom, 1), round(0.92 * v_nom, 1), round(1.05 * v_nom, 1), round(1.06 * v_nom, 1))
    return {"crit_low": row[0], "adeq_low": row[1], "adeq_high": row[2], "crit_high": row[3]}


def classify(u: Optional[float], v_nom: Optional[float]) -> Optional[str]:
    """Devolve "adequada", "precaria", "critica" ou "ausente" (None se não der para classificar)."""
    if u is None or not v_nom:
        return None
    if u < ABSENT_FRACTION * v_nom:
        return "ausente"
    lim = limits(v_nom)
    if lim["adeq_low"] <= u <= lim["adeq_high"]:
        return "adequada"
    if lim["crit_low"] <= u <= lim["crit_high"]:
        return "precaria"
    return "critica"


def detect_nominal(u: Optional[float]) -> Optional[float]:
    """Deduz a tensão nominal fase-neutro (127 ou 220 V) a partir de uma leitura."""
    if u is None:
        return None
    if 100.0 <= u <= 150.0:
        return 127.0
    if 180.0 <= u <= 260.0:
        return 220.0
    return None
