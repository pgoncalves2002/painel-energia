// Tela inicial enquanto nenhum medidor enviou dados.

import { h } from "../dom.js";
import { guide, diagnostics } from "./conexao.js";
import { scope } from "../view.js";
import { isHa, isAddon } from "../host.js";

export default function mount(root, app) {
  const sc = scope();
  const ing = app.status.ingest;
  const simUrl = "http://" + (ing.host || "ip-do-servidor") + ":" + ing.http_port + ing.path;
  root.append(
    h("section", { class: "card" },
      h("div", { class: "card-head" }, h("div", null,
        h("h2", { style: { fontSize: "19px", fontWeight: "650", letterSpacing: "-0.01em" } }, "Aguardando a primeira leitura do medidor"),
        h("div", { class: "card-sub", style: { fontSize: "13.5px", marginTop: "4px" } },
          "O painel está pronto. Falta apontar o medidor para este " + (isHa || isAddon() ? "Home Assistant" : "servidor") + "; assim que a primeira leitura chegar, esta tela é substituída pelo painel."))),
      guide(app)),
    h("section", { class: "card" },
      h("div", { class: "card-head" }, h("div", null, h("h2", { class: "card-title" }, "Mensagens recebidas"),
        h("div", { class: "card-sub" }, "tudo o que chega ao painel aparece aqui, inclusive tentativas rejeitadas — útil para conferir a configuração"))),
      diagnostics(sc, 3000)),
    h("section", { class: "card" },
      h("div", { class: "card-head" }, h("div", null, h("h2", { class: "card-title" }, "Quer ver o painel funcionando antes?"))),
      h("p", { class: "muted" }, "Rode o simulador incluído no projeto (", h("span", { class: "code" }, "python3 tools/simulador.py --url " + simUrl),
        isHa ? "): ele envia leituras de teste do mesmo jeito que o medidor."
          : isAddon() ? ") ou ligue a opção Demonstração na aba Configuração do add-on para carregar um mês de dados simulados."
          : ") ou suba o painel com ", isHa || isAddon() ? null : h("span", { class: "code" }, "DEMO=1"), isHa || isAddon() ? null : " no arquivo .env para carregar um mês de dados simulados.")),
  );
  // verifica com mais frequência para trocar de tela assim que o medidor aparecer
  sc.every(3000, () => app.refreshStatus());
  return () => sc.destroy();
}
