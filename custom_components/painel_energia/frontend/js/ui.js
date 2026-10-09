// Componentes de interface reutilizados pelas telas.

import { h, set, icon, status, cssVar } from "./dom.js";
import * as fmt from "./fmt.js";
import { sparkline } from "./charts.js";

export const PHASES = ["a", "b", "c"];
export const PHASE_KEYS = {
  a: { p: "pa", q: "qa", s: "sa", u: "uarms", i: "iarms", pf: "pfa", ang: "pga" },
  b: { p: "pb", q: "qb", s: "sb", u: "ubrms", i: "ibrms", pf: "pfb", ang: "pgb" },
  c: { p: "pc", q: "qc", s: "sc", u: "ucrms", i: "icrms", pf: "pfc", ang: "pgc" },
};
export const phaseColorVar = (i) => "var(--s" + (i + 1) + ")";

/** Textos conforme o modo de instalação do medidor. */
export function flowLabels(mode) {
  if (mode === "geracao") return { c: "Consumo", g: "Geração", cNow: "Consumindo", gNow: "Gerando", net: "Saldo" };
  if (mode === "bidirecional") return { c: "Consumo da rede", g: "Injetado na rede", cNow: "Consumindo da rede", gNow: "Injetando na rede", net: "Saldo" };
  return { c: "Consumo", g: "Injetado", cNow: "Consumindo", gNow: "Injetando", net: "Saldo" };
}

// ------------------------------------------------------------------ controles
/** Controle segmentado. options: [[valor, rótulo]] */
export function seg(options, value, onChange, label) {
  const el = h("div", { class: "seg", role: "group", "aria-label": label || null });
  const buttons = new Map();
  const select = (v, fire) => {
    for (const [val, btn] of buttons) btn.setAttribute("aria-pressed", val === v ? "true" : "false");
    el.value = v;
    if (fire) onChange(v);
  };
  for (const [val, text] of options) {
    const btn = h("button", { type: "button", onclick: () => el.value !== val && select(val, true) }, text);
    buttons.set(val, btn);
    el.appendChild(btn);
  }
  select(value, false);
  el.select = (v) => select(v, false);
  return el;
}

export function stored(key, fallback, allowed) {
  try {
    const v = localStorage.getItem("pe." + key);
    if (v !== null && (!allowed || allowed.includes(v))) return v;
  } catch (e) { /* armazenamento indisponível: segue com o padrão */ }
  return fallback;
}
export function store(key, value) {
  try {
    localStorage.setItem("pe." + key, value);
  } catch (e) { /* idem */ }
}

export const RANGES = [["1h", "1 h", 3600], ["6h", "6 h", 21600], ["24h", "24 h", 86400], ["7d", "7 dias", 604800], ["30d", "30 dias", 2592000]];
export function rangeSeconds(key) {
  const r = RANGES.find((x) => x[0] === key);
  return r ? r[2] : 86400;
}
export function rangeSeg(keys, value, onChange) {
  return seg(RANGES.filter((r) => keys.includes(r[0])).map((r) => [r[0], r[1]]), value, onChange, "Período");
}

// ------------------------------------------------------------------ blocos de valor
/** Variação com sinal, seta e contexto. goodWhenDown: consumo menor é bom. */
export function delta(pct, ctx, goodWhenDown = true) {
  if (pct === null || pct === undefined || !Number.isFinite(pct)) return null;
  const flat = Math.abs(pct) < 0.5;
  const up = pct > 0;
  const cls = flat ? "flat" : (up === goodWhenDown ? "bad" : "good");
  const text = (up ? "+" : "−") + fmt.num(Math.abs(pct), Math.abs(pct) < 10 ? 1 : 0) + "%";
  return h("span", { class: "delta " + cls }, flat ? null : icon(up ? "up" : "down", "ico delta-ico"),
    h("span", null, flat ? "igual" : text), ctx ? h("span", { class: "delta-ctx" }, " " + ctx) : null);
}

