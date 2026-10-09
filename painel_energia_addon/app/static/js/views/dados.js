// Dados: qualquer grandeza enviada pelo medidor, em gráfico e tabela, e exportação.

import * as api from "../api.js";
import * as fmt from "../fmt.js";
import { h, set, icon, cssVar } from "../dom.js";
import { ChartCard, lineOption, seriesTable, tokens, dataTable } from "../charts.js";
import { rangeSeg, rangeSeconds, stored, store, seg } from "../ui.js";
import { scope } from "../view.js";

// grupo -> campos (fases A, B, C e, quando existe, o total)
const GROUPS = [
  ["potencia", "Potência ativa", ["pa", "pb", "pc", "pt"], "W", 0],
  ["reativa", "Potência reativa", ["qa", "qb", "qc", "qt"], "VAr", 0],
  ["aparente", "Potência aparente", ["sa", "sb", "sc", "st"], "VA", 0],
  ["tensao", "Tensão", ["uarms", "ubrms", "ucrms"], "V", 1],
  ["corrente", "Corrente", ["iarms", "ibrms", "icrms", "itrms"], "A", 2],
  ["fp", "Fator de potência", ["pfa", "pfb", "pfc", "pft"], "", 3],
  ["angulo", "Ângulo tensão-corrente", ["pga", "pgb", "pgc"], "°", 1],
  ["angulo_tensao", "Ângulo entre tensões", ["yuaub", "yuauc", "yubuc"], "°", 1],
  ["frequencia", "Frequência", ["freq"], "Hz", 2],
  ["temperatura", "Temperatura do medidor", ["tpsd"], "°C", 1],
  ["sinal", "Sinal Wi-Fi", ["rssi_wifi"], "dBm", 0],
];
const SCALE = new Set(["tensao", "fp", "frequencia", "temperatura", "sinal", "angulo", "angulo_tensao"]);

