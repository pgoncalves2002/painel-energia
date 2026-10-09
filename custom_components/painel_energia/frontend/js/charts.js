// Gráficos (ECharts) com as regras visuais do painel:
//  - marcas finas: linhas de 2 px, barras de até 24 px com ponta arredondada, grade em fio contínuo;
//  - um único eixo por gráfico; legenda sempre que houver 2 ou mais séries;
//  - tooltip com todas as séries do instante; cada gráfico tem uma visão em tabela.

import { cssVar, h, clear, icon } from "./dom.js";
import * as fmt from "./fmt.js";

const E = window.echarts;
const registry = new Set();

export function tokens() {
  return {
    surface: cssVar("--surface"), text: cssVar("--text"), text2: cssVar("--text-2"), muted: cssVar("--muted"),
    grid: cssVar("--grid"), axis: cssVar("--axis"), border: cssVar("--border"), neutral: cssVar("--neutral-mark"),
    s: [cssVar("--s1"), cssVar("--s2"), cssVar("--s3")],
    seq: [1, 2, 3, 4, 5, 6, 7].map((i) => cssVar("--seq-" + i)),
    good: cssVar("--good"), warning: cssVar("--warning"), critical: cssVar("--critical"),
    font: cssVar("--font"),
  };
}

/** Cores de série por dimensão: fases (A, B, C) ou fluxo (consumo, injeção). */
export const phaseColor = (i) => tokens().s[i];

export function decalOn() {
  return document.documentElement.dataset.decal === "1" || (window.matchMedia && matchMedia("(forced-colors: active)").matches);
}
// Textura de apoio (daltonismo, impressão): traços a 45° e 135°, nunca horizontais ou verticais.
const DECALS = [
  { symbol: "rect", symbolSize: 1, dashArrayX: [1, 0], dashArrayY: [2, 5], rotation: Math.PI / 4, color: "rgba(0,0,0,0.28)" },
  { symbol: "rect", symbolSize: 1, dashArrayX: [1, 0], dashArrayY: [2, 5], rotation: -Math.PI / 4, color: "rgba(0,0,0,0.28)" },
  { symbol: "rect", symbolSize: 1, dashArrayX: [1, 0], dashArrayY: [1, 3], rotation: Math.PI / 4, color: "rgba(0,0,0,0.28)" },
];
const DASHES = ["solid", [6, 4], [2, 3]];

function base(tk) {
  return {
    useUTC: true,
    animation: false,
    textStyle: { fontFamily: tk.font, color: tk.text2 },
    grid: { left: 2, right: 14, top: 26, bottom: 2, containLabel: true },
    aria: { enabled: true, decal: { show: decalOn(), decals: DECALS } },
  };
}

function tooltipBase(tk, extra) {
  return Object.assign({
    confine: true,
    backgroundColor: tk.surface,
    borderColor: tk.border,
    borderWidth: 1,
    padding: [9, 12],
    textStyle: { color: tk.text, fontFamily: tk.font },
    extraCssText: "box-shadow:0 8px 28px rgba(0,0,0,.16);border-radius:10px;",
    transitionDuration: 0,
  }, extra);
}

/** Monta o conteúdo do tooltip com nós de DOM (rótulos vêm do usuário: nunca via innerHTML). */
export function tip(head, rows, foot) {
  const root = h("div", { class: "tt" }, h("div", { class: "tt-head" }, head));
  for (const r of rows) {
    root.appendChild(h("div", { class: "tt-row" },
      r.color ? h("i", { class: "key " + (r.kind || "line"), style: { background: r.color } }) : null,
      h("span", { class: "n" }, r.name), h("span", { class: "v" }, r.value)));
  }
  if (foot) root.appendChild(h("div", { class: "tt-foot" }, foot));
  return root;
}

