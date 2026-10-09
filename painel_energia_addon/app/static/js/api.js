// Acesso à API do painel.

import { bridge } from "./host.js";

const BASE = bridge ? bridge.api : "api/";
const request = (u, init) => (bridge ? bridge.fetch(u, init) : fetch(u, init));

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function parse(resp) {
  let data = null;
  const text = await resp.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (e) {
      data = null;
    }
  }
  if (!resp.ok) {
    throw new ApiError(resp.status, (data && data.error) || "Erro " + resp.status);
  }
  return data;
}

export function url(path, params) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== null && v !== undefined && v !== "") q.set(k, v);
  }
  const s = q.toString();
  return BASE + path + (s ? "?" + s : "");
}

export async function get(path, params, opts) {
  const resp = await request(url(path, params), { cache: "no-store", signal: opts && opts.signal });
  return parse(resp);
}

export async function post(path, body) {
  const resp = await request(BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return parse(resp);
}

/**
 * Baixa um arquivo da API. Dentro do Home Assistant o endereço precisa ser assinado, porque um
 * link comum não leva o login.
 */
export async function download(path, params) {
  const target = url(path, params);
  window.location.assign(bridge ? await bridge.signPath(target) : target);
}

/** Atributos para um link de download que funciona nos dois modos. */
export function downloadLink(path, params) {
  if (!bridge) return { href: url(path, params), download: "" };
  return { href: "#", onclick: (ev) => {
    ev.preventDefault();
    download(path, params).catch((e) => console.error(e));
  } };
}