/** Bloco de indicador: rótulo, valor, unidade, linha de apoio, variação e tendência opcionais. */
export function tile({ label, value, unit, sub, deltaEl, spark, sparkColor, title }) {
  const right = spark && spark.filter((v) => v !== null && v !== undefined).length > 2 ? sparkline(spark, sparkColor) : null;
  return h("section", { class: "card tile", title: title || null },
    h("div", { class: "tile-label" }, label),
    h("div", { class: "tile-row" },
      h("div", { class: "tile-value" }, h("span", null, value), unit ? h("span", { class: "tile-unit" }, unit) : null),
      right),
    deltaEl ? h("div", null, deltaEl) : null,
    sub ? h("div", { class: "tile-sub" }, sub) : null);
}

export function sectionTitle(text) {
  return h("h2", { class: "section-title" }, text);
}

/** Barra de participação (parte do todo) com legenda de valores. items: [{ label, value, text, color }] */
export function shareBar(items) {
  const total = items.reduce((a, it) => a + Math.max(0, it.value || 0), 0);
  const bar = h("div", { class: "share", role: "img", "aria-label": items.map((it) => it.label + ": " + it.text).join("; ") });
  for (const it of items) {
    const w = total > 0 ? (Math.max(0, it.value || 0) / total) * 100 : 0;
    if (w > 0) bar.appendChild(h("i", { style: { width: w + "%", background: it.color } }));
  }
  const legend = h("div", { class: "share-legend" }, items.map((it) =>
    h("span", { class: "item", style: { display: "inline-flex", alignItems: "center", gap: "6px" } },
      h("i", { class: "key rect", style: { background: it.color } }), h("span", null, it.label), h("b", null, it.text),
      total > 0 ? h("span", null, fmt.pct((Math.max(0, it.value || 0) / total) * 100)) : null)));
  return h("div", null, bar, legend);
}

/** Lista de barras horizontais (comparar magnitudes). items: [{ label, value, text, note }] */
export function barList(items, color) {
  const max = items.reduce((a, it) => Math.max(a, it.value || 0), 0) || 1;
  return h("div", { style: { display: "grid", gridTemplateColumns: "auto minmax(60px, 1fr) auto", gap: "9px 12px", alignItems: "center", fontSize: "13px" } },
    items.map((it) => [
      h("span", { class: "muted nowrap" }, it.label),
      h("div", { style: { height: "8px" } }, h("i", { style: { display: "block", height: "8px", minWidth: it.value ? "3px" : "0",
        width: ((it.value || 0) / max) * 100 + "%", background: color || "var(--s1)", borderRadius: "0 4px 4px 0" } })),
      h("span", { class: "num nowrap", style: { fontWeight: "600", textAlign: "right" } }, it.text,
        it.note ? h("span", { class: "muted", style: { fontWeight: "400" } }, " " + it.note) : null),
    ]));
}

// ------------------------------------------------------------------ tensão
/** Classifica a tensão de uma fase com os limites vindos do servidor. */
export function voltageState(u, limits, vNom) {
  if (u === null || u === undefined) return { level: "neutral", text: "sem leitura", cat: null };
  if (!limits) return { level: "neutral", text: "", cat: null };
  if (vNom && u < 0.3 * vNom) return { level: "critical", text: "sem tensão", cat: "ausente" };
  if (u >= limits.adeq_low && u <= limits.adeq_high) return { level: "good", text: "adequada", cat: "adequada" };
  if (u >= limits.crit_low && u <= limits.crit_high) return { level: "warning", text: u < limits.adeq_low ? "baixa" : "alta", cat: "precaria" };
  return { level: "critical", text: u < limits.adeq_low ? "muito baixa" : "muito alta", cat: "critica" };
}

/** Limites do eixo em múltiplos de 5 (ou 10), para as marcas ficarem regulares. */
export function niceBounds(lo, hi) {
  let step = 5;
  let yMin = Math.floor((lo - 0.5) / step) * step;
  let yMax = Math.ceil((hi + 0.5) / step) * step;
  if ((yMax - yMin) / step > 6) {
    step = (yMax - yMin) / 10 > 6 ? 20 : 10;
    yMin = Math.floor((lo - 0.5) / step) * step;
    yMax = Math.ceil((hi + 0.5) / step) * step;
  }
  return { yMin, yMax, yStep: step };
}

