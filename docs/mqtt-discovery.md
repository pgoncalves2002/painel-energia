# Home Assistant por MQTT (modo servidor próprio)

Vale só para o painel rodando como servidor próprio. Com a integração do Home Assistant nada disto é necessário.

O painel publica o medidor no Home Assistant por **MQTT, com descoberta automática**: o dispositivo e os sensores aparecem sozinhos, com unidade e classe corretas, prontos para o painel Energia. Não é preciso editar YAML.

```
medidor ──(HTTP ou MQTT)──▶ Painel de Energia ──(MQTT)──▶ Mosquitto ──▶ Home Assistant
```

Não importa como o medidor envia para o painel (HTTP ou MQTT): o Home Assistant recebe do painel.

## 1. Ligar o Home Assistant ao broker

O `docker-compose.yml` do projeto já sobe um broker Mosquitto. No Home Assistant (versão Container, sem add-ons):

1. **Configurações › Dispositivos e serviços › Adicionar integração › MQTT**.
2. Preencha:

   | Campo | Valor |
   |---|---|
   | Broker | o IP do servidor onde o painel roda (ex.: `192.168.0.10`). Se o Home Assistant roda na mesma máquina com `network_mode: host`, use `127.0.0.1` |
   | Porta | `1883` (ou o valor de `MQTT_PUBLIC_PORT` no `.env`) |
   | Usuário e senha | `MQTT_USERNAME` e `MQTT_PASSWORD` do `.env` |

3. Conclua. Em instantes aparece o dispositivo **Medidor de energia** (fabricante IE Tecnologia).

Se o seu Home Assistant é o **OS/Supervised com o add-on Mosquitto**, faça o contrário: no `.env` do painel aponte `MQTT_HOST` para o IP do Home Assistant (com um usuário do add-on) e remova o serviço `mosquitto` do `docker-compose.yml`.

## 2. Sensores criados

Ativos por padrão:

- Potência ativa total e por fase (W)
- Tensão e corrente por fase, corrente total
- Fator de potência, frequência, potência reativa e aparente totais
- **Energia consumida** e **Energia injetada** (kWh, acumuladas)
- Energia consumida hoje e injetada hoje (zeram à meia-noite)
- Energia consumida por fase
- Temperatura do medidor e sinal Wi-Fi (diagnóstico)

Criados desativados (ative em Configurações › Dispositivos › seu medidor, se quiser): fator de potência, potência reativa e aparente por fase, ângulos, energia injetada por fase.

Os sensores de energia são totais mantidos pelo painel: continuam crescendo mesmo quando o contador interno do medidor zera, o que evita picos falsos no Home Assistant.

## 3. Painel Energia

**Configurações › Painéis › Energia**:

- **Consumo da rede** → `Energia consumida`
- **Retorno à rede** (se você tem solar) → `Energia injetada`
- **Dispositivos individuais** (opcional) → `Energia consumida fase A/B/C`

As estatísticas começam a aparecer depois de uma ou duas horas.

## 4. O painel web dentro do Home Assistant (opcional)

**Configurações › Painéis › Adicionar painel › Página da web** e informe `http://IP-DO-SERVIDOR:8080/`. O painel passa a ter um item na barra lateral. Isso não funciona bem se você ativou o login do painel (`DASH_USER`), porque o navegador não pede senha dentro de páginas embutidas.

## 5. Cartões de exemplo

Em [`cartoes_lovelace.yaml`](../homeassistant/cartoes_lovelace.yaml) há cartões prontos para colar (potência por fase, ponteiro, tensão em 24 h, consumo por dia).

## Tópicos MQTT

| Tópico | Conteúdo |
|---|---|
| `medidor/energia` | o que o medidor publica (JSON bruto), se você escolheu MQTT no medidor |
| `painel-energia/<id>/state` | JSON com todas as grandezas já como números, a cada leitura |
| `painel-energia/<id>/availability` | `online` / `offline` (o medidor parou de enviar) |
| `painel-energia/bridge/status` | `online` / `offline` (o painel caiu) |
| `homeassistant/sensor/painel_energia_<id>/…/config` | descoberta automática (retida) |

## Alternativa: sensores manuais (sem descoberta)

Os arquivos [`sensores_mqtt_manual.yaml`](../homeassistant/sensores_mqtt_manual.yaml) (trifásico) e [`sensores_mqtt_manual_monofasico.yaml`](../homeassistant/sensores_mqtt_manual_monofasico.yaml) criam os sensores lendo **direto o tópico do medidor**. Servem para quem quer que o Home Assistant continue recebendo mesmo com o painel desligado. Requisitos: o medidor precisa enviar por MQTT e a descoberta do painel deve ser desligada (`HA_DISCOVERY=false` no `.env`), senão os sensores ficam duplicados. Nesse modo os sensores de energia usam o contador bruto do medidor.

Para outro tópico ou nome: `python3 tools/gerar_yaml_ha.py --topico meu/topico --nome "Quadro geral"`.

## Problemas comuns

- **O dispositivo não aparece.** Confira em Sistema › Home Assistant, no painel, se o MQTT está "conectado" e se há "medidores anunciados". No Home Assistant, a integração MQTT precisa estar com a descoberta ligada (é o padrão) e o prefixo `homeassistant`.
- **Sensores "indisponíveis".** O medidor parou de enviar há mais de 3 minutos ou o painel está parado. Veja o indicador no topo do painel.
- **Mudei o nome do medidor no painel.** O nome do dispositivo no Home Assistant é atualizado; os identificadores das entidades já criadas não mudam.
- **Excluí o medidor no painel.** Os sensores são removidos do Home Assistant automaticamente.