function valueAxis(tk, spec, extent, span) {
  const kilo = spec.unit === "W" && extent >= 2000;
  // casas decimais do eixo conforme a amplitude mostrada (evita marcas repetidas como "1, 1, 2, 2")
  const shown = (span === undefined || span <= 0 ? extent : span) / (kilo ? 1000 : 1);
  // tudo zero: o eixo padrão vai de 0 a 1, uma casa basta
  let dec = shown <= 0 ? 1 : shown >= 8 ? 0 : shown >= 0.8 ? 1 : shown >= 0.08 ? 2 : 3;
  if (spec.axisDec !== undefined) dec = spec.axisDec;
  return {
    type: "value",
    unitLabel: kilo ? "kW" : (spec.unit || ""),
    scale: !!spec.scale,
    min: spec.yMin,
    max: spec.yMax,
    interval: spec.yStep,
    splitNumber: 4,
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: tk.muted, fontSize: 11.5, formatter: (v) => fmt.num(kilo ? v / 1000 : v, dec) },
    splitLine: { lineStyle: { color: tk.grid, width: 1, type: "solid" } },
  };
}

/** Unidade do eixo vertical, escrita no canto superior esquerdo do gráfico. */
function unitMark(tk, axis) {
  const text = axis.unitLabel;
  delete axis.unitLabel;
  if (!text) return [];
  return [{ type: "text", left: 2, top: 4, silent: true, style: { text, fill: tk.muted, font: "11.5px " + tk.font } }];
}

function extentOf(seriesList) {
  let m = 0;
  for (const s of seriesList) for (const v of s.values) if (v !== null && v !== undefined && Math.abs(v) > m) m = Math.abs(v);
  return m;
}

