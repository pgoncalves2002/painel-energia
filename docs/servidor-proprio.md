# Servidor próprio (Docker), sem Home Assistant

Esta é a forma alternativa de usar o painel: um contêiner separado, com um broker MQTT opcional.
Se você usa Home Assistant, prefira a integração descrita no [README](../README.md): ela dispensa tudo isto.

## Instalação com Docker

Requisitos: um servidor ou NAS com Docker e Docker Compose, ligado na mesma rede do medidor.

1. Copie esta pasta para o servidor.
2. Crie o arquivo de configuração e **troque a senha do MQTT**:
   ```bash
   cp .env.example .env
   nano .env            # MQTT_PASSWORD, TARIFA_KWH, PUBLIC_URL...
   ```
3. Suba:
   ```bash
   docker compose up -d --build
   ```
4. Abra `http://IP-DO-SERVIDOR:8080`. A tela inicial mostra exatamente o que preencher no medidor e lista as mensagens que chegam.

Para ver o painel com dados antes de ligar o medidor, coloque `DEMO=1` no `.env` (cria um medidor simulado com um mês de histórico; `DEMO=solar` inclui geração). Depois volte para `DEMO=0` e exclua o medidor "DEMO" em Sistema.

Comandos úteis: `docker compose logs -f painel`, `docker compose restart painel`, `docker compose down` (os dados ficam nos volumes).

## Configurar o medidor

No navegador, abra o endereço do medidor (ex.: `http://192.168.0.100`), toque em **Acessar o Sistema**, entre com o usuário e a senha do medidor e vá à tela de transmissão. Marque **Habilitar transmissão**, escolha o método **Padrão** e use uma das opções:

| | Opção A · HTTP (a mais simples) | Opção B · MQTT |
|---|---|---|
| Protocolo | HTTP POST (GET também funciona) | MQTT |
| Servidor | IP do servidor | IP do servidor |
| Porta | `8080` | `1883` |
| Usuário / senha | — | os do `.env` (`MQTT_USERNAME`, `MQTT_PASSWORD`); se a tela do medidor não tiver esses campos, deixe sem |
| Caminho / tópico | `/api/ingest` | `medidor/energia` |
| Intervalo | 30 s | 30 s |

Comece pela opção HTTP: tem menos peças e cada tentativa do medidor, certa ou errada, aparece na lista **Mensagens recebidas** do painel. Nas duas opções o Home Assistant recebe os dados, porque quem publica para ele é o painel. O MQTT só é necessário no medidor se você quiser que o Home Assistant leia o medidor direto, sem passar pelo painel (veja os sensores manuais em `homeassistant/`).

Dicas: fixe no roteador o IP do medidor e o do servidor; o campo **ID do equipamento** do medidor vira o identificador no painel (se tiver mais de um medidor, use IDs diferentes).

Os nomes dos campos na tela do medidor podem variar com a versão do firmware. Se algo não bater, a lista **Mensagens recebidas** (tela inicial e Sistema) mostra o que chegou e por que foi rejeitado.

## Home Assistant por MQTT

Resumo: adicione a integração **MQTT** apontando para o IP do servidor, porta 1883, com o usuário e a senha do `.env`. O dispositivo e os sensores aparecem sozinhos. No painel Energia, use **Energia consumida** em "Consumo da rede" e **Energia injetada** em "Retorno à rede".

Passo a passo, lista de sensores, cartões de exemplo e a alternativa com YAML manual estão em [`mqtt-discovery.md`](mqtt-discovery.md).

## Testar sem o medidor

O simulador finge ser o medidor e envia do mesmo jeito que ele:

```bash
python3 tools/simulador.py --url http://IP-DO-SERVIDOR:8080/api/ingest              # HTTP POST
python3 tools/simulador.py --modo mqtt --mqtt-host IP-DO-SERVIDOR \
        --mqtt-user painel --mqtt-pass SUA-SENHA                                    # MQTT (pip install paho-mqtt)
python3 tools/simulador.py --help                                                   # solar, monofásico, intervalo...
```

## Dados e cópia de segurança

