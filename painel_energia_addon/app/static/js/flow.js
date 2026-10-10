// Fluxo de energia instantâneo: solar, rede e casa, com pontos que andam na direção da energia.
// A velocidade e a quantidade de pontos crescem com a potência de cada ligação.

import * as fmt from "./fmt.js";
import { h } from "./dom.js";

const R = 46;
const NODES = {
  solar: { x: 200, y: 74 },
  grid: { x: 64, y: 228 },
  home: { x: 336, y: 228 },
};

// trechos retos e curvas, amostrados em pontos (assim a posição dos pontos não depende do navegador)
function quad(p0, c, p1, steps = 14) {
  const out = [];
  for (let i = 1; i <= steps; i++) {
    const t = i / steps;
    const a = (1 - t) * (1 - t);
    const b = 2 * (1 - t) * t;
    const d = t * t;
    out.push([a * p0[0] + b * c[0] + d * p1[0], a * p0[1] + b * c[1] + d * p1[1]]);
  }
  return out;
}
const PATHS = {
  solar_grid: [[188, 118], [188, 188], ...quad([188, 188], [188, 214], [162, 214]), [108, 214]],
  solar_home: [[212, 118], [212, 188], ...quad([212, 188], [212, 214], [238, 214]), [292, 214]],
  grid_home: [[110, 242], [290, 242]],
};
// cor pela origem da energia: azul é da rede, laranja é do solar (vá para a casa ou para a rede),
// as mesmas cores de "consumo da rede" e "injetado na rede" nos gráficos
const FLOW_INFO = {
  grid_home: { cls: "grid", label: "Da rede para a casa" },
  solar_home: { cls: "solar", label: "Do solar para a casa" },
  solar_grid: { cls: "solar", label: "Do solar para a rede (injeção)" },
};
const LEGEND = [["grid", "Energia da rede"], ["solar", "Energia do solar"]];

