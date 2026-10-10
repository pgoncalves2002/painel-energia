// Visão geral: o que está acontecendo agora, o resumo do dia e do mês, e o histórico recente.

import * as api from "../api.js";
import * as fmt from "../fmt.js";
import { h, set, status } from "../dom.js";
import { ChartCard, lineOption, barOption, seriesTable, tokens, link } from "../charts.js";
import { tile, delta, seg, rangeSeg, rangeSeconds, stored, store, shareBar, sectionTitle, flowLabels,
  PHASES, PHASE_KEYS, phaseColorVar, voltageState, niceBounds, setTiles } from "../ui.js";
import { sparkline } from "../charts.js";
import { scope } from "../view.js";
import { FlowDiagram, flowNotes, solarFoot } from "../flow.js";

export default function mount(root, app) {
  const sc = scope();
  const dev = app.device;
  const nPh = app.phases();
  const phases = PHASES.slice(0, nPh);
  const labels = app.labels();
  const info0 = app.info();
  const fresh = info0 && info0.first_seen && Date.now() / 1000 - info0.first_seen < 3 * 3600;
  let range = stored("geral.periodo", fresh ? "1h" : "24h", ["1h", "6h", "24h", "7d"]);
  let split = nPh === 3 ? stored("geral.visao", "total", ["total", "fases"]) : "total";
  let summary = null;
  let mode = "consumo";
  let monthSpark = null;
  let phaseSpark = null;
  let flowView = null;

  const hero = h("section", { class: "card c5 hero" });
  const hourly = new ChartCard({ title: "Consumo de hoje, hora a hora", height: 200, cls: "c7" });
  const tiles = h("div", { class: "tiles" });
  const phaseRow = h("div", { class: "grid" });
  const power = new ChartCard({ title: "Potência ativa", height: 270, cls: "c12" });
  const volt = new ChartCard({ title: "Tensão", height: 224, cls: "c6" });
  const curr = new ChartCard({ title: "Corrente", height: 224, cls: "c6" });

  const rangeCtl = rangeSeg(["1h", "6h", "24h", "7d"], range, (v) => {
    range = v;
    store("geral.periodo", v);
    loadHistory(true);
  });
  const splitCtl = nPh === 3 ? seg([["total", "Total"], ["fases", "Por fase"]], split, (v) => {
    split = v;
    store("geral.visao", v);
    loadHistory(true);
    loadHourly();
  }, "Detalhamento") : null;

  root.append(
    h("div", { class: "grid" }, hero, hourly.el),
    tiles,
    phaseRow,
    sectionTitle("Histórico recente"),
    h("div", { class: "filters" }, rangeCtl, splitCtl),
    h("div", { class: "grid" }, power.el, volt.el, curr.el),
  );

  // ---------------------------------------------------------------- agora
  function renderHero(live) {
    if (!live || !live.reading || live.reading.pt === null || live.reading.pt === undefined) {
      set(hero, h("div", { class: "hero-label" }, "Potência agora"), h("div", { class: "muted" }, "Aguardando a primeira leitura do medidor…"));
      return;
    }
    const r = live.reading;
    const L = flowLabels(mode);
    const info = app.info();
    if (live.flow && live.flow.available) {
      renderFlowHero(live, r, info);
      return;
    }
    if (flowView) {
      flowView.destroy();
      flowView = null;
      hourly.el.classList.remove("beside-flow");
    }
    const exporting = mode !== "consumo" && r.pt < -5;
    const p = fmt.power(mode === "consumo" ? r.pt : Math.abs(r.pt));
    const label = mode === "consumo" ? "Potência agora" : (exporting ? L.gNow + " agora" : L.cNow + " agora");
    const offline = info && !info.online;
    // No modo "só consumo", potência total negativa pede uma explicação: é geração ou TC invertido.
    const reversed = mode === "consumo" && r.pt < -20;
    const items = phases.map((ph, i) => {
      const v = r[PHASE_KEYS[ph].p];
      return { label: labels[ph], value: Math.abs(v || 0), text: fmt.power(v).text, color: phaseColorVar(i) };
    });
    set(hero,
      h("div", { class: "hero-label" }, h("span", null, label),
        offline ? status("serious", "medidor sem enviar · última leitura " + fmt.ago(live.device.age)) : null),
      h("div", { class: "hero-top" },
        h("div", { class: "hero-value" }, h("span", null, p.v), h("span", { class: "hero-unit" }, p.u)),
        phaseSpark && phaseSpark.pt ? h("div", { title: "Potência total na última hora" }, sparkline(phaseSpark.pt, tokens().s[0], 168, 48)) : null),
      nPh === 3 ? shareBar(items) : null,
      reversed ? h("p", { class: "note", style: { margin: "10px 0 0" } },
        "Potência negativa: a energia está indo para a rede (geração) ou os TCs estão invertidos. Com geração, o painel passa a mostrar consumo e injeção em instantes.") : null,
      h("div", { class: "hero-meta" },
        h("span", null, "Corrente ", h("b", null, fmt.num(r.itrms, 1) + " A")),
        h("span", null, "Aparente ", h("b", null, r.st === null || r.st === undefined ? "—" : fmt.num(r.st / 1000, 2) + " kVA")),
        h("span", null, "Fator de potência ", h("b", null, fmt.num(r.pft === null || r.pft === undefined ? null : Math.abs(r.pft), 2))),
        h("span", null, "Frequência ", h("b", null, fmt.num(r.freq, 2) + " Hz"))));
  }

  // Com a geração do DTU: o fluxo solar → casa → rede no lugar do número grande.
  function renderFlowHero(live, r, info) {
    if (!flowView) {
      flowView = new FlowDiagram();
      hourly.el.classList.add("beside-flow");      // acompanha a altura do fluxo, ao lado dele
    }
    flowView.update(live.flow);
    const offline = info && !info.online;
    set(hero,
      h("div", { class: "hero-label" }, h("span", null, "Fluxo de energia agora"),
        offline ? status("serious", "medidor sem enviar · última leitura " + fmt.ago(live.device.age)) : null),
      flowView.el,
      flowNotes(live.flow),
      solarFoot(live.solar),
      h("div", { class: "hero-meta" },
        h("span", null, "Corrente ", h("b", null, fmt.num(r.itrms, 1) + " A")),
        h("span", null, "Fator de potência ", h("b", null, fmt.num(r.pft === null || r.pft === undefined ? null : Math.abs(r.pft), 2))),
        h("span", null, "Frequência ", h("b", null, fmt.num(r.freq, 2) + " Hz"))));
  }

  function renderPhases(live) {
    if (!live || !live.reading) return;
    const r = live.reading;
    const info = app.info();
    set(phaseRow, phases.map((ph, i) => {
      const k = PHASE_KEYS[ph];
      const vs = voltageState(r[k.u], live.limits, info && info.v_nom);
      const p = fmt.power(r[k.p]);
      const spark = phaseSpark && phaseSpark[k.p] ? sparkline(phaseSpark[k.p], tokens().s[i]) : null;
      return h("section", { class: "card phase " + (nPh === 3 ? "c4" : "c12") },
        h("div", { class: "phase-head" },
          h("div", { class: "phase-name" }, h("span", { class: "dot", style: { background: phaseColorVar(i) } }), h("span", null, nPh === 3 ? labels[ph] : "Medição")),
          vs.text ? status(vs.level, "tensão " + vs.text) : null),
        h("div", { class: "phase-main" },
          h("div", { class: "phase-power" }, p.v, h("small", null, p.u)),
          spark ? h("div", { title: "Potência na última hora" }, spark) : null),
        h("dl", { class: "kv" },
          h("div", null, h("dt", null, "Tensão"), h("dd", null, fmt.num(r[k.u], 1) + " V")),
          h("div", null, h("dt", null, "Corrente"), h("dd", null, fmt.num(r[k.i], 2) + " A")),
          h("div", null, h("dt", null, "Fator de pot."), h("dd", null, fmt.num(r[k.pf] === null || r[k.pf] === undefined ? null : Math.abs(r[k.pf]), 2)))));
    }));
  }

  function renderLive(live) {
    renderHero(live);
    renderPhases(live);
  }

  // ---------------------------------------------------------------- indicadores do dia e do mês
  function renderTiles() {
    const s = summary;
    if (!s) return;
    const L = flowLabels(s.mode);
    const hasExport = s.mode !== "consumo";
    const out = [];
    const today = fmt.energy(s.today.c_t);
    out.push(tile({
      label: L.c + " hoje", value: today.v, unit: today.u,
      deltaEl: delta(fmt.change(s.today.c_t, s.yesterday_same_time.c_t), "vs. ontem até esta hora"),
      sub: fmt.money(s.today.cost) + " · ontem: " + fmt.energy(s.yesterday.c_t).text,
    }));
    if (hasExport) {
      const g = fmt.energy(s.today.g_t);
      out.push(tile({ label: L.g + " hoje", value: g.v, unit: g.u,
        sub: "saldo do dia: " + fmt.energy(s.today.net).text + (s.today.net < 0 ? " (sobrou)" : "") }));
    }
    const month = fmt.energy(s.month.c_t);
    const partial = s.first_data && s.first_data > s.month_start;
    out.push(tile({
      label: L.c + " no mês", value: month.v, unit: month.u,
      deltaEl: partial ? null : delta(fmt.change(s.month.c_t, s.last_month_same_time.c_t), "vs. mesmo ponto do mês passado"),
      sub: fmt.money(s.month.cost) + (partial ? " · medindo desde " + fmt.dateShort(s.first_data) : ""),
      spark: monthSpark, sparkColor: tokens().s[0],
      title: "Tendência: consumo diário dos últimos 30 dias",
    }));
    if (s.projection) {
      const pr = fmt.energy(s.projection.c_t);
      out.push(tile({ label: "Projeção para o mês", value: pr.v, unit: pr.u,
        sub: fmt.money(s.projection.cost) + " · média de " + fmt.num(s.projection.daily_avg, 1) + " kWh/dia" }));
    }
    if (s.demand_today) {
      out.push(tile({ label: "Maior demanda hoje", value: fmt.num(s.demand_today.kw, 2), unit: "kW",
        sub: "média de 15 min, às " + fmt.time(s.demand_today.ts) +
          (s.demand_month ? " · no mês: " + fmt.num(s.demand_month.kw, 2) + " kW" : "") }));
    }
    if (s.base_load) {
      const b = fmt.power(s.base_load.w);
      out.push(tile({ label: "Carga de base", value: b.v, unit: b.u,
        sub: "sempre ligada (madrugada) · ≈ " + fmt.num(s.base_load.kwh_month, 0) + " kWh/mês, " + fmt.money(s.base_load.cost_month),
        title: "Mediana da potência entre 1h e 5h nos últimos 7 dias" }));
    }
    setTiles(tiles, out);
  }

  function loadSummary() {
    sc.load("summary", () => api.get("summary", { device: dev }), (s) => {
      const changed = mode !== s.mode;
      summary = s;
      mode = s.mode;
      renderTiles();
      if (changed) {
        renderLive(app.live);
        loadHourly();
      }
    });
  }

  function loadMonthSpark() {
    const now = Math.floor(Date.now() / 1000);
    sc.load("mspark", () => api.get("energy", { device: dev, group: "day", from: now - 30 * 86400, to: now }), (d) => {
      monthSpark = d.rows.map((r) => r.c_t);
      renderTiles();
    });
  }

  function loadPhaseSpark() {
    const now = Math.floor(Date.now() / 1000);
    sc.load("pspark", () => api.get("series", { device: dev, fields: "pt,pa,pb,pc", from: now - 3600, to: now + 1, points: 60 }), (d) => {
      phaseSpark = {};
      for (const k of Object.keys(d.series)) phaseSpark[k] = d.series[k].avg || [];
      renderLive(app.live);
    });
  }

  // ---------------------------------------------------------------- consumo de hoje por hora
  function loadHourly() {
    sc.load("hourly", () => api.get("consumo", { device: dev, period: "day" }), (d) => {
      const tk = tokens();
      const L = flowLabels(mode);
      const rows = d.rows;
      const hasExport = mode !== "consumo";
      let series;
      let legend;
      if (split === "fases" && nPh === 3) {
        series = phases.map((ph, i) => ({ name: labels[ph], color: tk.s[i], values: rows.map((r) => r["c_" + ph]) }));
        legend = series.map((s) => ({ name: s.name, color: s.color, kind: "rect" }));
      } else {
        series = [{ name: L.c, color: tk.s[0], values: rows.map((r) => r.c_t) }];
        if (hasExport) series.push({ name: L.g, color: tk.s[1], values: rows.map((r) => r.g_t), sign: -1 });
        legend = series.map((s) => ({ name: s.name, color: s.color, kind: "rect" }));
      }
      const spec = {
        cats: rows.map((r) => fmt.groupTick(r.label, "hour")), full: rows.map((r) => fmt.groupLabel(r.label, "hour")),
        unit: "kWh", dec: 2, series, est: rows.map((r) => r.est), everyTick: 3, hideZero: false,
        foot: (i) => (rows[i].c_t === null ? null : "custo: " + fmt.money(rows[i].c_t * d.tariff - (rows[i].g_t || 0) * d.credit)),
      };
      hourly.setTitle(hasExport ? "Energia de hoje, hora a hora" : "Consumo de hoje, hora a hora");
      hourly.setSub(hasExport
        ? "kWh por hora · " + L.c.toLowerCase() + ": " + fmt.energy(d.totals.c_t).text + " · " + L.g.toLowerCase() + ": " + fmt.energy(d.totals.g_t).text
        : "kWh por hora · total do dia: " + fmt.energy(d.totals.c_t).text);
      hourly.render(barOption(spec), {
        legend,
        table: () => ({
          cols: ["Hora"].concat(series.map((s) => s.name + " (kWh)")),
          rows: rows.map((r, i) => (r.n ? [fmt.groupTick(r.label, "hour")].concat(series.map((s) => fmt.num(s.values[i], 2))) : null))
            .filter(Boolean),
        }),
      });
    });
  }

  // ---------------------------------------------------------------- histórico recente
  function loadHistory(dim) {
    const now = Math.floor(Date.now() / 1000);
    const from = now - rangeSeconds(range);
    const fields = ["pt"];
    for (const ph of phases) fields.push(PHASE_KEYS[ph].p, PHASE_KEYS[ph].u, PHASE_KEYS[ph].i);
    if (dim) for (const c of [power, volt, curr]) c.loading(true);
    sc.load("history", () => api.get("series", { device: dev, fields: fields.join(","), from, to: now + 1, points: 720 }), (d) => {
      const tk = tokens();
      const S = d.series;
      const live = app.live;
      if (!d.t.length) {
        for (const c of [power, volt, curr]) c.empty("Sem leituras neste período.");
        return;
      }
      const common = { t: d.t, from, to: now, res: d.res };
      const note = fmt.resLabel(d.res);

      let pSpec;
      if (split === "fases" && nPh === 3) {
        pSpec = Object.assign({ unit: "W", dec: 0, zeroBase: true, endLabels: true,
          series: phases.map((ph, i) => ({ name: labels[ph], color: tk.s[i], values: S[PHASE_KEYS[ph].p].avg })) }, common);
      } else {
        pSpec = Object.assign({ unit: "W", dec: 0, zeroBase: true,
          series: [{ name: "Potência total", color: tk.s[0], values: S.pt.avg, min: S.pt.min, max: S.pt.max, area: true }] }, common);
      }
      power.setSub((split === "fases" && nPh === 3 ? "por fase" : "total") + " · " + note + (d.res && split !== "fases" ? " · o véu mostra mínimo e máximo" : ""));
      power.render(lineOption(pSpec), { legend: pSpec.series.map((s) => ({ name: s.name, color: s.color })), table: seriesTable(pSpec) });

      const vSeries = phases.map((ph, i) => ({ name: nPh === 3 ? labels[ph] : "Tensão", color: tk.s[i], values: S[PHASE_KEYS[ph].u].avg }));
      const vSpec = Object.assign({ unit: "V", dec: 1, scale: true, series: vSeries }, common);
      if (live && live.limits) {
        let lo = Infinity;
        let hi = -Infinity;
        for (const s of vSeries) for (const v of s.values) if (v !== null && v > 20) {
          lo = Math.min(lo, v);
          hi = Math.max(hi, v);
        }
        vSpec.band = { from: live.limits.adeq_low, to: live.limits.adeq_high, label: "faixa adequada" };
        Object.assign(vSpec, niceBounds(Math.min(lo, live.limits.adeq_low), Math.max(hi, live.limits.adeq_high)));
      }
      volt.setSub((nPh === 3 ? "por fase · " : "") + note);
      volt.render(lineOption(vSpec), { legend: vSeries.map((s) => ({ name: s.name, color: s.color })), table: seriesTable(vSpec) });

      const cSeries = phases.map((ph, i) => ({ name: nPh === 3 ? labels[ph] : "Corrente", color: tk.s[i], values: S[PHASE_KEYS[ph].i].avg }));
      const cSpec = Object.assign({ unit: "A", dec: 2, zeroBase: true, series: cSeries }, common);
      curr.setSub((nPh === 3 ? "por fase · " : "") + note);
      curr.render(lineOption(cSpec), { legend: cSeries.map((s) => ({ name: s.name, color: s.color })), table: seriesTable(cSpec) });
      link([power, volt, curr]);
    }, () => {
      for (const c of [power, volt, curr]) c.loading(false);
    });
  }

  // ---------------------------------------------------------------- ciclo de vida
  sc.onCleanup(app.onLive(renderLive));
  sc.onCleanup(() => flowView && flowView.destroy());
  renderLive(app.live);
  loadSummary();
  loadMonthSpark();
  loadPhaseSpark();
  loadHourly();
  loadHistory(false);
  sc.every(60000, () => {
    loadSummary();
    loadHourly();
    loadPhaseSpark();
    loadHistory(false);
  });
  sc.every(600000, loadMonthSpark);
  return () => sc.destroy();
}
