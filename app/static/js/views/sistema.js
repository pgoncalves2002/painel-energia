// Sistema: medidor, ligação, Home Assistant, configurações e dados armazenados.

import * as api from "../api.js";
import * as fmt from "../fmt.js";
import { h, set, status, icon, toast } from "../dom.js";
import { seg, PHASES } from "../ui.js";
import { isHa, isAddon, bridge, whereOptions } from "../host.js";
import { guide, diagnostics } from "./conexao.js";
import { scope } from "../view.js";

function field(label, control, hint) {
  // campos de formulário ficam dentro de <label>; grupos de botões, dentro de <div>
  const tag = control.classList && control.classList.contains("seg") ? "div" : "label";
  return h(tag, { class: "field" }, h("span", { class: "field-label" }, label), control, hint ? h("span", { class: "field-hint" }, hint) : null);
}
function head(title, sub) {
  return h("div", { class: "card-head" }, h("div", null, h("h2", { class: "card-title" }, title), sub ? h("div", { class: "card-sub" }, sub) : null));
}

export default function mount(root, app) {
  const sc = scope();
  const meter = h("section", { class: "card c6" });
  const settings = h("section", { class: "card c6" });
  const conn = h("section", { class: "card c12" });
  const ha = h("section", { class: "card c6" });
  const store = h("section", { class: "card c6" });
  root.append(h("div", { class: "grid" }, meter, settings, conn, ha, store));

  // ---------------------------------------------------------------- conferência dos contadores
  // Compara o avanço dos contadores de energia do medidor com o que a potência medida indica.
  const checkEl = h("dd", null, h("span", { class: "muted" }, "calculando…"));
  function renderCheck(c) {
    const nums = "contadores " + fmt.num(c.counter_kwh, 2) + " kWh · pela potência " + fmt.num(c.power_kwh, 2) + " kWh nas últimas " + c.hours + " h";
    const more = (text) => h("div", { class: "muted", style: { fontWeight: "400", marginTop: "2px" } }, text);
    if (c.status === "ok") set(checkEl, status("good", "batem com a potência medida"), more(nums));
    else if (c.status === "aguardando") set(checkEl, h("span", { class: "muted" }, "ainda sem energia suficiente para comparar"));
    else if (c.status === "sem_contador") set(checkEl, h("span", { class: "muted" }, "este medidor não envia contadores; a energia é calculada pela potência"));
    else if (c.status === "wh") {
      set(checkEl, status("serious", "os contadores parecem estar em Wh, não em kWh"),
        more("Eles avançam cerca de 1000 vezes mais que a potência indica, e por isso o consumo fica zerado. Mude a unidade dos contadores para Wh " + whereOptions() + (isHa ? "." : isAddon() ? " e reinicie o add-on." : " (COUNTER_UNIT=wh) e reinicie o painel.")));
    } else if (c.status === "kwh") {
      set(checkEl, status("serious", "a unidade Wh não combina com este medidor"),
        more("Os contadores já chegam em kWh. Volte a unidade dos contadores para kWh " + whereOptions() + "."));
    } else {
      set(checkEl, status("warning", "não batem com a potência medida"),
        more(nums + ". Confira a posição e o sentido dos TCs; se estiver tudo certo, a unidade dos contadores pode ser outra."));
    }
  }
  const loadCheck = () => {
    if (app.device) sc.load("check", () => api.get("check", { device: app.device }), renderCheck, () => {});
  };

  // ---------------------------------------------------------------- medidor
  let editing = false;
  function renderMeter() {
    const info = app.info();
    if (!info) {
      set(meter, head("Medidor"), h("p", { class: "muted" }, "Nenhum medidor enviou dados ainda. Veja abaixo como ligar o medidor ao painel."));
      return;
    }
    if (editing) return;       // não sobrescreve o formulário enquanto a pessoa digita
    const live = app.live;
    const r = (live && live.reading) || {};
    const nameIn = h("input", { class: "input", type: "text", maxlength: 60, value: info.custom_name || "", placeholder: info.name });
    const labelIn = {};
    for (const ph of PHASES.slice(0, info.phases)) {
      const cur = info.labels[ph];
      labelIn[ph] = h("input", { class: "input", type: "text", maxlength: 30, value: cur === "Fase " + ph.toUpperCase() ? "" : cur, placeholder: "Fase " + ph.toUpperCase() });
    }
    for (const el of [nameIn, ...Object.values(labelIn)]) el.addEventListener("input", () => { editing = true; });
    const save = async () => {
      try {
        const labels = {};
        for (const [ph, el] of Object.entries(labelIn)) labels[ph] = el.value;
        await api.post("devices/" + encodeURIComponent(info.id), { name: nameIn.value, labels });
        editing = false;
        await app.refreshStatus();
        renderMeter();
        toast("Nomes salvos.");
      } catch (e) {
        toast("Não foi possível salvar: " + e.message);
      }
    };
    const delWrap = h("span", null);
    const askDelete = () => set(delWrap,
      h("span", { class: "muted" }, "Apagar todas as leituras deste medidor? "),
      h("button", { class: "btn sm danger", type: "button", onclick: async () => {
        try {
          await api.post("devices/" + encodeURIComponent(info.id) + "/delete", {});
          toast("Medidor e dados excluídos.");
          editing = false;
          await app.refreshStatus();
          app.rerender();
        } catch (e) {
          toast("Não foi possível excluir: " + e.message);
        }
      } }, "Sim, excluir"), " ",
      h("button", { class: "btn sm", type: "button", onclick: () => set(delWrap, delBtn) }, "Cancelar"));
    const delBtn = h("button", { class: "btn sm danger", type: "button", onclick: askDelete }, "Excluir este medidor e seus dados");
    set(delWrap, delBtn);

    set(meter, head("Medidor", info.online ? "enviando normalmente" : "sem enviar no momento"),
      h("dl", { class: "dl" },
        h("dt", null, "Situação"), h("dd", null, info.online ? status("good", "online · última leitura " + fmt.ago(info.age)) : status("serious", "sem dados · última leitura " + fmt.ago(info.age))),
        h("dt", null, "ID do equipamento"), h("dd", null, h("span", { class: "code" }, info.id)),
        h("dt", null, "Modelo"), h("dd", null, (info.model || "—") + (info.phases === 3 ? " (trifásico)" : " (monofásico)")),
        h("dt", null, "Rede"), h("dd", null, info.v_nom ? fmt.num(info.v_nom, 0) + " V fase-neutro" : "não identificada"),
        h("dt", null, "Recebendo por"), h("dd", null, { mqtt: "MQTT", http: "HTTP", demo: "simulação" }[info.source] || info.source || "—"),
        h("dt", null, "Intervalo entre leituras"), h("dd", null, info.interval ? fmt.num(info.interval, 0) + " s" : "—"),
        h("dt", null, "Leituras recebidas"), h("dd", null, fmt.num(info.n_readings, 0) + " desde " + fmt.dateLong(info.first_seen)),
        h("dt", null, "Temperatura interna"), h("dd", null, r.tpsd === null || r.tpsd === undefined ? "—" : fmt.num(r.tpsd, 1) + " °C"),
        h("dt", null, "Sinal Wi-Fi"), h("dd", null, r.rssi_wifi === null || r.rssi_wifi === undefined ? "não informado por este firmware" : fmt.num(r.rssi_wifi, 0) + " dBm"),
        h("dt", null, "Contadores do medidor"), h("dd", null, "consumo " + fmt.num(r.ept_c, 2) + " kWh · geração " + fmt.num(r.ept_g, 2) + " kWh"),
        h("dt", null, "Conferência dos contadores"), checkEl),
      h("div", { class: "form-grid", style: { marginTop: "16px" } },
        field("Nome do medidor", nameIn),
        Object.entries(labelIn).map(([ph, el]) => field("Nome da fase " + ph.toUpperCase(), el, ph === "a" ? "ex.: Cozinha, Chuveiros, Ar-condicionado" : null))),
      h("div", { class: "form-actions" }, h("button", { class: "btn primary", type: "button", onclick: save }, "Salvar nomes"), h("span", { class: "spacer", style: { flex: "1" } }), delWrap));
  }

  // ---------------------------------------------------------------- configurações
  function renderSettings() {
    const s = app.settings();
    const tariff = h("input", { class: "input", type: "number", step: "0.01", min: "0", max: "100", value: String(s.tariff), inputmode: "decimal" });
    const credit = h("input", { class: "input", type: "number", step: "0.01", min: "0", max: "100", value: String(s.credit), inputmode: "decimal" });
    const mode = h("select", { class: "select" },
      h("option", { value: "auto" }, "Automático"),
      h("option", { value: "consumo" }, "Só consumo"),
      h("option", { value: "bidirecional" }, "Entrada da rede, com solar"),
      h("option", { value: "geracao" }, "Saída do inversor solar"));
    mode.value = s.mode;
    const vnom = h("select", { class: "select" },
      h("option", { value: "auto" }, "Automática"), h("option", { value: "127" }, "127 V"), h("option", { value: "220" }, "220 V"));
    vnom.value = s.v_nominal === "auto" ? "auto" : String(Math.round(s.v_nominal));
    const save = async () => {
      try {
        await api.post("settings", { tariff: tariff.value.replace(",", "."), credit: credit.value.replace(",", "."), mode: mode.value, v_nominal: vnom.value });
        await app.refreshStatus();
        toast("Configurações salvas.");
      } catch (e) {
        toast("Não foi possível salvar: " + e.message);
      }
    };
    const decalOn = document.documentElement.dataset.decal === "1";
    set(settings, head("Configurações"),
      h("div", { class: "form-grid" },
        field("Tarifa (R$ por kWh)", tariff, "com impostos; está na sua conta de luz"),
        field("Crédito por kWh injetado (R$)", credit, "0 se não quiser abater a energia injetada do custo"),
        field("Modo de instalação", mode, "onde o medidor está ligado; no automático o painel deduz pelas leituras"),
        field("Tensão nominal (fase-neutro)", vnom, "define as faixas de tensão adequada")),
      h("div", { class: "form-actions" }, h("button", { class: "btn primary", type: "button", onclick: save }, "Salvar configurações")),
      h("div", { style: { borderTop: "1px solid var(--border)", marginTop: "18px", paddingTop: "16px", display: "flex", flexDirection: "column", gap: "14px" } },
        field("Tema", seg([["auto", "Automático"], ["light", "Claro"], ["dark", "Escuro"]], app.themeSetting(), (v) => app.setTheme(v), "Tema")),
        field("Acessibilidade", seg([["0", "Só cores"], ["1", "Cores + padrões"]], decalOn ? "1" : "0", (v) => app.setDecal(v === "1"), "Padrões nos gráficos"),
          "acrescenta traços e hachuras às séries, para quem tem dificuldade em distinguir cores")));
  }

  // ---------------------------------------------------------------- ligação e mensagens
  set(conn, head("Ligação do medidor", "como configurar a transmissão no medidor e o que está chegando"),
    h("div", { class: "grid" }, h("div", { class: "c5" }, guide(app)),
      h("div", { class: "c7" }, h("h3", { class: "card-title", style: { margin: "0 0 10px" } }, "Mensagens recebidas"), diagnostics(sc, 5000))));

  // ---------------------------------------------------------------- Home Assistant
  function renderHa() {
    const m = app.status.mqtt;
    const body = [];
    if (isHa) {
      const hai = app.status.ha || {};
      body.push(h("dl", { class: "dl" },
        h("dt", null, "Integração"), h("dd", null, status("good", "rodando dentro do Home Assistant" + (hai.version ? " " + hai.version : ""))),
        h("dt", null, "Sensores"), h("dd", null, (hai.entities || 0) + " criados para " + (app.status.devices.length === 1 ? "o medidor" : "os medidores")),
        h("dt", null, "Recepção do medidor"), h("dd", null, "HTTP direto no Home Assistant" + (m.enabled ? " e MQTT (" + m.topics_in.join(", ") + ")" : "")),
        h("dt", null, "Opções"), h("dd", null, h("a", { href: "#", onclick: (ev) => {
          ev.preventDefault();
          bridge.navigate("/config/integrations/integration/painel_energia");
        } }, "abrir a integração no Home Assistant"))));
      body.push(h("ol", { class: "steps", style: { marginTop: "14px" } },
        h("li", null, "Os sensores aparecem em Configurações › Dispositivos e serviços › Painel de Energia, no dispositivo ", h("b", null, (app.info() && app.info().name) || "Medidor de energia"), "."),
        h("li", null, "No painel Energia do Home Assistant, use ", h("b", null, "Energia consumida"), " em “Consumo da rede” e ", h("b", null, "Energia injetada"), " em “Retorno à rede”."),
        h("li", null, "Para avisos e automações, use os sensores ", h("b", null, "Situação da tensão"), " (adequada, precária, crítica, sem tensão) e a disponibilidade dos sensores (medidor parou de enviar).")));
      set(ha, head("Home Assistant", "sensores nativos, prontos para o painel Energia e para automações"), body);
      return;
    }
    if (!m.enabled) {
      if (isAddon()) {
        body.push(h("div", { class: "callout" }, status("neutral", ""), h("div", null,
          "Os sensores chegam ao Home Assistant por MQTT. Instale o add-on Mosquitto broker, adicione a integração MQTT e reinicie este add-on: ele encontra o broker sozinho.")));
      } else body.push(h("div", { class: "callout" }, status("neutral", ""), h("div", null, "Integração desligada. Defina ", h("span", { class: "code" }, "MQTT_HOST"),
        " no arquivo .env (no docker-compose incluído já vem apontando para o broker Mosquitto) e reinicie o painel.")));
    } else {
      body.push(h("dl", { class: "dl" },
        h("dt", null, "Broker"), h("dd", null, h("span", { class: "code" }, m.host + ":" + m.port), m.auth ? " · com login" : " · sem login"),
        h("dt", null, "Conexão"), h("dd", null, m.connected ? status("good", "conectado" + (m.since ? " " + fmt.ago(Date.now() / 1000 - m.since) : "")) : status("critical", "desconectado" + (m.error ? " · " + m.error : ""))),
        h("dt", null, "Descoberta automática"), h("dd", null, m.ha_discovery ? "ligada (prefixo " + m.ha_prefix + ")" : "desligada"),
        h("dt", null, "Medidores anunciados"), h("dd", null, m.discovered.length ? m.discovered.join(", ") : "nenhum ainda"),
        h("dt", null, "Mensagens"), h("dd", null, fmt.num(m.rx, 0) + " recebidas do medidor · " + fmt.num(m.tx, 0) + " publicadas"),
        h("dt", null, "Tópico do medidor"), h("dd", null, h("span", { class: "code" }, m.topics_in.join(", "))),
        h("dt", null, "Tópicos do painel"), h("dd", null, h("span", { class: "code" }, m.base_topic + "/<id>/state"))));
      body.push(h("ol", { class: "steps", style: { marginTop: "14px" } },
        h("li", null, "No Home Assistant: Configurações › Dispositivos e serviços › Adicionar integração › ", h("b", null, "MQTT"),
          (isAddon() ? ". Com o add-on Mosquitto broker ela costuma ser oferecida sozinha." : ". Informe o IP deste servidor, a porta 1883 e, se houver, o usuário e a senha do .env.")),
        h("li", null, "O dispositivo ", h("b", null, (app.info() && app.info().name) || "Medidor de energia"), " aparece sozinho, com os sensores de potência, tensão, corrente e energia."),
        h("li", null, "No painel Energia do Home Assistant, use ", h("b", null, "Energia consumida"), " em “Consumo da rede” e ", h("b", null, "Energia injetada"), " em “Retorno à rede”.")));
    }
    set(ha, head("Home Assistant", "sensores publicados por MQTT, com descoberta automática"), body);
  }

  // ---------------------------------------------------------------- dados armazenados
  function renderStore() {
    const st = app.status;
    set(store, head("Dados armazenados"),
      h("dl", { class: "dl" },
        h("dt", null, "Tamanho do banco"), h("dd", null, fmt.bytes(st.db.size)),
        h("dt", null, "Leituras individuais"), h("dd", null, st.db.retention_days ? "guardadas por " + st.db.retention_days + " dias" : "guardadas para sempre"),
        h("dt", null, "Resumos de 15 min e energia"), h("dd", null, "guardados para sempre"),
        h("dt", null, "Fuso horário do servidor"), h("dd", null, st.tz),
        h("dt", null, "Painel ligado"), h("dd", null, "há " + fmt.duration(st.uptime)),
        h("dt", null, "Versão"), h("dd", null, st.version + (st.demo !== "off" ? " · modo demonstração (" + st.demo + ")" : "")),
        h("dt", null, "Acesso ao painel"), h("dd", null, isHa || isAddon() ? "protegido pelo login do Home Assistant" : st.auth ? "protegido por usuário e senha" : "sem senha (defina DASH_USER e DASH_PASSWORD no .env para proteger)")),
      h("div", { class: "form-actions" },
        h("a", Object.assign({ class: "btn" }, api.downloadLink("backup")), icon("download"), "Baixar cópia do banco"),
        h("a", { class: "btn", href: "#/dados" }, icon("table"), "Exportar CSV")),
      h("p", { class: "note", style: { marginTop: "12px" } }, "A cópia é um arquivo SQLite completo. Para restaurar, " + (isAddon() ? "pare o add-on e coloque o arquivo como energia.db na pasta de dados dele." : isHa ? "pare o Home Assistant e coloque o arquivo como energia.db na pasta painel_energia, dentro da pasta de configuração." : "pare o painel e coloque o arquivo como energia.db na pasta de dados.")));
  }

  function renderAll() {
    renderMeter();
    renderHa();
    renderStore();
  }
  renderSettings();
  renderAll();
  loadCheck();
  sc.every(60000, loadCheck);
  sc.onCleanup(app.onLive(renderMeter));
  sc.every(15000, async () => {
    await app.refreshStatus();
    renderAll();
  });
  return () => sc.destroy();
}
