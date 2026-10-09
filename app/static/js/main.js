// Inicialização, navegação entre telas e atualização periódica.

import * as api from "./api.js";
import * as fmt from "./fmt.js";
import { h, set, clear, toast, status as statusEl, icon } from "./dom.js";
import { disposeAll } from "./charts.js";
import { stored, store } from "./ui.js";
import { bridge, isHa, isAddon, setHostKind } from "./host.js";
import geral from "./views/geral.js";
import consumo from "./views/consumo.js";
import fases from "./views/fases.js";
import qualidade from "./views/qualidade.js";
import dados from "./views/dados.js";
import sistema from "./views/sistema.js";
import aguardando from "./views/aguardando.js";

const ROUTES = {
  geral: { title: "Visão geral", view: geral },
  consumo: { title: "Consumo", view: consumo },
  fases: { title: "Fases", view: fases },
  qualidade: { title: "Qualidade da energia", view: qualidade },
  dados: { title: "Dados", view: dados },
  sistema: { title: "Sistema", view: sistema },
};
const LIVE_MS = 5000;
const STATUS_MS = 15000;
const CHECK_MS = 10 * 60 * 1000;

const $ = (id) => document.getElementById(id);
const liveListeners = new Set();
let unmount = null;
let currentRoute = null;
let serverDown = false;

export const app = {
  status: null,       // /api/status
  device: null,       // id do medidor selecionado
  live: null,         // /api/live do medidor selecionado
  fields: null,       // /api/fields
  check: null,        // /api/check do medidor selecionado (conferência dos contadores de energia)
  /** Chama fn a cada leitura nova; devolve a função que cancela. */
  onLive(fn) {
    liveListeners.add(fn);
    return () => liveListeners.delete(fn);
  },
  info() {
    return (this.status && this.status.devices.find((d) => d.id === this.device)) || null;
  },
  settings() {
    return (this.status && this.status.settings) || { tariff: 0, credit: 0, mode: "auto" };
  },
  labels() {
    const info = this.info();
    return (info && info.labels) || { a: "Fase A", b: "Fase B", c: "Fase C" };
  },
  phases() {
    const info = this.info();
    return info ? info.phases : 3;
  },
  async refreshStatus() {
    await loadStatus();
  },
  setDevice(id) {
    if (id === this.device) return;
    this.device = id;
    this.live = null;
    this.check = null;
    store("medidor", id);
    syncDeviceSelects();
    pollLive();
    loadCheck();
    navigate(true);
  },
  rerender() {
    navigate(true);
  },
};