/** Amplitude que o eixo vertical vai mostrar, considerando limites fixos e base no zero. */
function spanOf(spec) {
  let lo = Infinity;
  let hi = -Infinity;
  for (const s of spec.series) {
    for (const v of s.values) {
      if (v === null || v === undefined) continue;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  }
  if (!Number.isFinite(lo)) return 0;
  if (spec.zeroBase || !spec.scale) {
    lo = Math.min(lo, 0);
    hi = Math.max(hi, 0);
  }
  if (typeof spec.yMin === "number") lo = spec.yMin;
  if (typeof spec.yMax === "number") hi = spec.yMax;
  return hi - lo;
}

// ------------------------------------------------------------------ linhas no tempo
/**
 * spec: { t: [epoch], from, to, res, unit, dec, scale, yMin, yMax,
 *         series: [{ name, color, values, min, max, area }],
 *         band: { from, to, label }, limits: [{ y, label }], endLabels: bool }
 */
export function lineOption(spec) {
  const tk = tokens();
  const opt = base(tk);
  const xs = spec.t.map(fmt.chartTime);
  const step = spec.res || median(diffs(spec.t)) || 30;
  const gap = Math.max(step * 2.6, 90);
  const decal = decalOn();
  const series = [];
  const names = [];

  const build = (values) => {
    const data = [];
    for (let i = 0; i < xs.length; i++) {
      if (i > 0 && spec.t[i] - spec.t[i - 1] > gap) data.push([xs[i - 1] + 1000, null]);   // quebra a linha onde faltam dados
      data.push([xs[i], values[i] === undefined ? null : values[i]]);
    }
    return data;
  };
  // Um ponto sem vizinhos (primeiras leituras, ou leitura solta entre duas lacunas) não forma linha:
  // ganha um marcador para não sumir do gráfico.
  const isolated = (values) => {
    const out = [];
    const has = (i) => i >= 0 && i < xs.length && values[i] !== null && values[i] !== undefined;
    for (let i = 0; i < xs.length; i++) {
      if (!has(i)) continue;
      const left = has(i - 1) && spec.t[i] - spec.t[i - 1] <= gap;
      const right = has(i + 1) && spec.t[i + 1] - spec.t[i] <= gap;
      if (!left && !right) out.push([xs[i], values[i]]);
    }
    return out;
  };

  spec.series.forEach((s, idx) => {
    names.push(s.name);
    if (s.min && s.max && spec.series.length === 1) {
      // faixa mín.–máx. do intervalo, como um véu claro atrás da linha
      const lo = build(s.min);
      const hi = build(s.max.map((v, i) => (v === null || s.min[i] === null ? null : v - s.min[i])));
      const hidden = { type: "line", showSymbol: false, silent: true, lineStyle: { opacity: 0 }, stack: "faixa" + idx, tooltip: { show: false }, z: 1 };
      series.push(Object.assign({ name: "__lo" + idx, data: lo }, hidden));
      series.push(Object.assign({ name: "__hi" + idx, data: hi, areaStyle: { color: s.color, opacity: 0.13 } }, hidden));
    }
    const item = {
      type: "line", name: s.name, data: build(s.values), showSymbol: false, symbol: "circle", symbolSize: 8,
      connectNulls: false, z: 3, sampling: "lttb",
      lineStyle: { width: 2, color: s.color, join: "round", cap: "round", type: decal ? DASHES[idx % 3] : "solid" },
      itemStyle: { color: s.color, borderColor: tk.surface, borderWidth: 2 },
      emphasis: { focus: "none", lineStyle: { width: 2 } },
    };
    if (s.area) item.areaStyle = { color: s.color, opacity: 0.1 };
    if (idx === 0) {
      if (spec.band) {
        item.markArea = { silent: true, itemStyle: { color: tk.neutral, opacity: 0.16 },
          label: { show: !!spec.band.label, position: "insideTopLeft", color: tk.muted, fontSize: 11, formatter: spec.band.label || "" },
          data: [[{ yAxis: spec.band.from }, { yAxis: spec.band.to }]] };
      }
      if (spec.limits && spec.limits.length) {
        item.markLine = { silent: true, symbol: "none", animation: false,
          lineStyle: { color: tk.muted, width: 1, type: "solid", opacity: 0.7 },
          label: { color: tk.muted, fontSize: 11, position: spec.limitLabel || "insideEndTop", formatter: (p) => p.name },
          data: spec.limits.map((l) => ({ yAxis: l.y, name: l.label })) };
      }
    }
    series.push(item);
    const iso = isolated(s.values);
    if (iso.length) {
      series.push({ type: "scatter", name: "__iso" + idx, data: iso, symbol: "circle", symbolSize: 7, silent: true, z: 4,
        itemStyle: { color: s.color, borderColor: tk.surface, borderWidth: 1.5 }, tooltip: { show: false } });
    }
  });

  const extent = extentOf(spec.series);
  const y = valueAxis(tk, spec, extent, spanOf(spec));
  // Em períodos curtos, a virada do mês aparece como dia ("01/10"), igual aos demais rótulos.
  const days = ((spec.to || spec.t[spec.t.length - 1] || 0) - (spec.from || spec.t[0] || 0)) / 86400;
  opt.xAxis = {
    type: "time",
    min: spec.from ? fmt.chartTime(spec.from) : undefined,
    max: spec.to ? fmt.chartTime(Math.min(spec.to, Date.now() / 1000)) : undefined,
    axisLine: { lineStyle: { color: tk.axis } },
    axisTick: { show: false },
    splitLine: { show: false },
    // o fundo transparente faz a folga lateral contar na hora de esconder rótulos encostados
    axisLabel: { color: tk.muted, fontSize: 11.5, hideOverlap: true, padding: [0, 6], backgroundColor: "transparent",
      formatter: { year: "{yyyy}", month: days <= 100 ? "{dd}/{MM}" : "{MM}/{yyyy}", day: "{dd}/{MM}", hour: "{HH}:{mm}", minute: "{HH}:{mm}", second: "{HH}:{mm}:{ss}" } },
  };
  opt.graphic = unitMark(tk, y);
  opt.yAxis = y;
  opt.legend = { show: false, data: names };
  opt.series = series;

  const longRange = spec.res >= 86400;
  opt.tooltip = tooltipBase(tk, {
    trigger: "axis",
    axisPointer: { type: "line", snap: true, lineStyle: { color: tk.muted, width: 1, type: "solid" }, label: { show: false } },
    formatter: (params) => {
      const rows = [];
      let x = null;
      for (const p of params) {
        if (p.seriesName.startsWith("__")) continue;
        x = p.value[0];
        const s = spec.series.find((q) => q.name === p.seriesName);
        rows.push({ color: s ? s.color : p.color, name: p.seriesName, value: fmt.withUnit(p.value[1], spec.unit, spec.dec) });
      }
      if (x === null) return "";
      let foot = null;
      if (spec.series.length === 1 && spec.series[0].min && params.length) {
        const s = spec.series[0];
        const i = nearest(xs, x);
        if (s.min[i] !== null && s.max[i] !== null && s.min[i] !== undefined) {
          foot = "mín. " + fmt.withUnit(s.min[i], spec.unit, spec.dec) + " · máx. " + fmt.withUnit(s.max[i], spec.unit, spec.dec);
        }
      }
      if (!foot && spec.res) foot = fmt.resLabel(spec.res);
      return tip(longRange ? fmt.wallDate(x) : fmt.wallDateTime(x), rows, foot);
    },
  });

  // rótulos diretos no fim das linhas, só quando as séries terminam afastadas umas das outras
  if (spec.endLabels && spec.series.length >= 2 && spec.series.length <= 4) {
    const last = spec.series.map((s) => lastValue(s.values)).filter((v) => v !== null);
    const all = [];
    for (const s of spec.series) for (const v of s.values) if (v !== null && v !== undefined) all.push(v);
    if (last.length === spec.series.length && all.length) {
      const lo = spec.zeroBase ? Math.min(0, Math.min(...all)) : Math.min(...all);
      const range = (Math.max(...all) - lo) || 1;
      const sorted = [...last].sort((a, b) => a - b);
      let minGap = Infinity;
      for (let i = 1; i < sorted.length; i++) minGap = Math.min(minGap, sorted[i] - sorted[i - 1]);
      if (minGap / range > 0.075) {
        opt.grid.right = 64;
        for (const s of series) {
          if (s.name.startsWith("__")) continue;
          const label = s.name.length > 9 ? s.name.slice(0, 8) + "…" : s.name;
          s.endLabel = { show: true, formatter: () => label, color: tk.text2, fontSize: 11.5, distance: 6 };
        }
      }
    }
  }
  return opt;
}

function lastValue(values) {
  for (let i = values.length - 1; i >= 0; i--) if (values[i] !== null && values[i] !== undefined) return values[i];
  return null;
}
function diffs(t) {
  const out = [];
  for (let i = 1; i < t.length; i++) out.push(t[i] - t[i - 1]);
  return out;
}
function median(a) {
  if (!a.length) return 0;
  const s = [...a].sort((x, y) => x - y);
  return s[Math.floor(s.length / 2)];
}
function nearest(xs, x) {
  let lo = 0;
  let hi = xs.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (xs[mid] < x) lo = mid + 1;
    else hi = mid;
  }
  if (lo > 0 && Math.abs(xs[lo - 1] - x) < Math.abs(xs[lo] - x)) return lo - 1;
  return lo;
}