- O banco é um arquivo SQLite (`energia.db`) no volume `painel_data`. Mantenha esse volume em disco local, não em pasta de rede.
- **Leituras individuais** ficam guardadas por `RAW_RETENTION_DAYS` dias (padrão 400). **Resumos de 15 minutos e energia por período ficam para sempre.**
- Cópia: em Sistema › Dados armazenados › *Baixar cópia do banco* (pode ser feito com o painel em uso).
- Restaurar uma cópia:
  ```bash
  docker compose stop painel
  docker compose run --rm --no-deps -v "$PWD:/backup:ro" --entrypoint sh painel -c \
    "rm -f /data/energia.db-wal /data/energia.db-shm && cp /backup/NOME-DA-COPIA.db /data/energia.db"
  docker compose start painel
  ```
- Exportação em CSV (leituras ou energia por hora/dia/mês) na aba Dados.

## Como a energia é calculada

O medidor envia contadores acumulados de kWh (total e por fase, consumo e geração). O painel soma a diferença entre leituras consecutivas, o que mantém os valores certos mesmo se o painel ficar um tempo fora do ar: ao voltar, a energia do intervalo é repartida pelo tempo e marcada como estimada. Reinícios do contador, leituras corrompidas e recuos após queda de energia são detectados e não viram picos falsos. Com geração solar, o total de consumo/injeção vem do contador total do medidor (saldo entre as fases a cada instante), por isso pode diferir da soma das fases.

Para conferir com o seu medidor, a tela **Sistema › Medidor › Conferência dos contadores** compara o avanço dos contadores com a energia que a potência medida indica nas últimas 24 horas. Os dois números devem ficar próximos. Se os contadores do seu medidor vierem em Wh em vez de kWh, o painel avisa no topo da tela; nesse caso defina `COUNTER_UNIT=wh` no `.env` e reinicie.

O custo é uma estimativa: energia consumida × tarifa (menos o crédito por kWh injetado, se você configurar). Não inclui bandeiras, taxa mínima nem iluminação pública.

## Segurança

- Troque `MQTT_PASSWORD`. Com usuário e senha definidos, só quem faz login lê os dados e publica para o Home Assistant. Conexões sem login conseguem apenas **enviar** leituras no tópico do medidor (para medidores cuja tela não tem usuário e senha de MQTT); para exigir login de todos, use `MQTT_METER_ANONYMOUS=false`.
- `DASH_USER` / `DASH_PASSWORD` protegem o painel com login. `INGEST_TOKEN` exige que o medidor envie para `/api/ingest/<token>`.
- O painel foi feito para a rede local. Não abra as portas 8080 e 1883 para a internet; para acesso de fora, use VPN.

## Solução de problemas

| Sintoma | O que verificar |
|---|---|
| Nenhuma mensagem chega | IP e porta do servidor no medidor; servidor e medidor na mesma rede; firewall do servidor liberando 8080/1883; `docker compose ps` |
| Mensagem "rejeitada" na lista | O detalhe diz o motivo. Token: caminho sem `/api/ingest/<token>` |
| Medidor em MQTT e nada chega | `docker compose logs mosquitto` mostra cada conexão. "not authorised": usuário/senha diferentes do `.env`. Sem nenhuma linha do IP do medidor: servidor/porta errados no medidor. Conecta mas nada aparece: tópico diferente de `MQTT_TOPIC_IN` |
| Painel abre, mas "Sem dados" | O medidor parou de enviar: veja o LED e a página do medidor; confira se o IP do servidor mudou |
| Porta 1883 ou 8080 ocupada | Mude `MQTT_PUBLIC_PORT` ou `PAINEL_PORT` no `.env` e use a nova porta no medidor |
| Horários errados | `TZ` no `.env` (padrão `America/Sao_Paulo`) |
| Rejeitada: "o relógio do servidor está atrás da última leitura" | O servidor ligou com a hora errada (comum em Raspberry Pi sem internet). Quando a hora for acertada o painel volta a gravar sozinho; se não for, ele segue com a hora atual depois de algumas leituras |
| Potência aparece, mas o consumo fica zerado | Veja Sistema › Medidor › Conferência dos contadores. Se disser que os contadores estão em Wh, defina `COUNTER_UNIT=wh` no `.env` e reinicie |
| Uma fase com potência negativa | Sem solar: TC dessa fase invertido (inverta os fios do TC no medidor). Com solar: escolha o modo de instalação em Sistema |
| Home Assistant não mostra o medidor | Veja [`mqtt-discovery.md`](mqtt-discovery.md) |