// ------------------------------------------------------------------ navegação
function routeName() {
  const name = (location.hash || "#/").replace(/^#\/?/, "").split(/[/?]/)[0];
  return ROUTES[name] ? name : "geral";
}

function navigate(force) {
  const name = routeName();
  if (!force && name === currentRoute) return;
  currentRoute = name;
  if (unmount) {
    try {
      unmount();
    } catch (e) {
      console.error(e);
    }
    unmount = null;
  }
  disposeAll();
  const view = $("view");
  clear(view);
  const route = ROUTES[name];
  $("page-title").textContent = route.title;
  document.title = route.title + " · Painel de Energia";
  for (const nav of [$("nav"), $("tabbar")]) {
    for (const a of nav.querySelectorAll("a")) {
      if (a.dataset.route === name) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
  }
  if (!app.status) {
    view.appendChild(h("div", { class: "card empty" }, h("p", null, serverDown ? "Sem resposta do servidor do painel. Tentando novamente…" : "Carregando…")));
    return;
  }
  const needsDevice = name !== "sistema";
  try {
    unmount = (needsDevice && !app.device ? aguardando : route.view)(view, app) || null;
  } catch (e) {
    console.error(e);
    view.appendChild(h("div", { class: "card empty" }, h("h2", null, "Algo deu errado ao montar esta tela"), h("p", null, String(e.message || e))));
  }
  window.scrollTo(0, 0);
}

// ------------------------------------------------------------------ estado do servidor e do medidor
async function loadStatus() {
  try {
    const st = await api.get("status");
    const hadDevice = !!app.device;
    const before = app.status;
    app.status = st;
    setHostKind(st.host);
    serverDown = false;
    fmt.setTz(st.tz);
    const ids = st.devices.map((d) => d.id);
    if (!app.device || !ids.includes(app.device)) {
      const saved = stored("medidor", null);
      app.device = ids.includes(saved) ? saved : (ids[0] || null);
      app.live = null;
    }
    syncDeviceSelects();
    renderChrome();
    if (!before || hadDevice !== !!app.device) {
      if (app.device) await pollLive();
      loadCheck();
      navigate(true);
    }
  } catch (e) {
    if (e.status === 401 && !isHa) {
      location.reload();
      return;
    }
    serverDown = true;
    renderChrome();
    if (!app.status) navigate(true);
  }
}

/** Conferência dos contadores de energia: só vira aviso no topo quando a unidade parece errada. */
async function loadCheck() {
  const dev = app.device;
  if (!dev) return;
  try {
    const c = await api.get("check", { device: dev });
    if (dev !== app.device) return;
    app.check = Object.assign({ device: dev }, c);
    renderChrome();
  } catch (e) { /* é só um aviso: sem resposta, nada muda */ }
}

async function pollLive() {
  if (!app.device) return;
  const dev = app.device;
  try {
    const live = await api.get("live", { device: dev });
    if (dev !== app.device) return;
    const fresh = !app.live || app.live.ts !== live.ts;
    live.received = Date.now() / 1000;
    live.skew = live.now - live.received;      // diferença entre o relógio do servidor e o do navegador
    app.live = live;
    serverDown = false;
    renderChip();
    if (fresh) for (const fn of [...liveListeners]) fn(live);
  } catch (e) {
    if (e.status === 404) {
      await loadStatus();
    } else {
      serverDown = true;
      renderChip();
    }
  }
}

function syncDeviceSelects() {
  const devices = (app.status && app.status.devices) || [];
  for (const [sel, wrap] of [[$("device-select"), $("device-field")], [$("device-select-m"), $("device-select-m")]]) {
    const many = devices.length > 1;
    wrap.hidden = !many;
    if (!many) continue;
    const sig = devices.map((d) => d.id + ":" + d.name).join("|");
    if (sel.dataset.sig !== sig) {
      clear(sel);
      for (const d of devices) sel.appendChild(h("option", { value: d.id }, d.name));
      sel.dataset.sig = sig;
    }
    sel.value = app.device || "";
  }
}

function ageNow() {
  const live = app.live;
  if (!live || !live.ts) return null;
  return Math.max(0, Date.now() / 1000 + (live.skew || 0) - live.ts);
}

function renderChip() {
  const chip = $("live-chip");
  if (serverDown) {
    set(chip, h("span", { class: "pulse bad" }), h("span", null, "Sem conexão com o painel"));
    return;
  }
  const info = app.info();
  if (!info) {
    set(chip, h("span", { class: "pulse off" }), h("span", null, "Aguardando o medidor"));
    return;
  }
  const age = ageNow();
  const online = info.online && age !== null && age < Math.max(180, 4 * (info.interval || 30));
  if (online) set(chip, h("span", { class: "pulse" }), h("b", null, "Ao vivo"), h("span", null, "· " + fmt.ago(age)));
  else set(chip, h("span", { class: "pulse bad" }), h("b", null, "Sem dados"), h("span", null, age === null ? "" : "· última leitura " + fmt.ago(age)));
}

function renderChrome() {
  renderChip();
  const st = app.status;
  if (!st) return;
  const info = app.info();
  $("brand-sub").textContent = info ? (info.model || "Medidor") + (info.phases === 3 ? " · trifásico" : " · monofásico") : "Aguardando o medidor";
  $("app-version").textContent = "v" + st.version;
  const side = $("side-status");
  clear(side);
  if (st.mqtt.enabled) {
    side.appendChild(statusEl(st.mqtt.connected ? "good" : "critical", st.mqtt.connected ? "MQTT conectado" : "MQTT desconectado"));
  }
  if (st.demo !== "off") side.appendChild(statusEl("neutral", "Modo demonstração"));

  const banner = $("banner");
  const msgs = [];
  if (info && info.id === "DEMO") msgs.push("Você está vendo o medidor de demonstração: os dados são simulados.");
  const browserOff = -new Date().getTimezoneOffset() * 60;
  if (Math.abs(browserOff - st.tz_offset) >= 60) {
    msgs.push("Os horários aparecem no fuso do servidor (" + st.tz + "), diferente do fuso deste aparelho. Se não for o esperado, ajuste " +
      (isHa || isAddon() ? "o fuso horário do Home Assistant (Configurações › Sistema › Geral)." : "a variável TZ no arquivo .env."));
  }
  const chk = app.check && app.check.device === app.device ? app.check.status : null;
  if (chk === "wh") {
    msgs.push("Os contadores de energia deste medidor parecem estar em Wh, não em kWh: por isso o consumo aparece zerado. Veja como corrigir em Sistema › Medidor.");
  } else if (chk === "kwh") {
    msgs.push("A opção COUNTER_UNIT=wh não combina com este medidor: o consumo aparece mil vezes menor. Veja Sistema › Medidor.");
  }
  banner.hidden = msgs.length === 0;
  if (msgs.length) set(banner, icon("info"), h("div", null, msgs.map((m) => h("div", null, m))));
}

// ------------------------------------------------------------------ tema
function effectiveTheme() {
  const t = document.documentElement.getAttribute("data-theme");
  if (t === "light" || t === "dark") return t;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
/** Aplica o tema escolhido; em "automático" segue o sistema ou, dentro do Home Assistant, o tema dele. */
function applyTheme() {
  const t = themeSetting();
  const root = document.documentElement;
  if (t === "light" || t === "dark") root.setAttribute("data-theme", t);
  else if (bridge) root.setAttribute("data-theme", bridge.state().dark ? "dark" : "light");
  else root.removeAttribute("data-theme");
}
function setTheme(t) {
  if (t === "light" || t === "dark") {
    store("tema", t);
  } else {
    try {
      localStorage.removeItem("pe.tema");
    } catch (e) { /* sem armazenamento */ }
  }
  applyTheme();
  navigate(true);
}
function themeSetting() {
  return stored("tema", "auto", ["light", "dark"]);
}
function setDecal(on) {
  if (on) document.documentElement.setAttribute("data-decal", "1");
  else document.documentElement.removeAttribute("data-decal");
  store("padroes", on ? "1" : "0");
  navigate(true);
}

// As telas recebem estas funções pelo objeto app (evita importação circular com este módulo).
app.setTheme = setTheme;
app.themeSetting = themeSetting;
app.setDecal = setDecal;
app.toast = toast;

// ------------------------------------------------------------------ partida
function start() {
  $("theme-btn").addEventListener("click", () => setTheme(effectiveTheme() === "dark" ? "light" : "dark"));
  for (const id of ["device-select", "device-select-m"]) {
    $(id).addEventListener("change", (ev) => app.setDevice(ev.target.value));
  }
  window.addEventListener("hashchange", () => navigate(false));
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (!document.documentElement.getAttribute("data-theme")) navigate(true);
  });
  if (bridge) {
    // dentro do Home Assistant: botão que abre o menu lateral dele e tema acompanhando o dele
    const menu = $("ha-menu");
    menu.addEventListener("click", () => bridge.toggleMenu());
    let last = bridge.state();
    menu.hidden = !last.menu;
    document.documentElement.setAttribute("data-host", "ha");
    bridge.subscribe((st) => {
      menu.hidden = !st.menu;
      const themeChanged = st.dark !== last.dark;
      last = st;
      if (themeChanged && themeSetting() === "auto") {
        applyTheme();
        navigate(true);
      }
    });
    applyTheme();
  }
  navigate(true);
  loadStatus();
  api.get("fields").then((f) => {
    app.fields = f;
  }).catch(() => {});

  setInterval(() => {
    if (!document.hidden) pollLive();
  }, LIVE_MS);
  setInterval(() => {
    if (!document.hidden) loadStatus();
  }, STATUS_MS);
  setInterval(() => {
    if (!document.hidden) loadCheck();
  }, CHECK_MS);
  setInterval(renderChip, 1000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      loadStatus();
      pollLive();
    }
  });
}

start();