/** Coloca os blocos de indicador numa grade com colunas equilibradas (sem sobrar um bloco sozinho). */
export function setTiles(el, items) {
  const list = items.filter(Boolean);
  const n = list.length;
  const cols = n <= 5 ? Math.max(n, 2) : n === 6 ? 3 : n <= 8 ? 4 : 5;
  el.style.setProperty("--cols", String(cols));
  set(el, list);
}

// ------------------------------------------------------------------ diagrama fasorial
/**
 * Tensões e correntes das três fases como vetores.
 * Convenção: Va em 0° (para a direita); os ângulos informados pelo medidor giram no sentido horário.
 */
export function phasor(reading, labels, phases) {
  const size = 280;
  const c = size / 2;
  const R = 108;
  const svg = h("svg", { class: "phasor", viewBox: "0 0 " + size + " " + size, role: "img",
    "aria-label": "Diagrama fasorial: tensões e correntes de cada fase" });
  const grid = cssVar("--grid");
  const axis = cssVar("--axis");
  for (const r of [R, R * 0.66, R * 0.33]) svg.appendChild(h("circle", { cx: c, cy: c, r, fill: "none", stroke: grid, "stroke-width": 1 }));
  svg.appendChild(h("line", { x1: c - R - 8, y1: c, x2: c + R + 8, y2: c, stroke: axis, "stroke-width": 1 }));
  svg.appendChild(h("line", { x1: c, y1: c - R - 8, x2: c, y2: c + R + 8, stroke: axis, "stroke-width": 1 }));

  const us = PHASES.slice(0, phases).map((p) => reading[PHASE_KEYS[p].u] || 0);
  const is = PHASES.slice(0, phases).map((p) => reading[PHASE_KEYS[p].i] || 0);
  const uMax = Math.max(...us, 1);
  const iMax = Math.max(...is, 0.01);
  const vAngle = { a: 0, b: -(reading.yuaub === null || reading.yuaub === undefined ? 120 : reading.yuaub),
    c: -(reading.yuauc === null || reading.yuauc === undefined ? 240 : reading.yuauc) };
  const point = (len, deg) => {
    const rad = (deg * Math.PI) / 180;
    return [c + len * Math.cos(rad), c - len * Math.sin(rad)];
  };
  PHASES.slice(0, phases).forEach((p, idx) => {
    const color = cssVar("--s" + (idx + 1));
    const u = us[idx];
    if (u < 0.3 * uMax) return;
    const va = vAngle[p];
    const [x, y] = point((u / uMax) * R, va);
    svg.appendChild(h("line", { x1: c, y1: c, x2: x, y2: y, stroke: color, "stroke-width": 2.5, "stroke-linecap": "round" }));
    svg.appendChild(h("circle", { cx: x, cy: y, r: 4.5, fill: color, stroke: cssVar("--surface"), "stroke-width": 2 }));
    const [lx, ly] = point(R + 16, va);
    svg.appendChild(h("text", { x: lx, y: ly + 4, "text-anchor": "middle" }, "V" + p.toUpperCase()));
    const cur = is[idx];
    const ang = reading[PHASE_KEYS[p].ang];
    if (cur > 0.02 && ang !== null && ang !== undefined) {
      const ia = va - ang;
      const len = Math.max(0.18, (cur / iMax) * 0.72) * R;
      const [ix, iy] = point(len, ia);
      svg.appendChild(h("line", { x1: c, y1: c, x2: ix, y2: iy, stroke: color, "stroke-width": 1.75, "stroke-linecap": "round", "stroke-dasharray": "5 4" }));
      svg.appendChild(h("rect", { x: ix - 3.5, y: iy - 3.5, width: 7, height: 7, fill: color, stroke: cssVar("--surface"), "stroke-width": 1.5,
        transform: "rotate(45 " + ix + " " + iy + ")" }));
    }
  });
  return svg;
}