export default function mount(root, app) {
  const sc = scope();
  const dev = app.device;
  const nPh = app.phases();
  const labels = app.labels();
  // medidor recém-ligado: começa mostrando a última hora, onde os primeiros pontos aparecem
  const born = app.info() && app.info().first_seen;
  let range = stored("dados.periodo", born && Date.now() / 1000 - born < 3 * 3600 ? "1h" : "24h", ["1h", "6h", "24h", "7d", "30d"]);
  let group = stored("dados.grandeza", "potencia", GROUPS.map((g) => g[0]));
  let csvFmt = stored("dados.csv", "br", ["br", "en"]);

  const chart = new ChartCard({ title: "", height: 320, cls: "c12" });
  const tableCard = h("section", { class: "card c12" });
  const exportCard = h("section", { class: "card c12" });
  const groupSel = h("select", { class: "select", style: { width: "auto" }, "aria-label": "Grandeza", onchange: (ev) => {
    group = ev.target.value;
    store("dados.grandeza", group);
    load(true);
  } }, GROUPS.map((g) => h("option", { value: g[0] }, g[1])));
  groupSel.value = group;
  const rangeCtl = rangeSeg(["1h", "6h", "24h", "7d", "30d"], range, (v) => {
    range = v;
    store("dados.periodo", v);
    load(true);
    renderExport();
  });

  root.append(
    h("div", { class: "filters" }, rangeCtl, groupSel),
    h("div", { class: "grid" }, chart.el, tableCard, exportCard),
  );

  function fieldName(f) {
    const meta = app.fields && app.fields.fields.find((x) => x.key === f);
    if (!meta) return f;
    if (meta.phase && meta.phase !== "t") return nPh === 3 ? labels[meta.phase] : meta.label.split(" · ")[0];
    if (meta.phase === "t") return "Total";
    return meta.label;
  }

  function load(dim) {
    const now = Math.floor(Date.now() / 1000);
    const from = now - rangeSeconds(range);
    const g = GROUPS.find((x) => x[0] === group);
    let fields = g[2];
    if (nPh === 1) {
      // monofásico: só a fase A (o total é igual a ela)
      const metaOf = (f) => app.fields && app.fields.fields.find((x) => x.key === f);
      fields = fields.filter((f) => {
        const m = metaOf(f);
        return !m || m.phase === null || m.phase === "a";
      });
    }
    if (dim) chart.loading(true);
    sc.load("series", () => api.get("series", { device: dev, fields: fields.join(","), from, to: now + 1, points: 800 }), (d) => {
      const tk = tokens();
      chart.setTitle(g[1]);
      if (!d.t.length) {
        chart.empty("Sem leituras neste período.");
        return;
      }
      const phaseColors = { a: tk.s[0], b: tk.s[1], c: tk.s[2] };
      let k = 0;
      const series = fields.map((f) => {
        const meta = app.fields && app.fields.fields.find((x) => x.key === f);
        const s = d.series[f];
        let color;
        if (meta && meta.phase === "t") color = cssVar("--text-2");      // o total não é uma fase: tinta neutra
        else if (meta && phaseColors[meta.phase]) color = phaseColors[meta.phase];
        else color = tk.s[k++ % 3];
        const one = fields.length === 1;
        return { name: fieldName(f), color, values: s.avg, min: one ? s.min : undefined, max: one ? s.max : undefined, area: one && !SCALE.has(group) };
      });
      const spec = { t: d.t, from, to: now, res: d.res, unit: g[3], dec: g[4], scale: SCALE.has(group), zeroBase: !SCALE.has(group), series };
      chart.setSub(fmt.resLabel(d.res) + " · " + d.t.length + (d.t.length === 1 ? " ponto" : " pontos"));
      chart.render(lineOption(spec), { legend: series.map((s) => ({ name: s.name, color: s.color })), table: seriesTable(spec) });
    }, (e) => {
      chart.loading(false);
      chart.empty("Não foi possível carregar: " + e.message);
    });
  }

  function loadTable() {
    sc.load("readings", () => api.get("readings", { device: dev, limit: 60 }), (d) => {
      const metaOf = (f) => (app.fields && app.fields.fields.find((x) => x.key === f)) || { dec: 2, label: f };
      const cols = ["Data e hora"].concat(d.fields);
      const rows = d.rows.map((r) => [fmt.dateTimeFull(r[0])].concat(d.fields.map((f, i) => fmt.num(r[i + 1], metaOf(f).dec))));
      const table = dataTable(cols, rows, 380);
      const ths = table.querySelectorAll("th");
      d.fields.forEach((f, i) => {
        const m = metaOf(f);
        if (ths[i + 1]) ths[i + 1].title = m.label + (m.unit ? " (" + m.unit + ")" : "");
      });
      set(tableCard,
        h("div", { class: "card-head" }, h("div", null, h("h2", { class: "card-title" }, "Últimas leituras recebidas"),
          h("div", { class: "card-sub" }, "exatamente como o medidor enviou · passe o cursor no nome da coluna para ver o significado"))),
        table);
    });
  }

  function renderExport() {
    const now = Math.floor(Date.now() / 1000);
    const from = now - rangeSeconds(range);
    const link = (kind, text, span) => h("a", Object.assign({ class: "btn" }, api.downloadLink("export.csv", { device: dev, kind, fmt: csvFmt, from: span ? now - span : from, to: now + 1 })),
      icon("download"), text);
    const fmtCtl = seg([["br", "Excel em português (; e vírgula)"], ["en", "Padrão internacional (, e ponto)"]], csvFmt, (v) => {
      csvFmt = v;
      store("dados.csv", v);
      renderExport();
    }, "Formato do CSV");
    set(exportCard,
      h("div", { class: "card-head" }, h("div", null, h("h2", { class: "card-title" }, "Exportar"),
        h("div", { class: "card-sub" }, "arquivos CSV para abrir no Excel, LibreOffice ou em scripts"))),
      h("div", { class: "form-actions", style: { marginTop: "0" } }, fmtCtl),
      h("div", { class: "form-actions" },
        link("bruto", "Leituras do período selecionado"),
        link("hora", "Energia por hora (30 dias)", 30 * 86400),
        link("dia", "Energia por dia (1 ano)", 366 * 86400),
        link("mes", "Energia por mês (tudo)", 6 * 366 * 86400)),
      h("p", { class: "note", style: { marginTop: "12px" } }, "A cópia completa do banco de dados fica em Sistema › Dados armazenados."));
  }

  load(false);
  loadTable();
  renderExport();
  sc.every(30000, loadTable);
  sc.every(60000, () => load(false));
  return () => sc.destroy();
}