// ------------------------------------------------------------------ barras por categoria
/**
 * spec: { cats: [rótulo curto], full: [rótulo completo], unit, dec,
 *         series: [{ name, color, values, sign: 1 | -1 }], stacked: bool,
 *         est: [bool], foot: (i) => texto, everyTick: n }
 */
export function barOption(spec) {
  const tk = tokens();
  const opt = base(tk);
  const n = spec.cats.length;
  const many = spec.series.length > 1;
  // ponta arredondada só no segmento mais externo de cada barra
  const outer = { pos: new Array(n).fill(-1), neg: new Array(n).fill(-1) };
  spec.series.forEach((s, si) => {
    for (let i = 0; i < n; i++) {
      const v = s.values[i];
      if (v === null || v === undefined || v === 0) continue;
      if ((s.sign || 1) > 0) outer.pos[i] = si;
      else outer.neg[i] = si;
    }
  });
  const series = spec.series.map((s, si) => ({
    type: "bar", name: s.name, stack: spec.stacked === false ? undefined : "pilha", barMaxWidth: 24, barMinWidth: 2,
    barCategoryGap: "26%", barGap: "16%",
    itemStyle: { color: s.color, borderColor: tk.surface, borderWidth: many && spec.stacked !== false ? 1 : 0 },
    emphasis: { focus: "none", itemStyle: { color: s.color, opacity: 0.82 } },
    data: s.values.map((v, i) => {
      if (v === null || v === undefined) return { value: null };
      const sign = s.sign || 1;
      const item = { value: sign * v, itemStyle: {} };
      if (sign > 0 && outer.pos[i] === si) item.itemStyle.borderRadius = [4, 4, 0, 0];
      if (sign < 0 && outer.neg[i] === si) item.itemStyle.borderRadius = [0, 0, 4, 4];
      if (spec.est && spec.est[i]) item.itemStyle.opacity = 0.55;
      return item;
    }),
  }));
  let extent = 0;
  for (let i = 0; i < n; i++) {
    let pos = 0;
    let neg = 0;
    for (const s of spec.series) {
      const v = s.values[i] || 0;
      if ((s.sign || 1) > 0) pos += v;
      else neg += v;
    }
    extent = Math.max(extent, pos, neg);
  }
  opt.xAxis = {
    type: "category", data: spec.cats,
    axisLine: { lineStyle: { color: tk.axis } }, axisTick: { show: false },
    axisLabel: { color: tk.muted, fontSize: 11.5, hideOverlap: true, interval: spec.everyTick ? spec.everyTick - 1 : "auto" },
  };
  opt.yAxis = valueAxis(tk, spec, extent);
  opt.graphic = unitMark(tk, opt.yAxis);
  opt.legend = { show: false, data: spec.series.map((s) => s.name) };
  opt.series = series;
  opt.tooltip = tooltipBase(tk, {
    trigger: "axis",
    axisPointer: { type: "shadow", shadowStyle: { color: tk.text, opacity: 0.06 } },
    formatter: (params) => {
      if (!params.length) return "";
      const i = params[0].dataIndex;
      const rows = [];
      for (const s of spec.series) {
        const v = s.values[i];
        if (v === null || v === undefined) continue;
        if (many && !v && spec.hideZero) continue;
        rows.push({ color: s.color, kind: "rect", name: s.name, value: fmt.withUnit(v, spec.unit, spec.dec) });
      }
      if (!rows.length) rows.push({ name: "sem dados", value: "—" });
      let foot = spec.foot ? spec.foot(i) : null;
      if (spec.est && spec.est[i]) foot = (foot ? foot + " · " : "") + "inclui trecho estimado";
      return tip(spec.full ? spec.full[i] : spec.cats[i], rows, foot);
    },
  });
  return opt;
}

