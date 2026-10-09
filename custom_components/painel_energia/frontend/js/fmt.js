// Formatação de números, unidades e datas em pt-BR.
// Datas e horas são sempre mostradas no fuso do servidor (o mesmo usado para fechar os dias).

let TZ = "America/Sao_Paulo";
const cache = new Map();

function nf(dec) {
  let f = cache.get(dec);
  if (!f) {
    f = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: dec, maximumFractionDigits: dec });
    cache.set(dec, f);
  }
  return f;
}

export function num(v, dec = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return nf(dec).format(v);
}

/** Potência: usa kW a partir de 1000 W. Devolve {v, u, text}. */
export function power(w) {
  if (w === null || w === undefined) return { v: "—", u: "W", text: "—" };
  const a = Math.abs(w);
  if (a >= 1000) return unit(num(w / 1000, 2), "kW");
  return unit(num(w, 0), "W");
}

export function energy(kwh) {
  if (kwh === null || kwh === undefined) return { v: "—", u: "kWh", text: "—" };
  const a = Math.abs(kwh);
  if (a >= 10000) return unit(num(kwh / 1000, 2), "MWh");
  if (a >= 100) return unit(num(kwh, 0), "kWh");
  if (a >= 10) return unit(num(kwh, 1), "kWh");
  return unit(num(kwh, 2), "kWh");
}

function unit(v, u) {
  return { v, u, text: v + " " + u };
}

const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
export function money(v) {
  return v === null || v === undefined ? "—" : brl.format(v);
}

export function pct(v, dec = 0) {
  return v === null || v === undefined ? "—" : num(v, dec) + "%";
}

/** Valor com a unidade da grandeza (W, V, A, Hz...). */
export function withUnit(v, u, dec) {
  if (v === null || v === undefined) return "—";
  if (u === "W") return power(v).text;
  if (u === "kWh") return energy(v).text;
  return num(v, dec) + (u ? " " + u : "");
}

// ------------------------------------------------------------------ fuso do servidor
export function setTz(tz) {
  if (tz && tz !== TZ) {
    TZ = tz;
    dfCache.clear();
    offCache.clear();
  }
}
export function getTz() {
  return TZ;
}

const dfCache = new Map();
function df(key, opts) {
  let f = dfCache.get(key);
  if (!f) {
    try {
      f = new Intl.DateTimeFormat("pt-BR", { timeZone: TZ, ...opts });
    } catch (e) {
      f = new Intl.DateTimeFormat("pt-BR", opts);
    }
    dfCache.set(key, f);
  }
  return f;
}
const ms = (ts) => new Date(ts * 1000);

export const time = (ts) => (ts ? df("t", { hour: "2-digit", minute: "2-digit" }).format(ms(ts)) : "—");
export const timeSec = (ts) => (ts ? df("ts", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(ms(ts)) : "—");
export const dateShort = (ts) => (ts ? df("d", { day: "2-digit", month: "2-digit" }).format(ms(ts)) : "—");
export const dateLong = (ts) => (ts ? df("dl", { day: "numeric", month: "long", year: "numeric" }).format(ms(ts)) : "—");
export const dateTime = (ts) => (ts ? df("dt", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).format(ms(ts)) : "—");
export const dateTimeFull = (ts) =>
  ts ? df("dtf", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(ms(ts)) : "—";
export const weekday = (ts) => (ts ? df("wd", { weekday: "short" }).format(ms(ts)).replace(".", "") : "—");

const offCache = new Map();
/** Diferença (s) entre o horário do servidor e o UTC no instante ts. */
export function tzOffset(ts) {
  const hour = Math.floor(ts / 3600);
  let off = offCache.get(hour);
  if (off === undefined) {
    const p = {};
    for (const part of df("off", { hourCycle: "h23", year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "numeric", second: "numeric" }).formatToParts(new Date(hour * 3600000))) {
      p[part.type] = Number(part.value);
    }
    off = Math.round((Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) - hour * 3600000) / 1000);
    offCache.set(hour, off);
  }
  return off;
}
/** Instante (s) -> milissegundos "de parede" do servidor, para eixos de tempo com useUTC. */
export const chartTime = (ts) => (ts + tzOffset(ts)) * 1000;

// ------------------------------------------------------------------ rótulos vindos do servidor
const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
const MESES_LONGOS = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];
const DIAS = ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"];

