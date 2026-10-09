// Qualidade da energia: tensão por fase em relação às faixas de referência, frequência,
// fator de potência e ocorrências.

import * as api from "../api.js";
import * as fmt from "../fmt.js";
import { h, set, status } from "../dom.js";
import { ChartCard, lineOption, barOption, seriesTable, tokens } from "../charts.js";
import { tile, rangeSeg, rangeSeconds, stored, store, eventItem, PHASES, PHASE_KEYS, phaseColorVar, niceBounds, setTiles } from "../ui.js";
import { scope } from "../view.js";

export default function mount(root, app) {
  const sc = scope();
  const dev = app.device;
  const nPh = app.phases();
  const phases = PHASES.slice(0, nPh);
  const labels = app.labels();
  let range = stored("qualidade.periodo", "7d", ["24h", "7d", "30d"]);

  const tiles = h("div", { class: "tiles" });
  const table = h("section", { class: "card c12" });
  const volt = new ChartCard({ title: "Tensão por fase", height: 270, cls: "c12" });
  const hist = new ChartCard({ title: "Distribuição da tensão", height: 230, cls: "c6" });
  const freq = new ChartCard({ title: "Frequência", height: 230, cls: "c6" });
  const events = h("section", { class: "card c12" });
  const rangeCtl = rangeSeg(["24h", "7d", "30d"], range, (v) => {
    range = v;
    store("qualidade.periodo", v);
    load(true);
  });

  root.append(
    h("div", { class: "filters" }, rangeCtl),
    tiles,
    h("div", { class: "grid" }, table, volt.el, hist.el, freq.el, events),
    h("p", { class: "note" }, "As faixas de tensão seguem a referência do PRODIST (Módulo 8) para redes de baixa tensão. " +
      "A conformidade considera a média de cada janela de tempo, como na regra da distribuidora; as ocorrências consideram cada leitura, " +
      "por isso uma queda rápida pode aparecer na lista sem mudar a conformidade. " +
      "Os indicadores são estimativas feitas com as leituras deste medidor e não substituem a medição oficial da distribuidora."),
  );

  function renderQuality(q) {
    const lim = q.limits;
    const out = [];
    let nAll = 0;
    let nOk = 0;
    let lowest = null;
    let highest = null;
    for (const ph of phases) {
      const p = q.phases[ph];
      if (!p) continue;
      nAll += p.n - p.counts.ausente;
      nOk += p.counts.adequada;
      if (p.min && p.min.value > 20 && (!lowest || p.min.value < lowest.value)) lowest = Object.assign({ ph }, p.min);
      if (p.max && (!highest || p.max.value > highest.value)) highest = Object.assign({ ph }, p.max);
    }
    const name = (ph) => (nPh === 3 ? labels[ph] + " · " : "");
    if (lim && nAll) {
      const share = (nOk / nAll) * 100;
      out.push(tile({ label: "Tensão adequada", value: fmt.num(share, share > 99.9 || share < 10 ? 2 : 1), unit: "%",
        sub: "do tempo, entre " + fmt.num(lim.adeq_low, 0) + " e " + fmt.num(lim.adeq_high, 0) + " V · " + q.n_windows + " janelas de " + Math.round(q.window / 60) + " min" }));
    }
    if (lowest) out.push(tile({ label: "Menor tensão", value: fmt.num(lowest.value, 1), unit: "V", sub: name(lowest.ph) + fmt.dateTime(lowest.ts) }));
    if (highest) out.push(tile({ label: "Maior tensão", value: fmt.num(highest.value, 1), unit: "V", sub: name(highest.ph) + fmt.dateTime(highest.ts) }));
    if (q.imbalance) {
      out.push(tile({ label: "Desequilíbrio de tensão", value: fmt.num(q.imbalance.avg, 2), unit: "%",
        sub: "média · em 95% do tempo fica abaixo de " + fmt.num(q.imbalance.p95, 2) + "%",
        title: "Maior desvio de uma fase em relação à média das três, em porcentagem" }));
    }
    if (q.freq && q.freq.min && q.freq.max) {
      out.push(tile({ label: "Frequência", value: fmt.num(q.freq.avg, 2), unit: "Hz",
        sub: "de " + fmt.num(q.freq.min.value, 2) + " a " + fmt.num(q.freq.max.value, 2) + " Hz · " + fmt.pct(q.freq.within, 1) + " entre 59,9 e 60,1" }));
    }
    if (q.pf && q.pf.t) {
      out.push(tile({ label: "Fator de potência médio", value: fmt.num(q.pf.t.avg, 2),
        sub: "abaixo de 0,92 em " + fmt.pct(q.pf.t.below) + " do tempo",
        title: "Energia ativa dividida pela aparente no período. Em residências não há cobrança por fator de potência baixo." }));
    }
    setTiles(tiles, out);

    // ---- tabela por fase
    const rows = phases.map((ph, i) => {
      const p = q.phases[ph] || {};
      const valid = p.n ? p.n - p.counts.ausente : 0;
      const share = (k) => (valid ? fmt.pct((p.counts[k] / valid) * 100, 2) : "—");
      let situation = "—";
      if (p.drp !== null && p.drp !== undefined && valid) {
        situation = p.ok ? status("good", "dentro da referência") : status(p.drc > q.ref.drc ? "critical" : "warning", "fora da referência");
      } else if (p.n && !valid) {
        situation = status("neutral", "sem tensão");
      }
      return h("tr", null,
        h("td", null, h("span", { style: { display: "inline-flex", alignItems: "center", gap: "7px" } },
          h("span", { class: "dot", style: { background: phaseColorVar(i), width: "8px", height: "8px" } }), nPh === 3 ? labels[ph] : "Tensão")),
        h("td", null, fmt.num(p.avg, 1) + " V"),
        h("td", null, p.min ? fmt.num(p.min.value, 1) + " V" : "—"),
        h("td", null, p.max ? fmt.num(p.max.value, 1) + " V" : "—"),
        h("td", null, share("adequada")),
        h("td", null, share("precaria")),
        h("td", null, share("critica")),
        h("td", null, situation));
    });
    set(table,
      h("div", { class: "card-head" }, h("div", null, h("h2", { class: "card-title" }, "Conformidade da tensão"),
        h("div", { class: "card-sub" }, lim
          ? "adequada: " + fmt.num(lim.adeq_low, 0) + "–" + fmt.num(lim.adeq_high, 0) + " V · precária: até " + fmt.num(lim.crit_low, 0) + " ou " +
            fmt.num(lim.crit_high, 0) + " V · crítica: além disso · referência: precária em no máximo " + fmt.num(q.ref.drp, 0) + "% e crítica em " +
            fmt.num(q.ref.drc, 1) + "% das janelas"
          : "tensão nominal não identificada; defina em Sistema › Configurações"))),
      h("div", { class: "scroll-x" }, h("table", { class: "plain" },
        h("thead", null, h("tr", null, ["", "Média", "Mínima", "Máxima", "Adequada", "Precária", "Crítica", "Situação"].map((c) => h("th", null, c)))),
        h("tbody", null, rows))));

    // ---- distribuição (histograma por fase)
    const tk = tokens();
    const volts = new Set();
    for (const ph of phases) for (const [v] of (q.phases[ph] || { hist: [] }).hist) if (v > 20) volts.add(v);
    const cats = [...volts].sort((a, b) => a - b);
    if (!cats.length) {
      hist.empty("Sem leituras neste período.");
    } else {
      const series = phases.map((ph, i) => {
        const p = q.phases[ph];
        const map = new Map(p.hist);
        const valid = p.n - p.counts.ausente || 1;
        return { name: nPh === 3 ? labels[ph] : "Tensão", color: tk.s[i], values: cats.map((v) => ((map.get(v) || 0) / valid) * 100) };
      });
      hist.setSub("% do tempo em cada valor de tensão (arredondado para 1 V)");
      hist.render(barOption({ cats: cats.map((v) => String(v)), full: cats.map((v) => v + " V"), unit: "%", dec: 1, series, stacked: false, hideZero: false }), {
        legend: series.map((s) => ({ name: s.name, color: s.color, kind: "rect" })),
        table: () => ({ cols: ["Tensão (V)"].concat(series.map((s) => s.name + " (%)")), rows: cats.map((v, i) => [String(v)].concat(series.map((s) => fmt.num(s.values[i], 1)))) }),
      });
    }
  }

  function renderSeries(d, from, to, limits) {
    const tk = tokens();
    if (!d.t.length) {
      volt.empty("Sem leituras neste período.");
      freq.empty("Sem leituras neste período.");
      return;
    }
    const note = fmt.resLabel(d.res);
    const single = nPh === 1;
    const series = phases.map((ph, i) => {
      const s = d.series[PHASE_KEYS[ph].u];
      return { name: single ? "Tensão" : labels[ph], color: tk.s[i], values: s.avg, min: single ? s.min : undefined, max: single ? s.max : undefined };
    });
    const spec = { t: d.t, from, to, res: d.res, unit: "V", dec: 1, scale: true, series };
    if (limits) {
      let lo = Infinity;
      let hi = -Infinity;
      for (const ph of phases) {
        const s = d.series[PHASE_KEYS[ph].u];
        for (const v of (s.min || s.avg)) if (v !== null && v > 20) lo = Math.min(lo, v);
        for (const v of (s.max || s.avg)) if (v !== null) hi = Math.max(hi, v);
      }
      spec.band = { from: limits.adeq_low, to: limits.adeq_high, label: "faixa adequada" };
      spec.limits = [{ y: limits.crit_low, label: "crítica abaixo de " + fmt.num(limits.crit_low, 0) + " V" }, { y: limits.crit_high, label: "crítica acima de " + fmt.num(limits.crit_high, 0) + " V" }];
      Object.assign(spec, niceBounds(Math.min(lo, limits.crit_low), Math.max(hi, limits.crit_high)));
    }
    volt.setSub(note);
    volt.render(lineOption(spec), { legend: series.map((s) => ({ name: s.name, color: s.color })), table: seriesTable(spec) });

    const f = d.series.freq;
    const fSpec = { t: d.t, from, to, res: d.res, unit: "Hz", dec: 2, scale: true,
      series: [{ name: "Frequência", color: tk.s[0], values: f.avg, min: f.min, max: f.max }],
      band: { from: 59.9, to: 60.1, label: "59,9 a 60,1 Hz" }, yMin: 59.8, yMax: 60.2 };
    let fl = 59.8;
    let fh = 60.2;
    for (const v of (f.min || f.avg)) if (v !== null && v > 1) fl = Math.min(fl, v - 0.02);
    for (const v of (f.max || f.avg)) if (v !== null) fh = Math.max(fh, v + 0.02);
    fSpec.yMin = Math.floor(fl * 20) / 20;
    fSpec.yMax = Math.ceil(fh * 20) / 20;
    freq.setSub(note + (d.res ? " · o véu mostra mínimo e máximo" : ""));
    freq.render(lineOption(fSpec), { table: seriesTable(fSpec) });
  }

  function renderEvents(list) {
    set(events,
      h("div", { class: "card-head" }, h("div", null, h("h2", { class: "card-title" }, "Ocorrências"),
        h("div", { class: "card-sub" }, "tensão fora da faixa, falta de fase, medidor sem enviar e ajustes do contador"))),
      list.length ? h("div", { class: "events" }, list.map((ev) => eventItem(ev, labels)))
        : h("div", { class: "muted", style: { padding: "8px 0" } }, "Nenhuma ocorrência neste período."));
  }

  function load(dim) {
    const now = Math.floor(Date.now() / 1000);
    const from = now - rangeSeconds(range);
    const fields = phases.map((ph) => PHASE_KEYS[ph].u).concat(["freq"]);
    if (dim) for (const c of [volt, hist, freq]) c.loading(true);
    sc.load("quality", () => Promise.all([
      api.get("quality", { device: dev, from, to: now + 1 }),
      api.get("series", { device: dev, fields: fields.join(","), from, to: now + 1, points: 700 }),
      api.get("events", { device: dev, from, to: now + 1, limit: 60 }),
    ]), ([q, s, e]) => {
      renderQuality(q);
      renderSeries(s, from, now, q.limits);
      renderEvents(e.events);
    }, (err) => {
      for (const c of [volt, hist, freq]) c.loading(false);
      volt.empty("Não foi possível carregar: " + err.message);
    });
  }

  load(false);
  sc.every(120000, () => load(false));
  return () => sc.destroy();
}
