// Passo a passo para apontar o medidor para o painel e lista das últimas mensagens recebidas.
// Usado na tela de espera (antes da primeira leitura) e em Sistema.

import * as api from "../api.js";
import * as fmt from "../fmt.js";
import { h, set, status } from "../dom.js";
import { isHa, isAddon, whereOptions } from "../host.js";

function row(k, v, hint) {
  return [h("dt", null, k), h("dd", null, h("span", { class: "code" }, v), hint ? h("span", { class: "muted", style: { fontWeight: "400" } }, " " + hint) : null)];
}

export function guide(app) {
  const st = app.status;
  const ing = st.ingest;
  const local = !ing.host || ["localhost", "127.0.0.1", "[::1]"].includes(ing.host);
  const host = local ? "IP do servidor na sua rede" : ing.host;
  const server = local ? host : "http://" + host;
  const mq = st.mqtt;
  const blocks = [];
  const title = (text) => h("h3", { class: "card-title", style: { margin: "4px 0 8px" } }, text);

  blocks.push(h("ol", { class: "steps" },
    h("li", null, "No computador ou celular, abra o endereço do medidor na rede (por exemplo ", h("span", { class: "code" }, "http://192.168.0.100"),
      "), entre com o usuário e a senha do medidor e abra a tela ", h("b", null, "Configurações"), "."),
    h("li", null, "Na seção ", h("b", null, "NUVEM"), ", marque ", h("b", null, "Habilitar Transmissão"), " e escolha ", h("b", null, "Tipo de envio: Padrão"),
      " (a opção Monitor IE envia para a nuvem do fabricante)."),
    h("li", null, "Preencha os campos como abaixo, toque em ", h("b", null, "Salvar"), " e aguarde: a primeira leitura aparece aqui em até um minuto.")));

  // HTTP primeiro: é o caminho com menos peças, e cada tentativa do medidor aparece em "Mensagens recebidas".
  blocks.push(h("div", null,
    title(mq.enabled ? "Opção A · HTTP POST (a mais simples)" : "Campos do medidor"),
    h("dl", { class: "dl" },
      row("Protocolo", "HTTP POST (Variáveis Payload Único)", "(HTTP GET também funciona)"),
      row("ID do Dispositivo", "1", "(qualquer valor; com mais de um medidor, use um diferente em cada)"),
      row("IP ou Domínio do Servidor", server, local ? "(não use localhost)" : null),
      row("Caminho", ing.path),
      row("Porta", String(ing.http_port)),
      row("Intervalo de transmissão", "30", "segundos (o mínimo aceito pelo medidor)"))));
  if (isHa) {
    if (ing.scheme === "https") {
      blocks.push(h("p", { class: "note" }, "Atenção: este Home Assistant usa HTTPS e o medidor só envia por HTTP. Ative a porta dedicada " + whereOptions() + " e use essa porta no medidor."));
    }
    blocks.push(h("p", { class: "note" }, "O caminho funciona como uma senha" + (ing.local_only ? " e só é aceito de aparelhos da sua rede local" : "") +
      ". Ele foi definido ao adicionar a integração."));
    if (ing.alt_port) {
      blocks.push(h("div", null, title("Alternativa · porta dedicada"),
        h("dl", { class: "dl" }, row("Porta", String(ing.alt_port)), row("Caminho", "/", "(qualquer caminho é aceito nesta porta)"))));
    }
  }
  if (mq.enabled) {
    blocks.push(h("div", null,
      title(isHa ? "Alternativa · MQTT" : "Opção B · MQTT"),
      h("dl", { class: "dl" },
        row("Protocolo", "MQTT (Variáveis Payload Único)"),
        isHa ? row("Servidor", "o broker MQTT usado pelo Home Assistant") : row("IP ou Domínio do Servidor", host, local ? "(não use localhost)" : null),
        isHa ? null : row("Porta", String(ing.mqtt_port || 1883)),
        isHa ? null : (isAddon() ? row("Usuário e senha", "de um usuário do Home Assistant", "(o add-on Mosquitto broker aceita os usuários do HA)") : mq.auth ? row("Usuário e senha", "os mesmos do arquivo .env", "(MQTT_USERNAME / MQTT_PASSWORD)") : row("Usuário e senha", "em branco", "(broker sem login)")),
        row("Tópico", ing.mqtt_topic || "medidor/energia")),
      ing.mqtt_no_login ? h("p", { class: "note", style: { marginTop: "8px" } },
        "Se a tela do medidor não tiver usuário e senha para o MQTT, deixe sem: o broker que acompanha o painel aceita, " +
        "sem login, apenas o envio de leituras nesse tópico.") : null));
    if (!isHa) blocks.push(h("p", { class: "note" }, "Nas duas opções o Home Assistant recebe os dados, porque quem publica para ele é o painel."));
  } else if (!isHa) {
    blocks.push(h("p", { class: "note" }, isAddon() ? "Para os sensores aparecerem no Home Assistant, instale o add-on Mosquitto broker e a integração MQTT; depois reinicie este add-on."
      : "O MQTT está desligado neste painel. Para usar MQTT e o Home Assistant, defina MQTT_HOST no arquivo .env e reinicie."));
  }
  blocks.push(h("p", { class: "note" }, "Dica: fixe o IP do medidor e o do servidor no roteador, para que os endereços não mudem."));
  return h("div", { style: { display: "flex", flexDirection: "column", gap: "16px" } }, blocks);
}

/** Lista viva das últimas mensagens recebidas; atualiza sozinha enquanto a tela estiver aberta. */
export function diagnostics(sc, intervalMs) {
  const box = h("div", null, h("p", { class: "muted" }, "Carregando…"));

  function render(d) {
    const head = h("div", { class: "hero-meta", style: { marginBottom: "10px" } },
      h("span", null, "Aceitas ", h("b", null, String(d.ok))),
      h("span", null, "Rejeitadas ", h("b", null, String(d.failed))),
      Object.keys(d.by_source).length ? h("span", null, "Por via ", h("b", null, Object.entries(d.by_source).map(([k, v]) => k.toUpperCase() + ": " + v).join(" · "))) : null);
    if (!d.recent.length) {
      set(box, head, h("p", { class: "muted" }, "Nenhuma mensagem recebida desde que o painel foi iniciado."));
      return;
    }
    const rows = d.recent.slice(0, 25).map((m) => h("tr", null,
      h("td", { class: "left" }, fmt.timeSec(m.ts)),
      h("td", { class: "left" }, m.source === "mqtt" ? "MQTT" : m.source === "demo" ? "Simulado" : "HTTP " + (m.method || "")),
      h("td", { class: "left" }, m.topic || m.remote || "—"),
      h("td", { class: "left" }, m.ok ? status("good", "aceita") : status("critical", "rejeitada")),
      h("td", { class: "left" }, m.ok ? (m.device ? "medidor " + m.device + " · " + m.fields + " grandezas" : "") : (m.error || "")),
      h("td", { class: "left", style: { maxWidth: "340px", overflow: "hidden", textOverflow: "ellipsis" }, title: m.snippet || "" }, (m.snippet || "").slice(0, 70))));
    set(box, head, h("div", { class: "table-wrap", style: { maxHeight: "300px" } }, h("table", { class: "data" },
      h("thead", null, h("tr", null, ["Hora", "Via", "Origem", "Resultado", "Detalhe", "Início da mensagem"].map((c) => h("th", { class: "left" }, c)))),
      h("tbody", null, rows))));
  }

  const load = () => sc.load("diag", () => api.get("diagnostics"), render);
  load();
  sc.every(intervalMs || 5000, load);
  return box;
}
