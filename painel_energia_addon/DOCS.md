# Painel de Energia

Recebe as leituras do medidor **IE Tecnologia SM-3W Lite** (ou SM-W Lite) e mostra um painel completo: tempo real, consumo e custo, fases, qualidade da energia e dados brutos.

## Primeiros passos

1. Inicie o add-on e ligue **Mostrar na barra lateral**.
2. Abra o painel. A tela inicial mostra o que preencher no medidor.
3. No medidor (tela Configurações, seção NUVEM):

| Campo | Valor |
|---|---|
| Habilitar Transmissão | marcado |
| Tipo de envio | `Padrão` |
| Protocolo | `HTTP POST (Variáveis Payload Único)` |
| ID do Dispositivo | `1` |
| IP ou Domínio do Servidor | `http://IP-DO-HOME-ASSISTANT` |
| Caminho | `/api/ingest` |
| Porta | `8080` |
| Intervalo | `30` segundos |

A primeira leitura chega em até um minuto. A lista **Mensagens recebidas** mostra cada envio do medidor e o motivo de qualquer rejeição.

## Sensores no Home Assistant

Os sensores são criados sozinhos por MQTT. Para isso:

1. Instale e inicie o add-on **Mosquitto broker**.
2. Adicione a integração **MQTT** (o Home Assistant costuma oferecê-la sozinho).
3. Reinicie este add-on: ele encontra o broker automaticamente.

Sem o broker o painel funciona normalmente; só não há sensores no Home Assistant. No painel Energia, use **Energia consumida** em "Consumo da rede" e **Energia injetada** em "Retorno à rede".

## Painel sem entrar no Home Assistant

Ligue **Painel direto** na aba Configuração e reinicie o add-on. O painel passa a abrir também em
`http://IP-DO-HOME-ASSISTANT:8081` (por exemplo `http://192.168.0.102:8081`), sem o login do Home Assistant:

- Por padrão ele é **só para visualizar**: tarifa, nomes e exclusões continuam sendo alterados pelo painel dentro do HA.
  Ligue **Permitir alterações no painel direto** se quiser mudar isso.
- Preencha **Usuário** e **Senha do painel direto** para o navegador pedir login. Em branco, qualquer aparelho da rede abre.
- Funciona dentro da rede de casa. Não abra a porta 8081 para a internet.

## Geração solar (DTU Hoymiles)

Com o add-on **Hoymiles DTU API** instalado e iniciado no mesmo Home Assistant, a visão geral mostra o **fluxo de energia agora**: solar, rede e casa, com pontos que andam na direção da energia (mais rápidos com mais potência). O Painel de Energia acha o add-on sozinho; se não achar, informe o endereço na opção **Endereço da API do DTU** (ex.: `http://192.168.0.102:8099`). Para desligar, desmarque **Geração solar do DTU Hoymiles**.

O medidor e o DTU não andam juntos: o medidor manda no máximo a cada 30 s, e o DTU atualiza bem mais vezes. O painel guarda os últimos 30 min do solar e, em **Sistema › Geração solar › Atualização do fluxo**, você escolhe:

- **Alinhado ao medidor** (padrão): cada leitura do medidor é cruzada com o solar do mesmo instante (interpolado entre duas leituras do DTU). Rede, solar e casa batem entre si; o fluxo muda a cada leitura do medidor.
- **Tempo real**: o solar acompanha o DTU a cada ~5 s; a casa fica a da última leitura do medidor e a rede é a diferença, marcada com ≈.

Deixe o **Intervalo de transmissão** do medidor em 30 s, o mínimo aceito. Além disso:

- a casa é calculada como rede + solar (medidor na entrada da rede) ou a rede como casa − solar (medidor só nas cargas; escolha em Sistema › Geração solar);
- a idade do dado do solar é a de quando o DTU recebeu a leitura, e o painel aprende de quanto em quanto tempo o DTU atualiza;
- se o medidor injeta mais do que o solar informado, o solar é corrigido para cima e a casa fica em zero, nunca negativa;
- sem dado novo do DTU por mais de 20 min (ou 3 intervalos), o solar mostrado é o mínimo que a injeção garante;
- com todos os microinversores desligados, o solar é zero, mesmo que o DTU repita o último valor.

Valores aproximados aparecem com ≈ (ou ≥ quando são um mínimo) e com o contorno tracejado; o motivo aparece logo abaixo do fluxo.

## Portas

- **8080**: só recebe as leituras do medidor. O painel não é servido nela.
- **8081**: painel direto, só quando a opção está ligada.
- Sem o painel direto, o painel abre apenas por dentro do Home Assistant, com o login dele.

Se a 8080 já estiver em uso, mude o número na aba Configuração (Rede) e use o mesmo número no medidor.

## Dados

O banco fica na pasta de dados do add-on e entra nos backups do Home Assistant. No painel, Sistema › Dados armazenados permite baixar uma cópia.
