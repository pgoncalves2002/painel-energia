// Onde o painel está rodando: sozinho (servidor próprio) ou dentro do Home Assistant.
// No Home Assistant o painel abre em um iframe; o elemento do painel entrega uma "ponte" com o acesso
// autenticado à API, o tema em uso e o botão de menu.

let found = null;
try {
  found = (window.frameElement && window.frameElement.painelBridge) || null;
} catch (e) {
  found = null;
}

export const bridge = found;
export const isHa = !!found;

// "ha" = integração; "addon" = add-on do Home Assistant; "standalone" = servidor próprio.
let kind = found ? "ha" : "standalone";
export function setHostKind(value) {
  if (!found && (value === "addon" || value === "standalone")) kind = value;
}
export const isAddon = () => kind === "addon";

/** Onde ficam as opções que, no servidor próprio, estão no arquivo .env. */
export function whereOptions() {
  if (kind === "ha") return "nas opções da integração (Configurações › Dispositivos e serviços › Painel de Energia › Configurar)";
  if (kind === "addon") return "na aba Configuração do add-on (Configurações › Add-ons › Painel de Energia)";
  return "no arquivo .env";
}
