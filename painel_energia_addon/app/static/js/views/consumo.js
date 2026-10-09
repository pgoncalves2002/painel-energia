// Consumo: energia e custo por dia, semana, mês ou ano, com padrões de uso.

import * as api from "../api.js";
import * as fmt from "../fmt.js";
import { h, set, icon } from "../dom.js";
import { ChartCard, barOption, lineOption, heatOption, scaleLegend, tokens } from "../charts.js";
import { tile, delta, seg, stored, store, shareBar, barList, flowLabels, PHASES, phaseColorVar, setTiles } from "../ui.js";
import { scope } from "../view.js";

const PERIODS = [["day", "Dia"], ["week", "7 dias"], ["month", "Mês"], ["year", "Ano"]];
const GROUP_TITLE = { hour: "por hora", day: "por dia", month: "por mês" };
const PREV_NAME = { day: "o dia anterior", week: "os 7 dias anteriores", month: "o mês anterior", year: "o ano anterior" };
/** Rótulo (AAAA-MM-DD) do dia em andamento, quando o período mostrado inclui hoje. */
function fmt_today(p) {
  return p.is_current ? p.date : null;
}

const BANDS = [["Madrugada (0h–6h)", 0, 6], ["Manhã (6h–12h)", 6, 12], ["Tarde (12h–18h)", 12, 18], ["Noite (18h–24h)", 18, 24]];

