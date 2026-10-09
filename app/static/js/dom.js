// Pequenos utilitários de DOM. Todo texto entra por textContent (nunca innerHTML).

const SVG_NS = "http://www.w3.org/2000/svg";
const SVG_TAGS = new Set(["svg", "path", "circle", "line", "polyline", "rect", "g", "text", "polygon", "defs", "marker", "title"]);

/** h("div", {class: "x", onclick: fn}, "texto", outroElemento, [lista]) */
export function h(tag, attrs, ...children) {
  const el = SVG_TAGS.has(tag) ? document.createElementNS(SVG_NS, tag) : document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (k === "class") el.setAttribute("class", v);
      else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
      else if (k === "dataset") Object.assign(el.dataset, v);
      else if (v === true) el.setAttribute(k, "");
      else el.setAttribute(k, v);
    }
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    if (Array.isArray(c)) append(el, c);
    else if (c instanceof Node) el.appendChild(c);
    else el.appendChild(document.createTextNode(String(c)));
  }
  return el;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function set(el, ...children) {
  clear(el);
  return append(el, children);
}

export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

const ICONS = {
  ok: "M20 6 9 17l-5-5",
  warn: "M12 9v4M12 17h.01M10.3 3.9 2.4 17.5A2 2 0 0 0 4.1 20.5h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z",
  crit: "M12 8v5M12 16.5h.01M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z",
  off: "M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M12 19.5h.01M3 3l18 18",
  info: "M12 11v5M12 7.5h.01M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z",
  up: "M12 19V5M6 11l6-6 6 6",
  down: "M12 5v14M6 13l6 6 6-6",
  left: "M15 5l-7 7 7 7",
  right: "M9 5l7 7-7 7",
  table: "M4 5h16v14H4zM4 10h16M4 15h16M10 5v14",
  chart: "M4 19V5M4 19h16M8 15l4-5 3 3 4-6",
  download: "M12 4v11M7 11l5 5 5-5M5 20h14",
  bolt: "M13 3 5 14h6l-1 7 8-11h-6l1-7Z",
  sun: "M12 4V2M12 22v-2M4 12H2M22 12h-2M6.3 6.3 4.9 4.9M19.1 19.1l-1.4-1.4M6.3 17.7l-1.4 1.4M19.1 4.9l-1.4 1.4M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10Z",
  refresh: "M20 11a8 8 0 0 0-14.9-3M4 4v4h4M4 13a8 8 0 0 0 14.9 3M20 20v-4h-4",
};

export function icon(name, cls) {
  const d = ICONS[name] || "";
  return h("svg", { class: cls || "ico", viewBox: "0 0 24 24", "aria-hidden": "true", fill: "none", stroke: "currentColor",
    "stroke-width": "1.9", "stroke-linecap": "round", "stroke-linejoin": "round" }, h("path", { d }));
}

/** Indicador de estado: sempre ícone + texto (nunca só cor). level: good | warning | serious | critical | neutral */
export function status(level, text) {
  const ico = { good: "ok", warning: "warn", serious: "warn", critical: "crit", neutral: "info" }[level] || "info";
  return h("span", { class: "st " + level }, icon(ico, "st-ico"), h("span", null, text));
}

let toastTimer = null;
export function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.hidden = true;
  }, 3800);
}