// ------------------------------------------------------------------ mapa de calor (magnitude: um só matiz)
/** spec: { x: [rótulos], y: [rótulos], cells: [[xi, yi, valor]], unit, dec, head: (xi, yi) => texto, max } */
export function heatOption(spec) {
  const tk = tokens();
  const opt = base(tk);
  let max = spec.max || 0;
  if (!spec.max) for (const c of spec.cells) if (c[2] !== null && c[2] > max) max = c[2];
  const min = Math.min(spec.min || 0, max);
  opt.grid = { left: 8, right: 8, top: 6, bottom: 2, containLabel: true };
  opt.xAxis = {
    type: "category", data: spec.x, axisLine: { show: false }, axisTick: { show: false }, splitArea: { show: false },
    axisLabel: { color: tk.muted, fontSize: 11, hideOverlap: true, interval: spec.xEvery ? spec.xEvery - 1 : "auto" },
  };
  opt.yAxis = {
    type: "category", data: spec.y, inverse: true, axisLine: { show: false }, axisTick: { show: false }, splitArea: { show: false },
    axisLabel: { color: tk.muted, fontSize: 11, hideOverlap: true, interval: spec.yEvery ? spec.yEvery - 1 : "auto" },
  };
  opt.visualMap = { show: false, type: "continuous", min, max: max > min ? max : min + 1, dimension: 2, inRange: { color: tk.seq } };
  opt.series = [{
    type: "heatmap", data: spec.cells.filter((c) => c[2] !== null && c[2] !== undefined), progressive: 0,
    itemStyle: { borderColor: tk.surface, borderWidth: spec.x.length > 40 ? 1 : 2, borderRadius: 2 },
    emphasis: { itemStyle: { borderColor: tk.text, borderWidth: 1 } },
  }];
  opt.tooltip = tooltipBase(tk, {
    trigger: "item",
    formatter: (p) => tip(spec.head(p.value[0], p.value[1]), [{ name: spec.name || "Consumo", value: fmt.withUnit(p.value[2], spec.unit, spec.dec) }]),
  });
  opt.aria = { enabled: true };
  return { option: opt, min, max };
}

