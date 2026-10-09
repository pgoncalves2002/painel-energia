#!/usr/bin/env python3
"""Gera sensores MQTT manuais para o Home Assistant, lendo direto o tópico do medidor.

Só é necessário se você NÃO quiser usar a descoberta automática feita pelo painel
(por exemplo, para o Home Assistant continuar recebendo mesmo com o painel desligado).

    python3 tools/gerar_yaml_ha.py > homeassistant/sensores_mqtt_manual.yaml
    python3 tools/gerar_yaml_ha.py --topico casa/medidor --mono
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.fields import FIELD_META  # noqa: E402

# campo -> (nome, classe, unidade, classe de estado, habilitado por padrão)
THREE = [
    ("pt", "Potência ativa total", "power", "W", "measurement"),
    ("pa", "Potência fase A", "power", "W", "measurement"),
    ("pb", "Potência fase B", "power", "W", "measurement"),
    ("pc", "Potência fase C", "power", "W", "measurement"),
    ("uarms", "Tensão fase A", "voltage", "V", "measurement"),
    ("ubrms", "Tensão fase B", "voltage", "V", "measurement"),
    ("ucrms", "Tensão fase C", "voltage", "V", "measurement"),
    ("iarms", "Corrente fase A", "current", "A", "measurement"),
    ("ibrms", "Corrente fase B", "current", "A", "measurement"),
    ("icrms", "Corrente fase C", "current", "A", "measurement"),
    ("itrms", "Corrente total", "current", "A", "measurement"),
    ("pft", "Fator de potência", "power_factor", None, "measurement"),
    ("freq", "Frequência", "frequency", "Hz", "measurement"),
    ("qt", "Potência reativa total", "reactive_power", "var", "measurement"),
    ("st", "Potência aparente total", "apparent_power", "VA", "measurement"),
    ("ept_c", "Energia consumida", "energy", "kWh", "total_increasing"),
    ("ept_g", "Energia injetada", "energy", "kWh", "total_increasing"),
    ("epa_c", "Energia consumida fase A", "energy", "kWh", "total_increasing"),
    ("epb_c", "Energia consumida fase B", "energy", "kWh", "total_increasing"),
    ("epc_c", "Energia consumida fase C", "energy", "kWh", "total_increasing"),
    ("tpsd", "Temperatura do medidor", "temperature", "°C", "measurement"),
]
MONO = [
    ("pa", "Potência ativa", "power", "W", "measurement"),
    ("uarms", "Tensão", "voltage", "V", "measurement"),
    ("iarms", "Corrente", "current", "A", "measurement"),
    ("pft", "Fator de potência", "power_factor", None, "measurement"),
    ("freq", "Frequência", "frequency", "Hz", "measurement"),
    ("qa", "Potência reativa", "reactive_power", "var", "measurement"),
    ("sa", "Potência aparente", "apparent_power", "VA", "measurement"),
    ("epa_c", "Energia consumida", "energy", "kWh", "total_increasing"),
    ("epa_g", "Energia injetada", "energy", "kWh", "total_increasing"),
    ("tpsd", "Temperatura do medidor", "temperature", "°C", "measurement"),
    ("rssi_wifi", "Sinal Wi-Fi", "signal_strength", "dBm", "measurement"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="Gera o YAML de sensores MQTT manuais para o Home Assistant")
    ap.add_argument("--topico", default="medidor/energia", help="tópico em que o medidor publica")
    ap.add_argument("--mono", action="store_true", help="medidor monofásico (SM-W Lite)")
    ap.add_argument("--nome", default="Medidor IE", help="nome do dispositivo no Home Assistant")
    args = ap.parse_args()

    model = "SM-W Lite" if args.mono else "SM-3W Lite"
    out = [
        "# Sensores MQTT manuais para o medidor IE Tecnologia %s." % model,
        "# Gerado por tools/gerar_yaml_ha.py. Leem direto o tópico em que o medidor publica (%s)." % args.topico,
        "#",
        "# Use ESTE arquivo ou a descoberta automática do painel, não os dois (os sensores ficariam duplicados).",
        "# Para usar: copie para a pasta de configuração do Home Assistant e inclua em configuration.yaml:",
        "#",
        "#   homeassistant:",
        "#     packages:",
        "#       medidor_energia: !include sensores_mqtt_manual.yaml",
        "#",
        "# expire_after: se o medidor ficar 3 minutos sem publicar, o sensor aparece como indisponível.",
        "",
        "mqtt:",
        "  sensor:",
    ]
    first = True
    for key, name, dclass, unit, sclass in (MONO if args.mono else THREE):
        assert key in FIELD_META, key
        out.append('    - name: "%s"' % name)
        out.append("      unique_id: medidor_ie_%s" % key)
        out.append('      state_topic: "%s"' % args.topico)
        if dclass == "power_factor":
            out.append('      value_template: "{{ (value_json.%s | float(0) | abs * 100) | round(1) }}"' % key)
            out.append('      unit_of_measurement: "%"')
        elif sclass == "total_increasing":
            # sem valor válido o sensor fica "desconhecido" (um zero seria lido como reinício do contador)
            out.append('      value_template: "{{ value_json.%s | float(none) }}"' % key)
            out.append('      unit_of_measurement: "%s"' % unit)
        else:
            out.append('      value_template: "{{ value_json.%s | float(0) }}"' % key)
            out.append('      unit_of_measurement: "%s"' % unit)
        out.append("      device_class: %s" % dclass)
        out.append("      state_class: %s" % sclass)
        if sclass != "total_increasing":
            out.append("      expire_after: 180")
        if first:
            out.append("      device: &medidor_ie")
            out.append('        identifiers: ["medidor_ie_%s"]' % ("smw" if args.mono else "sm3w"))
            out.append('        name: "%s"' % args.nome)
            out.append('        manufacturer: "IE Tecnologia"')
            out.append('        model: "%s"' % model)
            first = False
        else:
            out.append("      device: *medidor_ie")
        out.append("")
    sys.stdout.write("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
