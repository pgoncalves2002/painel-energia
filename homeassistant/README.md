# Home Assistant: sensores, painel Energia e automações

A instalação está no [README principal](../README.md). Aqui ficam os detalhes de uso dentro do Home Assistant.

## Nomes das entidades

O dispositivo se chama **Medidor de energia** (ou "Medidor <ID>" quando o ID do Dispositivo não é 1). As entidades seguem `sensor.<dispositivo>_<sensor>`, por exemplo:

- `sensor.medidor_de_energia_potencia_ativa_total`
- `sensor.medidor_de_energia_tensao_fase_a`
- `sensor.medidor_de_energia_energia_consumida`
- `sensor.medidor_de_energia_situacao_da_tensao_fase_a`

Mudar o nome do medidor no painel (Sistema › Medidor) muda o nome do dispositivo no Home Assistant; os identificadores das entidades já criadas não mudam.

## Painel Energia

Configurações › Painéis › Energia:

- **Consumo da rede** → `Energia consumida`
- **Retorno à rede** (com solar) → `Energia injetada`
- **Dispositivos individuais** (opcional) → `Energia consumida fase A/B/C`

As estatísticas aparecem depois de uma ou duas horas.

## Cartões

[`cartoes_lovelace.yaml`](cartoes_lovelace.yaml) tem cartões prontos para colar em um painel do Home Assistant (potência por fase, ponteiro, tensão em 24 h, consumo por dia).

## Automações de exemplo

Aviso quando a tensão de uma fase sai da faixa adequada:

```yaml
automation:
  - alias: "Energia: tensão fora da faixa"
    trigger:
      - platform: state
        entity_id:
          - sensor.medidor_de_energia_situacao_da_tensao_fase_a
          - sensor.medidor_de_energia_situacao_da_tensao_fase_b
          - sensor.medidor_de_energia_situacao_da_tensao_fase_c
        to: ["precaria", "critica", "ausente"]
    action:
      - service: notify.notify
        data:
          message: "{{ trigger.to_state.name }}: {{ trigger.to_state.state }}"
```

Aviso quando o medidor para de enviar:

```yaml
automation:
  - alias: "Energia: medidor parou de enviar"
    trigger:
      - platform: state
        entity_id: sensor.medidor_de_energia_potencia_ativa_total
        to: "unavailable"
        for: "00:05:00"
    action:
      - service: notify.notify
        data:
          message: "O medidor de energia está sem enviar leituras."
```

## Histórico do Home Assistant

O painel usa o próprio banco, então o histórico do Home Assistant só importa para os cartões e o painel Energia. Se quiser poupar espaço, exclua do `recorder` os sensores que não usa:

```yaml
recorder:
  exclude:
    entity_globs:
      - sensor.medidor_de_energia_potencia_reativa*
      - sensor.medidor_de_energia_potencia_aparente*
```

## Problemas comuns

- **A integração não aparece na lista.** A pasta precisa estar em `config/custom_components/painel_energia/` (com o arquivo `manifest.json` direto dentro dela) e o Home Assistant precisa ter sido reiniciado depois da cópia.
- **Sensores "indisponíveis".** O medidor está há mais de 3 minutos sem enviar. Veja o indicador no topo do painel e a lista de mensagens recebidas em Sistema.
- **O painel não aparece na barra lateral.** Confira a opção "Mostrar o painel na barra lateral" e recarregue a página do navegador.
- **MQTT.** Os arquivos `sensores_mqtt_manual*.yaml` e o guia em [`../docs/mqtt-discovery.md`](../docs/mqtt-discovery.md) valem só para o modo servidor próprio.
