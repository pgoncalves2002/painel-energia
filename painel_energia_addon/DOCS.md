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

## Portas

- **8080**: só recebe as leituras do medidor. O painel não é servido nela.
- **8081**: painel direto, só quando a opção está ligada.
- Sem o painel direto, o painel abre apenas por dentro do Home Assistant, com o login dele.

Se a 8080 já estiver em uso, mude o número na aba Configuração (Rede) e use o mesmo número no medidor.

## Dados

O banco fica na pasta de dados do add-on e entra nos backups do Home Assistant. No painel, Sistema › Dados armazenados permite baixar uma cópia.