function measure(pts) {
  const acc = [0];
  for (let i = 1; i < pts.length; i++) acc.push(acc[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
  return { pts, acc, len: acc[acc.length - 1] };
}
function pointAt(m, dist) {
  const d = Math.max(0, Math.min(m.len, dist));
  let i = 1;
  while (i < m.acc.length - 1 && m.acc[i] < d) i++;
  const seg = m.acc[i] - m.acc[i - 1] || 1;
  const t = (d - m.acc[i - 1]) / seg;
  return [m.pts[i - 1][0] + t * (m.pts[i][0] - m.pts[i - 1][0]), m.pts[i - 1][1] + t * (m.pts[i][1] - m.pts[i - 1][1])];
}
const dOf = (pts) => "M" + pts.map((p) => p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" L");

/** Pontos e velocidade (px/s) para uma potência em W. */
function dynamics(w) {
  if (!w || w < 10) return { n: 0, speed: 0 };
  return {
    n: w < 300 ? 1 : w < 1500 ? 2 : 3,
    speed: Math.min(170, 28 + 38 * Math.log2(1 + w / 200)),
  };
}

// ícones simples, desenhados em uma caixa de 24 × 24
const ICON = {
  solar: "M12 7.5a4.5 4.5 0 1 0 0 9 4.5 4.5 0 0 0 0-9ZM12 1.5v2.5M12 20v2.5M1.5 12H4M20 12h2.5M4.6 4.6l1.8 1.8M17.6 17.6l1.8 1.8M4.6 19.4l1.8-1.8M17.6 6.4l1.8-1.8",
  grid: "M8 22 11 3h2l3 19M5 7h14M6.5 12h11M9.5 3h5M9.8 12 15 22M14.2 12 9 22",
  home: "M3.5 11 12 4l8.5 7M5.5 9.5V20h13V9.5M10 20v-5.5h4V20",
};

export class FlowDiagram {
  constructor() {
    this.reduced = !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);
    this.flow = null;
    this.received = 0;
    this.lines = {};
    this.dots = {};
    this.state = {};
    this.svg = h("svg", { class: "flow-svg", viewBox: "0 0 400 312", role: "img" });
    this.titleEl = h("title", null, "Fluxo de energia");
    this.svg.append(this.titleEl);

    const lineLayer = h("g", null);
    const dotLayer = h("g", null);
    for (const [key, pts] of Object.entries(PATHS)) {
      const info = FLOW_INFO[key];
      const m = measure(pts);
      const d = dOf(pts);
      const line = h("path", { d, class: "flow-line " + info.cls });
      const tip = h("title", null, info.label);
      const hit = h("path", { d, class: "flow-hit" }, tip);
      lineLayer.append(line, hit);
      this.lines[key] = { line, tip, m };
      const dots = [0, 1, 2].map(() => h("circle", { r: 4.5, class: "flow-dot " + info.cls, cx: -20, cy: -20 }));
      dotLayer.append(...dots);
      this.dots[key] = dots;
      this.state[key] = { pos: 0, speed: 0, target: 0, n: 0 };
    }
    this.svg.append(lineLayer, dotLayer);

    this.nodes = {};
    for (const [key, p] of Object.entries(NODES)) {
      const g = h("g", { class: "flow-node " + key });
      const tip = h("title", null, "");
      const C = 2 * Math.PI * R;
      const ring = h("circle", { cx: p.x, cy: p.y, r: R, class: "flow-ring " + key });
      const ringSolar = key === "home" ? h("circle", { cx: p.x, cy: p.y, r: R, class: "flow-ring part solar",
        transform: "rotate(-90 " + p.x + " " + p.y + ")", "stroke-dasharray": "0 " + C }) : null;
      const iconEl = h("path", { d: ICON[key], class: "flow-icon", transform: "translate(" + (p.x - 12) + " " + (p.y - 33) + ")" });
      const value = h("text", { x: p.x, y: p.y + 9, class: "flow-value", "text-anchor": "middle" });
      const sub = h("text", { x: p.x, y: p.y + 26, class: "flow-subtext", "text-anchor": "middle" });
      const name = key === "solar" ? "Solar" : key === "grid" ? "Rede" : "Casa";
      const label = h("text", { x: p.x, y: key === "solar" ? p.y - R - 10 : p.y + R + 20, class: "flow-label", "text-anchor": "middle" }, name);
      g.append(tip, ring, ringSolar, iconEl, value, sub, label);
      this.svg.append(g);
      this.nodes[key] = { g, tip, ring, ringSolar, value, sub, C };
    }

    this.legend = h("div", { class: "legend flow-legend" },
      LEGEND.map(([cls, label]) => h("span", { class: "item" }, h("span", { class: "flow-swatch " + cls }), label)));
    this.el = h("div", { class: "flow" }, this.svg, this.legend);

    this.last = 0;
    this.raf = 0;
    this.tick = this.tick.bind(this);
    if (!this.reduced) this.raf = requestAnimationFrame(this.tick);
    this.ageTimer = setInterval(() => this.renderTexts(), 10000);
  }

  destroy() {
    cancelAnimationFrame(this.raf);
    clearInterval(this.ageTimer);
  }

  /** flow: o objeto "flow" de /api/live. */
  update(flow) {
    this.flow = flow;
    this.received = Date.now() / 1000;
    for (const key of Object.keys(PATHS)) {
      const w = (flow.flows && flow.flows[key]) || 0;
      const dyn = dynamics(w);
      const st = this.state[key];
      st.target = dyn.speed;
      if (dyn.n !== st.n) st.n = dyn.n;
      if (!st.speed) st.speed = dyn.speed;
      const L = this.lines[key];
      L.line.classList.toggle("idle", dyn.n === 0);
      L.tip.textContent = FLOW_INFO[key].label + ": " + (w ? fmt.power(w).text : "nada agora");
      this.dots[key].forEach((dot, i) => dot.classList.toggle("off", i >= dyn.n));
      if (this.reduced) this.place(key);
    }
    this.renderTexts();
  }

  renderTexts() {
    const f = this.flow;
    if (!f) return;
    const age = f.solar.age_s === null || f.solar.age_s === undefined ? null : f.solar.age_s + (Date.now() / 1000 - this.received);
    const N = this.nodes;
    const pfx = (part) => (part.min ? "≥ " : part.est ? "≈ " : "");

    // solar
    const s = f.solar;
    N.solar.value.textContent = s.w === null ? "—" : pfx(s) + fmt.power(s.w).text;
    let sSub = "";                       // cabe dentro do círculo: textos curtos; o detalhe vai no título e nos avisos
    if (s.state === "noite") sSub = "sem sol";
    else if (s.state === "expirado" || s.state === "fora" || s.state === "procurando") sSub = s.w ? "mínimo" : "sem dados";
    else if (age !== null && age > 90) sSub = fmt.ago(age);
    else if (s.est) sSub = "corrigido";
    N.solar.sub.textContent = sSub;
    N.solar.tip.textContent = "Solar: " + N.solar.value.textContent + (s.raw_w !== null && s.raw_w !== s.w ? " (DTU informou " + fmt.power(s.raw_w).text + ")" : "") + (sSub ? " · " + sSub : "");

    // rede
    const g = f.grid;
    if (g.w === null) {
      N.grid.value.textContent = "—";
      N.grid.sub.textContent = f.ref === "cargas" ? "sem dados do solar" : "sem leitura";
    } else {
      N.grid.value.textContent = (g.est ? "≈ " : "") + fmt.power(Math.abs(g.w)).text;
      N.grid.sub.textContent = g.w > 10 ? "comprando" : g.w < -10 ? "injetando" : "equilíbrio";
    }
    N.grid.tip.textContent = "Rede: " + N.grid.value.textContent + " " + N.grid.sub.textContent;

    // casa
    const hm = f.home;
    N.home.value.textContent = hm.w === null ? "—" : pfx(hm) + fmt.power(hm.w).text;
    const share = hm.solar_share;
    N.home.sub.textContent = share === null || share === undefined || !hm.w ? (hm.w === null ? "sem dados" : "") : fmt.num(share * 100, 0) + "% solar";
    N.home.tip.textContent = "Casa: " + N.home.value.textContent + (N.home.sub.textContent ? " · " + N.home.sub.textContent : "");
    const C = N.home.C;
    const gap = share > 0.02 && share < 0.98 ? 3 : 0;
    const solarLen = Math.max(0, (share || 0) * C - gap);
    N.home.ringSolar.setAttribute("stroke-dasharray", solarLen.toFixed(1) + " " + (C - solarLen).toFixed(1));
    N.home.ring.classList.toggle("unknown", hm.w === null || share === null || share === undefined);

    for (const key of ["solar", "grid", "home"]) N[key].g.classList.toggle("estimated", !!(f[key] && (f[key].est || f[key].min)));
    this.svg.setAttribute("aria-label", "Fluxo de energia agora. " + N.solar.tip.textContent + ". " + N.grid.tip.textContent + ". " + N.home.tip.textContent + ".");
  }

  place(key) {
    const st = this.state[key];
    const m = this.lines[key].m;
    this.dots[key].forEach((dot, i) => {
      if (i >= st.n) return;
      const p = pointAt(m, ((st.pos + (i * m.len) / st.n) % m.len + m.len) % m.len);
      dot.setAttribute("cx", p[0].toFixed(1));
      dot.setAttribute("cy", p[1].toFixed(1));
    });
  }

  tick(now) {
    const dt = this.last ? Math.min(0.1, (now - this.last) / 1000) : 0;
    this.last = now;
    for (const key of Object.keys(PATHS)) {
      const st = this.state[key];
      if (!st.n) continue;
      st.speed += (st.target - st.speed) * Math.min(1, dt * 1.5);     // muda de velocidade aos poucos
      st.pos = (st.pos + st.speed * dt) % this.lines[key].m.len;
      this.place(key);
    }
    this.raf = requestAnimationFrame(this.tick);
  }
}

/** Avisos do fluxo (dado do solar atrasado, corrigido, sem resposta...). */
export function flowNotes(flow) {
  if (!flow || !flow.notes || !flow.notes.length) return null;
  return h("ul", { class: "flow-notes" }, flow.notes.map((n) => h("li", { class: "lvl-" + n.level }, n.text)));
}

export function solarFoot(solar) {
  if (!solar || !solar.found) return null;
  const parts = [];
  if (solar.e_today_kwh !== null && solar.e_today_kwh !== undefined) parts.push(["Solar hoje ", fmt.energy(solar.e_today_kwh).text]);
  if (solar.panel_count) parts.push(["Painéis gerando ", (solar.panels_online ?? "—") + " de " + solar.panel_count]);
  if (solar.interval_s) parts.push(["DTU atualiza a cada ", "~" + fmt.duration(solar.interval_s)]);
  if (!parts.length) return null;
  return h("div", { class: "hero-meta flow-foot" }, parts.map(([a, b]) => h("span", null, a, h("b", null, b))));
}

