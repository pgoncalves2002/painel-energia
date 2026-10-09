// Painel de Energia dentro do Home Assistant.
// O Home Assistant carrega este módulo e cria o elemento <painel-energia-panel>; ele abre o painel em um
// iframe (mesma origem) e entrega uma "ponte" com o acesso autenticado à API, o tema e o menu lateral.

class PainelEnergiaPanel extends HTMLElement {
  constructor() {
    super();
    this._listeners = new Set();
    this._state = { dark: false, menu: false, lang: "pt-BR" };
    this._narrow = false;
  }

  set hass(hass) {
    this._hass = hass;
    this._refresh();
  }

  get hass() {
    return this._hass;
  }

  set narrow(value) {
    this._narrow = !!value;
    this._refresh();
  }

  set panel(panel) {
    this._panel = panel;
  }

  set route(_route) { /* o painel tem a própria navegação */ }

  _refresh() {
    const hass = this._hass;
    if (!hass) return;
    const next = {
      dark: !!(hass.themes && hass.themes.darkMode),
      menu: this._narrow || hass.dockedSidebar === "always_hidden",
      lang: hass.language || "pt-BR",
    };
    const prev = this._state;
    this._state = next;
    if (next.dark !== prev.dark || next.menu !== prev.menu) {
      for (const fn of [...this._listeners]) {
        try {
          fn(Object.assign({}, next));
        } catch (e) { /* o iframe pode ter sido recarregado */ }
      }
    }
  }

  connectedCallback() {
    if (this._frame) return;
    this.style.cssText = "display:block;position:relative;width:100%;height:100vh;height:100dvh;overflow:hidden;" +
      "background:var(--primary-background-color)";
    const cfg = (this._panel && this._panel.config) || {};
    const frame = document.createElement("iframe");
    frame.title = "Painel de Energia";
    frame.style.cssText = "border:0;display:block;width:100%;height:100%";
    frame.painelBridge = this._bridge();
    frame.src = (cfg.static_url || "/painel_energia_static") + "/index.html?v=" + encodeURIComponent(cfg.version || "");
    this.appendChild(frame);
    this._frame = frame;
  }

  disconnectedCallback() {
    // ao sair do painel, o iframe é descartado para parar as atualizações periódicas
    this._listeners.clear();
    if (this._frame) {
      this._frame.remove();
      this._frame = null;
    }
  }

  _bridge() {
    const el = this;
    return {
      api: "/api/painel_energia/",
      fetch: (path, init) => el._hass.fetchWithAuth(path, init),
      signPath: (path) => el._hass.callWS({ type: "auth/sign_path", path, expires: 60 }).then((r) => r.path),
      state: () => Object.assign({}, el._state),
      subscribe: (fn) => {
        el._listeners.add(fn);
        return () => el._listeners.delete(fn);
      },
      toggleMenu: () => el.dispatchEvent(new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true })),
      navigate: (path) => {
        window.history.pushState(null, "", path);
        window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
      },
    };
  }
}

if (!customElements.get("painel-energia-panel")) {
  customElements.define("painel-energia-panel", PainelEnergiaPanel);
}