// ------------------------------------------------------------------ mini-gráfico de tendência (SVG)
/** Linha discreta em tom neutro; o ponto mais recente leva a cor de destaque. */
export function sparkline(values, color, w = 96, hgt = 30) {
  const pts = values.map((v, i) => [i, v]).filter((p) => p[1] !== null && p[1] !== undefined);
  const svg = h("svg", { class: "spark", viewBox: "0 0 " + w + " " + hgt, "aria-hidden": "true",
    style: { width: w + "px", height: hgt + "px" } });
  if (pts.length < 2) return svg;
  let lo = Infinity;
  let hi = -Infinity;
  for (const p of pts) {
    lo = Math.min(lo, p[1]);
    hi = Math.max(hi, p[1]);
  }
  if (lo > 0 && lo < hi * 0.6) lo = 0;
  const span = hi - lo || 1;
  const n = values.length - 1 || 1;
  const xy = (p) => [3 + (p[0] / n) * (w - 8), 4 + (1 - (p[1] - lo) / span) * (hgt - 8)];
  const d = pts.map((p, i) => (i ? "L" : "M") + xy(p).map((q) => q.toFixed(1)).join(" ")).join(" ");
  svg.appendChild(h("path", { d, fill: "none", stroke: cssVar("--neutral-mark"), "stroke-width": "1.75", "stroke-linejoin": "round", "stroke-linecap": "round" }));
  const end = xy(pts[pts.length - 1]);
  svg.appendChild(h("circle", { cx: end[0].toFixed(1), cy: end[1].toFixed(1), r: 3.5, fill: color || cssVar("--s1"), stroke: cssVar("--surface"), "stroke-width": 2 }));
  return svg;
}

// ------------------------------------------------------------------ cartão de gráfico (título, legenda, tabela)
export class ChartCard {
  constructor({ title, sub, height = 250, cls = "", tools = true }) {
    this.height = height;
    this.hidden = new Set();
    this.tableMode = false;
    this.tableFn = null;
    this.chart = null;
    this.titleEl = h("h2", { class: "card-title" }, title);
    this.subEl = h("div", { class: "card-sub" }, sub || "");
    this.legendEl = h("div", { class: "legend", style: { marginBottom: "6px" } });
    this.plot = h("div", { class: "plot", style: { height: height + "px" } });
    this.tableEl = h("div", { hidden: true });
    this.footEl = h("div", { class: "card-foot", hidden: true });
    this.tableBtn = h("button", { class: "icon-btn", type: "button", "aria-pressed": "false", title: "Ver como tabela",
      "aria-label": "Alternar entre gráfico e tabela", onclick: () => this.toggleTable() }, icon("table"));
    this.el = h("section", { class: "card " + cls },
      h("div", { class: "card-head" }, h("div", null, this.titleEl, this.subEl),
        tools ? h("div", { class: "card-tools" }, this.tableBtn) : null),
      this.legendEl, this.plot, this.tableEl, this.footEl);
    this.emptyEl = null;
  }

  setTitle(text) { this.titleEl.textContent = text; }
  setSub(text) { this.subEl.textContent = text || ""; }
  setFoot(...children) {
    clear(this.footEl);
    const has = children.some((c) => c !== null && c !== undefined && c !== false && c !== "");
    this.footEl.hidden = !has;
    if (has) for (const c of children) if (c) this.footEl.append(c);
  }
  /** Mantém o desenho anterior esmaecido enquanto os dados novos não chegam. */
  loading(on) { this.plot.classList.toggle("is-loading", !!on); }

  empty(message) {
    if (this.emptyEl) this.emptyEl.remove();
    this.emptyEl = null;
    if (message) {
      if (this.chart) this.chart.clear();
      clear(this.legendEl);
      this.emptyEl = h("div", { class: "plot-empty" }, message);
      this.plot.appendChild(this.emptyEl);
      this.tableFn = null;
      if (this.tableMode) this.toggleTable();
    }
  }

