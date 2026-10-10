# Histórico

## 1.4.0

- Fluxo de energia animado na visão geral (solar, rede e casa, com potências instantâneas), usando a geração do add-on Hoymiles DTU API, encontrado sozinho.
- Tratamento da diferença entre o medidor (a cada 30 s) e o DTU (que atualiza de tempos em tempos): idade real do dado do solar, correção quando a injeção medida supera o solar informado, mínimo garantido pelo medidor quando o DTU fica sem dado novo e solar zero com os microinversores desligados.
- Cartão Geração solar em Sistema, com a opção "O que o medidor mede" (entrada da rede ou só as cargas).

## 1.3.1

- Painel direto sem a aba Sistema.

## 1.3.0

- Painel direto (opcional): abre em http://IP-DO-HOME-ASSISTANT:8081 sem o login do Home Assistant, só para visualizar ou com usuário e senha próprios.

## 1.2.0

- Primeira versão como add-on do Home Assistant.