/** "2026-10-07" -> {y, m, d, wd} sem depender do fuso do navegador. */
export function parseDate(iso) {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  const wd = new Date(Date.UTC(y, m - 1, d || 1)).getUTCDay();
  return { y, m, d, wd };
}

/** Rótulo curto de um grupo de energia: hora "14h", dia "07", mês "out". */
export function groupTick(label, group) {
  if (group === "month") return MESES[Number(label.slice(5, 7)) - 1];
  if (group === "day") return label.slice(8, 10);
  if (group === "hour") return label.slice(11, 13) + "h";
  return label.slice(11, 16);
}

/** Rótulo completo de um grupo de energia, para tooltip e tabela. */
export function groupLabel(label, group) {
  const p = parseDate(label);
  if (group === "month") return MESES_LONGOS[p.m - 1] + " de " + p.y;
  const day = DIAS[p.wd] + ", " + String(p.d).padStart(2, "0") + "/" + String(p.m).padStart(2, "0");
  if (group === "day") return day;
  const hh = label.slice(11, 13);
  if (group === "hour") return day + " · " + hh + "h–" + String((Number(hh) + 1) % 24).padStart(2, "0") + "h";
  return day + " · " + label.slice(11, 16);
}

export function periodTitle(p) {
  const a = parseDate(p.from_date);
  const b = parseDate(p.to_date);
  const dm = (x) => String(x.d).padStart(2, "0") + "/" + String(x.m).padStart(2, "0");
  if (p.period === "day") return DIAS[a.wd] + ", " + a.d + " de " + MESES_LONGOS[a.m - 1];
  if (p.period === "week") return dm(a) + " a " + dm(b);
  if (p.period === "month") return MESES_LONGOS[a.m - 1] + " de " + a.y;
  return String(a.y);
}

export const monthName = (m) => MESES_LONGOS[m - 1];
export const weekdayName = (i) => DIAS[i];

// ------------------------------------------------------------------ tempo relativo e durações
export function ago(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 90) return "há " + s + " s";
  if (s < 5400) return "há " + Math.round(s / 60) + " min";
  if (s < 172800) return "há " + Math.round(s / 3600) + " h";
  return "há " + Math.round(s / 86400) + " dias";
}

export function duration(seconds) {
  const s = Math.max(0, Math.round(seconds || 0));
  if (s < 60) return s + " s";
  if (s < 3600) return Math.round(s / 60) + " min";
  const h = Math.floor(s / 3600);
  const m = Math.round((s % 3600) / 60);
  if (h < 48) return h + " h" + (m ? " " + m + " min" : "");
  return Math.round(s / 86400) + " dias";
}

export function bytes(n) {
  if (!n) return "0";
  if (n < 1024 * 1024) return num(n / 1024, 0) + " kB";
  if (n < 1024 * 1024 * 1024) return num(n / 1048576, 1) + " MB";
  return num(n / 1073741824, 2) + " GB";
}

/** Variação percentual entre atual e referência; null quando não dá para comparar. */
export function change(cur, ref) {
  if (cur === null || cur === undefined || !ref || ref <= 0) return null;
  return ((cur - ref) / ref) * 100;
}

// ------------------------------------------------------------------ horários "de parede" (eixos dos gráficos)
// Os gráficos recebem os instantes já deslocados para o fuso do servidor (chartTime) e trabalham em UTC.
const wall = {
  dt: new Intl.DateTimeFormat("pt-BR", { timeZone: "UTC", weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }),
  dts: new Intl.DateTimeFormat("pt-BR", { timeZone: "UTC", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }),
  d: new Intl.DateTimeFormat("pt-BR", { timeZone: "UTC", weekday: "short", day: "2-digit", month: "2-digit", year: "numeric" }),
};
export const wallDateTime = (msShifted) => wall.dt.format(new Date(msShifted)).replace(".,", ",");
export const wallDateTimeSec = (msShifted) => wall.dts.format(new Date(msShifted));
export const wallDate = (msShifted) => wall.d.format(new Date(msShifted)).replace(".,", ",");

export function resLabel(res) {
  if (!res) return "leituras individuais";
  if (res < 3600) return "média de " + Math.round(res / 60) + " min";
  if (res < 86400) return "média de " + Math.round(res / 3600) + " h";
  return "média de " + Math.round(res / 86400) + (res >= 172800 ? " dias" : " dia");
}