  /** legend: [{ name, color, kind: "line" | "rect" }] · table: () => ({ cols: [...], rows: [[...]] }) */
  render(option, { legend = [], table = null } = {}) {
    this.empty(null);
    if (!this.chart) {
      this.chart = E.init(this.plot, null, { renderer: "canvas" });
      this.ro = new ResizeObserver(() => this.chart && this.chart.resize());
      this.ro.observe(this.plot);
      registry.add(this);
    }
    if (option.legend && this.hidden.size) {
      option.legend.selected = {};
      for (const name of this.hidden) option.legend.selected[name] = false;
    }
    this.chart.setOption(option, { notMerge: true });
    this.loading(false);
    this.tableFn = table;
    this.tableBtn.hidden = !table;
    clear(this.legendEl);
    if (legend.length >= 2) {
      for (const item of legend) {
        const off = this.hidden.has(item.name);
        const btn = h("button", { type: "button", "aria-pressed": off ? "false" : "true", title: "Mostrar ou ocultar esta série" },
          h("i", { class: "key " + (item.kind || "line"), style: { background: item.color } }), h("span", null, item.name));
        btn.addEventListener("click", () => {
          if (this.hidden.has(item.name)) this.hidden.delete(item.name);
          else this.hidden.add(item.name);
          btn.setAttribute("aria-pressed", this.hidden.has(item.name) ? "false" : "true");
          this.chart.dispatchAction({ type: "legendToggleSelect", name: item.name });
        });
        this.legendEl.appendChild(btn);
      }
    }
    if (this.tableMode) this.buildTable();
  }

  toggleTable() {
    this.tableMode = !this.tableMode && !!this.tableFn;
    this.tableBtn.setAttribute("aria-pressed", this.tableMode ? "true" : "false");
    this.tableBtn.title = this.tableMode ? "Ver como gráfico" : "Ver como tabela";
    this.plot.style.display = this.tableMode ? "none" : "";
    this.legendEl.style.display = this.tableMode ? "none" : "";
    this.tableEl.hidden = !this.tableMode;
    if (this.tableMode) this.buildTable();
    else if (this.chart) this.chart.resize();
  }

  buildTable() {
    clear(this.tableEl);
    if (!this.tableFn) return;
    const { cols, rows } = this.tableFn();
    this.tableEl.appendChild(dataTable(cols, rows, this.height));
  }

  dispose() {
    if (this.ro) this.ro.disconnect();
    if (this.chart) this.chart.dispose();
    this.chart = null;
    registry.delete(this);
  }
}

const MAX_ROWS = 500;
export function dataTable(cols, rows, maxHeight) {
  const shown = rows.length > MAX_ROWS ? rows.slice(rows.length - MAX_ROWS) : rows;
  const table = h("table", { class: "data" },
    h("thead", null, h("tr", null, cols.map((c) => h("th", { scope: "col" }, c)))),
    h("tbody", null, shown.map((r) => h("tr", null, r.map((c) => h("td", null, c === null || c === undefined ? "—" : c))))));
  const wrap = h("div", { class: "table-wrap", style: maxHeight ? { maxHeight: Math.max(maxHeight, 200) + "px" } : null, tabindex: "0", role: "region", "aria-label": "Dados do gráfico em tabela" }, table);
  if (rows.length > MAX_ROWS) {
    return h("div", null, wrap, h("div", { class: "note", style: { marginTop: "6px" } },
      "Mostrando as " + MAX_ROWS + " linhas mais recentes de " + rows.length + ". Use a exportação CSV (aba Dados) para o período completo."));
  }
  return wrap;
}

/** Tabela a partir de uma série temporal: uma linha por instante, uma coluna por série. */
export function seriesTable(spec) {
  return () => ({
    cols: ["Data e hora"].concat(spec.series.map((s) => s.name + (spec.unit ? " (" + spec.unit + ")" : ""))),
    rows: spec.t.map((ts, i) => [fmt.dateTime(ts)].concat(spec.series.map((s) => fmt.num(s.values[i], spec.dec === undefined ? 1 : spec.dec)))),
  });
}

export function scaleLegend(min, max, unit, dec) {
  const tk = tokens();
  return h("div", { class: "scale" }, h("span", null, min ? fmt.num(min, dec) : "0"),
    h("i", { style: { background: "linear-gradient(90deg," + tk.seq.join(",") + ")" } }),
    h("span", null, fmt.withUnit(max, unit, dec)));
}

export function disposeAll() {
  for (const c of [...registry]) c.dispose();
}

/** Sincroniza o cursor (crosshair) entre gráficos de mesmo eixo de tempo. */
export function link(cards) {
  const charts = cards.map((c) => c.chart).filter(Boolean);
  if (charts.length > 1) E.connect(charts);
}