export function phasorLegend() {
  return h("div", { class: "legend", style: { justifyContent: "center", marginTop: "8px" } },
    h("span", { class: "item" }, h("svg", { width: 26, height: 10, viewBox: "0 0 26 10", "aria-hidden": "true" },
      h("line", { x1: 1, y1: 5, x2: 20, y2: 5, stroke: "currentColor", "stroke-width": 2.5, "stroke-linecap": "round" }),
      h("circle", { cx: 21, cy: 5, r: 3.5, fill: "currentColor" })), "tensão"),
    h("span", { class: "item" }, h("svg", { width: 26, height: 10, viewBox: "0 0 26 10", "aria-hidden": "true" },
      h("line", { x1: 1, y1: 5, x2: 19, y2: 5, stroke: "currentColor", "stroke-width": 1.75, "stroke-dasharray": "5 4" }),
      h("rect", { x: 18, y: 2, width: 6, height: 6, fill: "currentColor", transform: "rotate(45 21 5)" })), "corrente"));
}

/** Sequência de fases deduzida dos ângulos entre tensões. */
export function phaseSequence(reading) {
  const ab = reading.yuaub;
  const ac = reading.yuauc;
  if (ab === null || ab === undefined || ac === null || ac === undefined) return null;
  const near = (v, t) => Math.abs(((v - t + 540) % 360) - 180) < 25;
  if (near(ab, 120) && near(ac, 240)) return { ok: true, text: "A → B → C (direta)" };
  if (near(ab, 240) && near(ac, 120)) return { ok: false, text: "A → C → B (inversa)" };
  return null;
}

// ------------------------------------------------------------------ diversos
export function loadingCard(text) {
  return h("div", { class: "card empty" }, h("p", null, text || "Carregando…"));
}

export function eventItem(ev, labels) {
  const phaseName = ev.phase ? (labels && labels[ev.phase]) || "Fase " + ev.phase.toUpperCase() : null;
  const d = ev.data || {};
  let level = { info: "neutral", aviso: "warning", critico: "critical" }[ev.level] || "neutral";
  let title = ev.msg;
  let meta = "";
  const dur = ev.end_ts ? fmt.duration(ev.end_ts - ev.ts) : null;
  if (ev.kind === "tensao") {
    const lim = d.v_nom ? { low: d.v_nom * 0.92, high: d.v_nom * 1.05 } : null;
    const low = lim && d.min !== null && d.min !== undefined && d.min < lim.low;
    const high = lim && d.max !== null && d.max !== undefined && d.max > lim.high;
    title = phaseName + ": tensão " + (low && high ? "instável" : high ? "alta" : "baixa") + (d.cat === "critica" ? " (faixa crítica)" : " (faixa precária)");
    const parts = [];
    if (low || !high) parts.push("mínima de " + fmt.num(d.min, 1) + " V");
    if (high) parts.push("máxima de " + fmt.num(d.max, 1) + " V");
    meta = parts.join(" · ");
  } else if (ev.kind === "falta_fase") {
    title = phaseName + ": sem tensão";
  } else if (ev.kind === "offline") {
    title = "Sem leituras do medidor";
    meta = "o medidor parou de enviar ou o painel estava fora do ar";
    level = "serious";
  } else if (ev.kind === "contador") {
    title = "Contador de energia do medidor reiniciado ou corrigido";
    const cs = (d.contadores || []).map((x) => x.k + ": " + fmt.num(x.de, 2) + " → " + fmt.num(x.para, 2)).slice(0, 3);
    meta = cs.join(" · ") + ((d.contadores || []).length > 3 ? " …" : "");
  }
  const when = fmt.dateTime(ev.ts) + (ev.open ? " · em andamento" : dur ? " · durou " + dur : "");
  return h("div", { class: "event" }, status(level, ""), h("div", null, h("div", { class: "event-title" }, title),
    meta ? h("div", { class: "event-meta" }, meta) : null), h("div", { class: "event-when" }, when));
}

export { h, set, icon, status };