export default function mount(root, app) {
  const sc = scope();
  const dev = app.device;
  const nPh = app.phases();
  const phases = PHASES.slice(0, nPh);
  const labels = app.labels();
  let period = stored("consumo.periodo", "day", PERIODS.map((p) => p[0]));
  let split = nPh === 3 ? stored("consumo.visao", "total", ["total", "fases"]) : "total";
  let anchor = null;      // data âncora (AAAA-MM-DD); null = hoje
  let data = null;
  let mode = "consumo";

  const prevBtn = h("button", { class: "icon-btn", type: "button", "aria-label": "Período anterior", onclick: () => go(data && data.period.prev_date) }, icon("left"));
  const nextBtn = h("button", { class: "icon-btn", type: "button", "aria-label": "Próximo período", onclick: () => go(data && data.period.next_date) }, icon("right"));
  const dateLabel = h("div", { class: "label" }, "…");
  const todayBtn = h("button", { class: "btn sm", type: "button", onclick: () => go(null) }, "Hoje");
  const periodCtl = seg(PERIODS, period, (v) => {
    period = v;
    store("consumo.periodo", v);
    load(true);
  }, "Período");
  const splitCtl = nPh === 3 ? seg([["total", "Total"], ["fases", "Por fase"]], split, (v) => {
    split = v;
    store("consumo.visao", v);
    if (data) render();
  }, "Detalhamento") : null;

  const tiles = h("div", { class: "tiles" });
  const main = new ChartCard({ title: "Consumo", height: 290, cls: "c12" });
  const second = new ChartCard({ title: "", height: 240, cls: "c12" });
  const side = h("div", { class: "grid" });

  root.append(
    h("div", { class: "filters" }, periodCtl, h("div", { class: "datenav" }, prevBtn, dateLabel, nextBtn), todayBtn, h("span", { class: "spacer" }), splitCtl),
    tiles,
    h("div", { class: "grid" }, main.el, second.el),
    side,
  );

  function go(date) {
    anchor = date || null;
    load(true);
  }

  function load(dim) {
    if (dim) {
      main.loading(true);
      second.loading(true);
    }
    sc.load("consumo", async () => {
      const [d, s] = await Promise.all([
        api.get("consumo", { device: dev, period, date: anchor }),
        api.get("summary", { device: dev }),
      ]);
      const p = d.period;
      let extra = null;
      if (period === "day") extra = await api.get("energy", { device: dev, group: "15min", from: p.from, to: p.to });
      else if (period === "year") extra = await api.get("energy", { device: dev, group: "day", from: p.from, to: p.to });
      else extra = await api.get("energy", { device: dev, group: "hour", from: p.from, to: p.to });
      return { d, s, extra };
    }, (res) => {
      data = res.d;
      data.extra = res.extra;
      mode = res.s.mode;
      render();
    }, (e) => {
      main.loading(false);
      second.loading(false);
      main.empty("Não foi possível carregar: " + e.message);
    });
  }

  function render() {
    const d = data;
    const p = d.period;
    const tk = tokens();
    const L = flowLabels(mode);
    const hasExport = mode !== "consumo" || d.totals.g_t > 0.05;
    const rows = d.rows;
    const T = d.totals;

    dateLabel.textContent = fmt.periodTitle(p);
    nextBtn.disabled = !p.has_next;
    todayBtn.hidden = p.is_current;

    // ---- indicadores do período
    const ref = p.is_current && d.prev_same_time ? d.prev_same_time : d.prev_totals;
    const hasRef = ref && ref.n > 0;
    const out = [];
    const total = fmt.energy(T.c_t);
    out.push(tile({ label: L.c, value: total.v, unit: total.u,
      deltaEl: hasRef ? delta(fmt.change(T.c_t, ref.c_t), p.is_current ? "vs. mesmo ponto d" + PREV_NAME[period] : "vs. " + PREV_NAME[period]) : null,
      sub: hasRef ? null : "sem dados d" + PREV_NAME[period] + " para comparar" }));
    out.push(tile({ label: "Custo estimado", value: fmt.money(T.c_t * d.tariff - T.g_t * d.credit),
      sub: "tarifa de " + fmt.money(d.tariff) + "/kWh" + (d.credit && hasExport ? " · crédito de " + fmt.money(d.credit) + "/kWh injetado" : "") }));
    if (hasExport) {
      const g = fmt.energy(T.g_t);
      const net = fmt.energy(T.c_t - T.g_t);
      out.push(tile({ label: L.g, value: g.v, unit: g.u }));
      out.push(tile({ label: "Saldo", value: net.v, unit: net.u,
        sub: "consumo menos " + L.g.toLowerCase() + (T.c_t - T.g_t < 0 ? " · negativo: sobrou energia" : "") }));
    }
    const withData = rows.filter((r) => r.n > 0);
    const effDays = rows.reduce((a, r) => a + r.n, 0) * 900 / 86400;
    if (period !== "day" && effDays >= 0.5) {
      const avg = fmt.energy(T.c_t / effDays);
      out.push(tile({ label: "Média por dia", value: avg.v, unit: avg.u, sub: fmt.money((T.c_t / effDays) * d.tariff) + " por dia · " + fmt.num(effDays, effDays < 10 ? 1 : 0) + " dias com dados" }));
    }
    if (withData.length) {
      const top = withData.reduce((a, r) => (r.c_t > a.c_t ? r : a));
      const e = fmt.energy(top.c_t);
      const what = { hour: "Hora de maior consumo", day: "Dia de maior consumo", month: "Mês de maior consumo" }[p.group];
      out.push(tile({ label: what, value: e.v, unit: e.u, sub: fmt.groupLabel(top.label, p.group) }));
    }
    setTiles(tiles, out);

    // ---- barras do período
    let series;
    if (split === "fases" && nPh === 3) {
      series = phases.map((ph, i) => ({ name: labels[ph], color: tk.s[i], values: rows.map((r) => r["c_" + ph]) }));
      if (hasExport) {
        phases.forEach((ph, i) => series.push({ name: labels[ph] + " · " + L.g.toLowerCase(), color: tk.s[i], values: rows.map((r) => r["g_" + ph]), sign: -1 }));
      }
    } else {
      series = [{ name: L.c, color: tk.s[0], values: rows.map((r) => r.c_t) }];
      if (hasExport) series.push({ name: L.g, color: tk.s[1], values: rows.map((r) => r.g_t), sign: -1 });
    }
    const spec = {
      cats: rows.map((r) => fmt.groupTick(r.label, p.group)), full: rows.map((r) => fmt.groupLabel(r.label, p.group)),
      unit: "kWh", dec: 2, series, est: rows.map((r) => r.est), hideZero: true,
      everyTick: p.group === "hour" ? 2 : undefined,
      foot: (i) => {
        const r = rows[i];
        if (r.c_t === null) return null;
        const parts = [];
        if (split === "fases" && nPh === 3) parts.push("total: " + fmt.energy(r.c_t).text);
        parts.push("custo: " + fmt.money(r.c_t * d.tariff - (r.g_t || 0) * d.credit));
        return parts.join(" · ");
      },
    };
    main.setTitle((hasExport ? "Energia " : "Consumo ") + GROUP_TITLE[p.group]);
    main.setSub("kWh" + (split === "fases" && hasExport ? " · acima do zero: consumo; abaixo: " + L.g.toLowerCase() : ""));
    const legend = split === "fases" && nPh === 3
      ? phases.map((ph, i) => ({ name: labels[ph], color: tk.s[i], kind: "rect" }))
      : series.map((s) => ({ name: s.name, color: s.color, kind: "rect" }));
    if (!withData.length) {
      main.empty("Sem dados neste período.");
    } else {
      main.render(barOption(spec), {
        legend: split === "fases" && hasExport ? [] : legend,
        table: () => ({
          cols: [{ hour: "Hora", day: "Dia", month: "Mês" }[p.group]].concat(series.map((s) => s.name + " (kWh)"), ["Custo (R$)"]),
          rows: rows.map((r, i) => (r.n ? [fmt.groupLabel(r.label, p.group)].concat(series.map((s) => fmt.num(s.values[i], 2)),
            [fmt.num(r.c_t * d.tariff - (r.g_t || 0) * d.credit, 2)]) : null)).filter(Boolean),
        }),
      });
      if (split === "fases" && hasExport) set(main.legendEl, legend.map((it) => h("span", { class: "item" }, h("i", { class: "key rect", style: { background: it.color } }), it.name)));
    }
    const notes = [];
    if (rows.some((r) => r.est)) notes.push("Barras mais claras incluem trechos estimados (o painel ficou sem receber leituras e repartiu a energia do contador pelo intervalo).");
    if (split === "fases" && hasExport) notes.push("Com geração, a soma das fases pode ser diferente do total: o total considera o saldo entre as fases a cada instante.");
    main.setFoot(notes.join(" "));

    renderSecond(d, p, tk, L);
    renderSide(d, p, hasExport);
  }

  function renderSecond(d, p, tk, L) {
    const ex = d.extra.rows;
    if (period === "day") {
      const pts = ex.filter((r) => r.n > 0 && r.end <= p.now + 900);
      second.plot.style.height = "240px";
      second.setTitle("Demanda a cada 15 minutos");
      second.setSub("potência média de cada intervalo de 15 min");
      second.setFoot(null);
      if (pts.length < 2) {
        second.empty("Sem dados suficientes neste dia.");
        return;
      }
      const spec = { t: pts.map((r) => r.start + 450), from: p.from, to: p.to, res: 900, unit: "W", dec: 0, zeroBase: true,
        series: [{ name: "Demanda", color: tk.s[0], values: pts.map((r) => r.c_t * 4000), area: true }] };
      second.render(lineOption(spec), {
        table: () => ({ cols: ["Intervalo", "Demanda (kW)", "Energia (kWh)"],
          rows: pts.map((r) => [fmt.time(r.start) + "–" + fmt.time(r.end), fmt.num(r.c_t * 4, 2), fmt.num(r.c_t, 3)]) }),
      });
      return;
    }
    if (period === "year") {
      // calendário: um quadrado por dia, meses nas linhas (só os meses que têm dados)
      let months = [];
      let cells = [];
      const index = new Map();
      for (const r of ex) {
        const key = r.label.slice(0, 7);
        if (!index.has(key)) {
          index.set(key, months.length);
          months.push(fmt.groupTick(key + "-01", "month"));
        }
        if (r.n > 0) cells.push([Number(r.label.slice(8, 10)) - 1, index.get(key), r.c_t, r.label]);
      }
      second.setTitle("Calendário de consumo");
      second.setSub("consumo de cada dia · quanto mais escuro, maior");
      if (!cells.length) {
        second.empty("Sem dados neste ano.");
        second.setFoot(null);
        return;
      }
      const first = Math.min(...cells.map((c) => c[1]));
      const last = Math.max(...cells.map((c) => c[1]));
      months = months.slice(first, last + 1);
      cells = cells.map((c) => [c[0], c[1] - first, c[2], c[3]]);
      second.plot.style.height = 44 + months.length * 24 + "px";
      const byCell = new Map(cells.map((c) => [c[0] + ":" + c[1], c[3]]));
      const low = Math.min(...cells.filter((c) => c[3] !== fmt_today(p)).map((c) => c[2]).concat([Infinity]));
      const { option, min, max } = heatOption({ x: Array.from({ length: 31 }, (_, i) => String(i + 1)), y: months, cells, unit: "kWh", dec: 2,
        min: Number.isFinite(low) ? Math.floor(low) : 0,
        head: (xi, yi) => fmt.groupLabel(byCell.get(xi + ":" + yi), "day") });
      second.render(option, { table: () => ({ cols: ["Dia", "Consumo (kWh)"], rows: cells.map((c) => [fmt.groupLabel(c[3], "day"), fmt.num(c[2], 2)]) }) });
      if (second.chart) second.chart.resize();
      second.setFoot(scaleLegend(min, max, "kWh", 1));
      return;
    }
    // semana ou mês: hora x dia (só os dias que têm dados)
    let days = [];
    let full = [];
    const index = new Map();
    let cells = [];
    for (const r of ex) {
      const key = r.label.slice(0, 10);
      if (!index.has(key)) {
        index.set(key, days.length);
        const pd = fmt.parseDate(key);
        days.push(fmt.weekdayName(pd.wd) + " " + String(pd.d).padStart(2, "0"));
        full.push(fmt.groupLabel(key, "day"));
      }
      if (r.n > 0) cells.push([Number(r.label.slice(11, 13)), index.get(key), r.c_t]);
    }
    second.setTitle("Quando o consumo acontece");
    second.setSub("consumo de cada hora (colunas) em cada dia (linhas) · quanto mais escuro, maior");
    if (!cells.length) {
      second.empty("Sem dados neste período.");
      second.setFoot(null);
      return;
    }
    const first = Math.min(...cells.map((c) => c[1]));
    const last = Math.max(...cells.map((c) => c[1]));
    days = days.slice(first, last + 1);
    full = full.slice(first, last + 1);
    cells = cells.map((c) => [c[0], c[1] - first, c[2]]);
    second.plot.style.height = 40 + days.length * (days.length > 10 ? 15 : 26) + "px";
    const hours = Array.from({ length: 24 }, (_, i) => String(i).padStart(2, "0") + "h");
    const { option, max } = heatOption({ x: hours, y: days, cells, unit: "kWh", dec: 2, xEvery: 3,
      head: (xi, yi) => full[yi] + " · " + hours[xi] + "–" + String((xi + 1) % 24).padStart(2, "0") + "h" });
    second.render(option, { table: () => ({ cols: ["Dia", "Hora", "Consumo (kWh)"], rows: cells.map((c) => [full[c[1]], hours[c[0]], fmt.num(c[2], 2)]) }) });
    if (second.chart) second.chart.resize();
    second.setFoot(scaleLegend(0, max, "kWh", 2));
  }

  function renderSide(d, p, hasExport) {
    const cards = [];
    const T = d.totals;
    if (nPh === 3 && T.c_t > 0) {
      const items = phases.map((ph, i) => ({ label: labels[ph], value: T["c_" + ph], text: fmt.energy(T["c_" + ph]).text, color: phaseColorVar(i) }));
      cards.push(h("section", { class: "card c4" }, h("div", { class: "card-head" }, h("div", null,
        h("h2", { class: "card-title" }, "Distribuição por fase"), h("div", { class: "card-sub" }, "quanto cada fase consumiu no período"))),
      shareBar(items)));
    }
    if (period === "week" || period === "month") {
      const sums = BANDS.map(() => 0);
      for (const r of d.extra.rows) {
        if (!r.n) continue;
        const hh = Number(r.label.slice(11, 13));
        BANDS.forEach((b, i) => {
          if (hh >= b[1] && hh < b[2]) sums[i] += r.c_t;
        });
      }
      const tot = sums.reduce((a, b) => a + b, 0);
      if (tot > 0) {
        cards.push(h("section", { class: "card c4" }, h("div", { class: "card-head" }, h("div", null,
          h("h2", { class: "card-title" }, "Por período do dia"), h("div", { class: "card-sub" }, "soma do consumo em cada faixa de horário"))),
        barList(BANDS.map((b, i) => ({ label: b[0], value: sums[i], text: fmt.energy(sums[i]).text, note: "(" + fmt.pct((sums[i] / tot) * 100) + ")" })))));
      }
    }
    if (period === "month" || period === "year") {
      const daily = period === "year" ? d.extra.rows : d.rows;
      const acc = Array.from({ length: 7 }, () => [0, 0]);
      for (const r of daily) {
        if (r.n < 90) continue;       // só dias completos entram na média
        const wd = fmt.parseDate(r.label).wd;
        acc[wd][0] += r.c_t;
        acc[wd][1] += 1;
      }
      const order = [1, 2, 3, 4, 5, 6, 0];
      if (acc.some((a) => a[1] > 0)) {
        cards.push(h("section", { class: "card c4" }, h("div", { class: "card-head" }, h("div", null,
          h("h2", { class: "card-title" }, "Por dia da semana"), h("div", { class: "card-sub" }, "consumo médio dos dias completos"))),
        barList(order.map((wd) => ({ label: fmt.weekdayName(wd), value: acc[wd][1] ? acc[wd][0] / acc[wd][1] : 0,
          text: acc[wd][1] ? fmt.energy(acc[wd][0] / acc[wd][1]).text : "—" })))));
      }
    }
    set(side, cards);
  }

  load(false);
  sc.every(60000, () => {
    if (data && data.period.is_current) load(false);
  });
  return () => sc.destroy();
}
