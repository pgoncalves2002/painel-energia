// Fases: comparação entre as fases agora (diagrama fasorial, tabela) e ao longo do tempo.

import * as api from "../api.js";
import * as fmt from "../fmt.js";
import { h, set, status } from "../dom.js";
import { ChartCard, lineOption, seriesTable, tokens, link } from "../charts.js";
import { rangeSeg, rangeSeconds, stored, store, shareBar, sectionTitle, phasor, phasorLegend, phaseSequence,
  PHASES, PHASE_KEYS, phaseColorVar, voltageState, niceBounds } from "../ui.js";
import { scope } from "../view.js";

const ROWS = [
  ["Tensão", "u", "V", 1, null],
  ["Corrente", "i", "A", 2, "itrms"],
  ["Potência ativa", "p", "W", 0, "pt"],
  ["Potência reativa", "q", "VAr", 0, "qt"],
  ["Potência aparente", "s", "VA", 0, "st"],
  ["Fator de potência", "pf", "", 2, "pft"],
  ["Ângulo tensão-corrente", "ang", "°", 1, null],
];

export default function mount(root, app) {
  const sc = scope();
  const dev = app.device;
  const nPh = app.phases();
  const phases = PHASES.slice(0, nPh);
  const labels = app.labels();
  // medidor recém-ligado: começa mostrando a última hora, onde os primeiros pontos aparecem
  const born = app.info() && app.info().first_seen;
  let range = stored("fases.periodo", born && Date.now() / 1000 - born < 3 * 3600 ? "1h" : "24h", ["1h", "6h", "24h", "7d", "30d"]);

  const phasorCard = h("section", { class: "card c4" });
  const tableCard = h("section", { class: "card c8" });
  const hints = h("div", { style: { display: "flex", flexDirection: "column", gap: "10px" } });
  const per = nPh === 3 ? " por fase" : "";
  const charts = [
    { key: "p", name: "Potência ativa", card: new ChartCard({ title: "Potência ativa" + per, height: 240, cls: "c12" }), unit: "W", dec: 0, zero: true, end: true },
    { key: "i", name: "Corrente", card: new ChartCard({ title: "Corrente" + per, height: 220, cls: "c6" }), unit: "A", dec: 2, zero: true },
    { key: "u", name: "Tensão", card: new ChartCard({ title: "Tensão" + per, height: 220, cls: "c6" }), unit: "V", dec: 1, scale: true },
    { key: "pf", name: "Fator de potência", card: new ChartCard({ title: "Fator de potência" + per, height: 220, cls: "c6" }), unit: "", dec: 2, scale: true, abs: true },
    { key: "q", name: "Potência reativa", card: new ChartCard({ title: "Potência reativa" + per, height: 220, cls: "c6" }), unit: "VAr", dec: 0, zero: true },
  ];
  const rangeCtl = rangeSeg(["1h", "6h", "24h", "7d", "30d"], range, (v) => {
    range = v;
    store("fases.periodo", v);
    load(true);
  });

  root.append(
    h("div", { class: "grid" }, nPh === 3 ? phasorCard : null, tableCard),
    hints,
    sectionTitle("Ao longo do tempo"),
    h("div", { class: "filters" }, rangeCtl),
    h("div", { class: "grid" }, charts.map((c) => c.card.el)),
  );
  if (nPh !== 3) tableCard.className = "card c12";

  function renderLive(live) {
    if (!live || !live.reading) {
      set(tableCard, h("p", { class: "muted" }, "Aguardando leituras…"));
      return;
    }
    const r = live.reading;
    const info = app.info();

    // ---- diagrama fasorial
    if (nPh === 3) {
      const seq = phaseSequence(r);
      set(phasorCard,
        h("div", { class: "card-head" }, h("div", null, h("h2", { class: "card-title" }, "Diagrama fasorial"),
          h("div", { class: "card-sub" }, "tensões e correntes, com os ângulos informados pelo medidor"))),
        phasor(r, labels, nPh), phasorLegend(),
        seq ? h("div", { class: "card-foot" }, status(seq.ok ? "good" : "warning", "Sequência de fases: " + seq.text)) : null);
    }

    // ---- tabela "agora"
    const head = h("tr", null, h("th", null, ""), phases.map((ph, i) => h("th", null,
      h("span", { style: { display: "inline-flex", alignItems: "center", gap: "6px" } },
        h("span", { class: "dot", style: { background: phaseColorVar(i), width: "8px", height: "8px" } }), nPh === 3 ? labels[ph] : "Medição"))),
      nPh === 3 ? h("th", null, "Total") : null);
    const body = ROWS.map(([name, key, unit, dec, totalKey]) => h("tr", null,
      h("td", null, name),
      phases.map((ph) => {
        let v = r[PHASE_KEYS[ph][key]];
        if (key === "pf" && v !== null && v !== undefined) v = Math.abs(v);
        return h("td", null, fmt.withUnit(v, unit, dec));
      }),
      nPh === 3 ? h("td", null, totalKey ? fmt.withUnit(key === "pf" && r[totalKey] !== null && r[totalKey] !== undefined ? Math.abs(r[totalKey]) : r[totalKey], unit, dec) : "—") : null));
    const voltRow = h("tr", null, h("td", null, "Situação da tensão"), phases.map((ph) => {
      const vs = voltageState(r[PHASE_KEYS[ph].u], live.limits, info && info.v_nom);
      return h("td", null, vs.text ? status(vs.level, vs.text) : "—");
    }), nPh === 3 ? h("td", null, "") : null);

    const extras = [];
    if (nPh === 3) {
      const cur = phases.map((ph) => r[PHASE_KEYS[ph].i] || 0);
      const mean = cur.reduce((a, b) => a + b, 0) / 3;
      const imb = mean > 0.05 ? (Math.max(...cur.map((c) => Math.abs(c - mean))) / mean) * 100 : null;
      extras.push(h("div", { style: { marginTop: "14px" } },
        h("div", { class: "card-sub", style: { marginBottom: "8px" } }, "Divisão da corrente entre as fases" +
          (imb === null ? "" : " · desequilíbrio de " + fmt.pct(imb) + " (maior desvio em relação à média)")),
        shareBar(phases.map((ph, i) => ({ label: labels[ph], value: cur[i], text: fmt.num(cur[i], 1) + " A", color: phaseColorVar(i) })))));
    }
    set(tableCard,
      h("div", { class: "card-head" }, h("div", null, h("h2", { class: "card-title" }, nPh === 3 ? "Agora, por fase" : "Agora"),
        h("div", { class: "card-sub" }, "leitura de " + fmt.timeSec(live.ts) + (info && info.v_nom ? " · rede de " + fmt.num(info.v_nom, 0) + " V (fase-neutro)" : "")))),
      h("div", { class: "scroll-x" }, h("table", { class: "plain" }, h("thead", null, head), h("tbody", null, body, voltRow))),
      extras);

    // ---- dicas de instalação
    const tips = [];
    const mode = live.mode || "consumo";
    phases.forEach((ph) => {
      const p = r[PHASE_KEYS[ph].p];
      const u = r[PHASE_KEYS[ph].u];
      if (info && info.v_nom && u !== null && u !== undefined && u < 0.3 * info.v_nom) {
        tips.push([status("neutral", ""), labels[ph] + " sem tensão no medidor. Se a instalação usa menos de três fases, isso é esperado; caso contrário, confira a ligação do terminal de tensão dessa fase."]);
      } else if (mode === "consumo" && p !== null && p !== undefined && p < -20) {
        tips.push([status("warning", ""), labels[ph] + " está com potência negativa (" + fmt.power(p).text + "). Sem geração solar, isso indica o TC dessa fase instalado ao contrário: inverta os fios do TC no medidor. Se você tem geração, escolha o modo de instalação em Sistema › Configurações."]);
      }
    });
    set(hints, tips.map(([ic, text]) => h("div", { class: "callout" }, ic, h("div", null, text))));
  }

  function load(dim) {
    const now = Math.floor(Date.now() / 1000);
    const from = now - rangeSeconds(range);
    const fields = [];
    for (const ph of phases) for (const c of charts) fields.push(PHASE_KEYS[ph][c.key]);
    if (dim) for (const c of charts) c.card.loading(true);
    sc.load("series", () => api.get("series", { device: dev, fields: fields.join(","), from, to: now + 1, points: 600 }), (d) => {
      const tk = tokens();
      const live = app.live;
      const note = fmt.resLabel(d.res);
      for (const c of charts) {
        if (!d.t.length) {
          c.card.empty("Sem leituras neste período.");
          continue;
        }
        const series = phases.map((ph, i) => {
          let values = d.series[PHASE_KEYS[ph][c.key]].avg;
          if (c.abs) values = values.map((v) => (v === null ? null : Math.abs(v)));
          return { name: nPh === 3 ? labels[ph] : c.name, color: tk.s[i], values };
        });
        const spec = { t: d.t, from, to: now, res: d.res, unit: c.unit, dec: c.dec, axisDec: c.axisDec, scale: !!c.scale,
          zeroBase: !!c.zero, endLabels: !!c.end, series };
        if (c.key === "u" && live && live.limits) {
          let lo = Infinity;
          let hi = -Infinity;
          for (const s of series) for (const v of s.values) if (v !== null && v > 20) {
            lo = Math.min(lo, v);
            hi = Math.max(hi, v);
          }
          spec.band = { from: live.limits.adeq_low, to: live.limits.adeq_high, label: "faixa adequada" };
          Object.assign(spec, niceBounds(Math.min(lo, live.limits.adeq_low), Math.max(hi, live.limits.adeq_high)));
        }
        if (c.key === "pf") {
          spec.yMax = 1;
          spec.limits = [{ y: 0.92, label: "" }];     // o rótulo fica no subtítulo: dentro do gráfico as linhas o cobrem
        }
        c.card.setSub(c.key === "pf" ? note + " · linha cinza: referência de 0,92" : note);
        c.card.render(lineOption(spec), { legend: series.map((s) => ({ name: s.name, color: s.color })), table: seriesTable(spec) });
      }
      link(charts.map((c) => c.card));
    }, () => {
      for (const c of charts) c.card.loading(false);
    });
  }

  sc.onCleanup(app.onLive(renderLive));
  renderLive(app.live);
  load(false);
  sc.every(60000, () => load(false));
  return () => sc.destroy();
}
