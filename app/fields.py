"""Grandezas enviadas pelos medidores IE Tecnologia.

O SM-3W Lite (trifásico) envia um JSON plano com 40 campos, todos como texto:

    {"id":"1","pa":"0.00","pb":"0.00","pc":"0.00","pt":"0.00", ... ,"tpsd":"30.08"}

O SM-W Lite (monofásico) envia um subconjunto (pa, qa, sa, uarms, iarms, pft, pga,
freq, epa_c, epa_g, tpsd, rssi_wifi). Os nomes abaixo são exatamente os do medidor;
o banco, a API e o painel usam os mesmos nomes para facilitar a conferência.
"""
from __future__ import annotations

PHASES = ("a", "b", "c")

# chave, rótulo, unidade, grupo, casas decimais, fase ("a"/"b"/"c"/"t"/None)
_TABLE = [
    ("pa", "Potência ativa · fase A", "W", "potencia", 1, "a"),
    ("pb", "Potência ativa · fase B", "W", "potencia", 1, "b"),
    ("pc", "Potência ativa · fase C", "W", "potencia", 1, "c"),
    ("pt", "Potência ativa · total", "W", "potencia", 1, "t"),
    ("qa", "Potência reativa · fase A", "VAr", "reativa", 1, "a"),
    ("qb", "Potência reativa · fase B", "VAr", "reativa", 1, "b"),
    ("qc", "Potência reativa · fase C", "VAr", "reativa", 1, "c"),
    ("qt", "Potência reativa · total", "VAr", "reativa", 1, "t"),
    ("sa", "Potência aparente · fase A", "VA", "aparente", 1, "a"),
    ("sb", "Potência aparente · fase B", "VA", "aparente", 1, "b"),
    ("sc", "Potência aparente · fase C", "VA", "aparente", 1, "c"),
    ("st", "Potência aparente · total", "VA", "aparente", 1, "t"),
    ("uarms", "Tensão · fase A", "V", "tensao", 2, "a"),
    ("ubrms", "Tensão · fase B", "V", "tensao", 2, "b"),
    ("ucrms", "Tensão · fase C", "V", "tensao", 2, "c"),
    ("iarms", "Corrente · fase A", "A", "corrente", 2, "a"),
    ("ibrms", "Corrente · fase B", "A", "corrente", 2, "b"),
    ("icrms", "Corrente · fase C", "A", "corrente", 2, "c"),
    ("itrms", "Corrente · total", "A", "corrente", 2, "t"),
    ("pfa", "Fator de potência · fase A", "", "fp", 3, "a"),
    ("pfb", "Fator de potência · fase B", "", "fp", 3, "b"),
    ("pfc", "Fator de potência · fase C", "", "fp", 3, "c"),
    ("pft", "Fator de potência · total", "", "fp", 3, "t"),
    ("pga", "Ângulo tensão-corrente · fase A", "°", "angulo", 2, "a"),
    ("pgb", "Ângulo tensão-corrente · fase B", "°", "angulo", 2, "b"),
    ("pgc", "Ângulo tensão-corrente · fase C", "°", "angulo", 2, "c"),
    ("freq", "Frequência", "Hz", "frequencia", 2, None),
    ("yuaub", "Ângulo entre tensões A-B", "°", "angulo_tensao", 2, None),
    ("yuauc", "Ângulo entre tensões A-C", "°", "angulo_tensao", 2, None),
    ("yubuc", "Ângulo entre tensões B-C", "°", "angulo_tensao", 2, None),
    ("tpsd", "Temperatura interna do medidor", "°C", "temperatura", 1, None),
    ("rssi_wifi", "Sinal Wi-Fi", "dBm", "sinal", 0, None),
    # Contadores acumulados (kWh). "_c" = consumido da rede, "_g" = gerado/injetado.
    ("epa_c", "Energia consumida · fase A", "kWh", "energia_c", 2, "a"),
    ("epb_c", "Energia consumida · fase B", "kWh", "energia_c", 2, "b"),
    ("epc_c", "Energia consumida · fase C", "kWh", "energia_c", 2, "c"),
    ("ept_c", "Energia consumida · total", "kWh", "energia_c", 2, "t"),
    ("epa_g", "Energia gerada · fase A", "kWh", "energia_g", 2, "a"),
    ("epb_g", "Energia gerada · fase B", "kWh", "energia_g", 2, "b"),
    ("epc_g", "Energia gerada · fase C", "kWh", "energia_g", 2, "c"),
    ("ept_g", "Energia gerada · total", "kWh", "energia_g", 2, "t"),
]

FIELD_META = {
    k: {"key": k, "label": label, "unit": unit, "group": group, "dec": dec, "phase": phase}
    for (k, label, unit, group, dec, phase) in _TABLE
}

ALL_FIELDS = tuple(k for (k, *_rest) in _TABLE)
COUNTER_FIELDS = tuple(k for k in ALL_FIELDS if FIELD_META[k]["group"] in ("energia_c", "energia_g"))
INSTANT_FIELDS = tuple(k for k in ALL_FIELDS if k not in COUNTER_FIELDS)

# Contador do medidor -> coluna da tabela de energia por intervalo (energy15).
COUNTER_COL = {
    "epa_c": "c_a", "epb_c": "c_b", "epc_c": "c_c", "ept_c": "c_t",
    "epa_g": "g_a", "epb_g": "g_b", "epc_g": "g_c", "ept_g": "g_t",
}
ENERGY_COLS = ("c_a", "c_b", "c_c", "c_t", "g_a", "g_b", "g_c", "g_t")

# Campos por fase, para quem precisa percorrer as três fases.
PHASE_FIELDS = {
    "a": {"p": "pa", "q": "qa", "s": "sa", "u": "uarms", "i": "iarms", "pf": "pfa", "ang": "pga"},
    "b": {"p": "pb", "q": "qb", "s": "sb", "u": "ubrms", "i": "ibrms", "pf": "pfb", "ang": "pgb"},
    "c": {"p": "pc", "q": "qc", "s": "sc", "u": "ucrms", "i": "icrms", "pf": "pfc", "ang": "pgc"},
}

GROUP_LABELS = {
    "potencia": "Potência ativa",
    "reativa": "Potência reativa",
    "aparente": "Potência aparente",
    "tensao": "Tensão",
    "corrente": "Corrente",
    "fp": "Fator de potência",
    "angulo": "Ângulo tensão-corrente",
    "frequencia": "Frequência",
    "angulo_tensao": "Ângulo entre tensões",
    "temperatura": "Temperatura",
    "sinal": "Sinal Wi-Fi",
    "energia_c": "Energia consumida (contador)",
    "energia_g": "Energia gerada (contador)",
}


def round_field(key: str, value):
    """Arredonda um valor conforme as casas decimais da grandeza."""
    if value is None:
        return None
    meta = FIELD_META.get(key)
    return round(value, meta["dec"] if meta else 3)
