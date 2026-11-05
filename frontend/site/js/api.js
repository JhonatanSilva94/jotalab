import { clearSession, getSession } from "./session.js";
import { openLogin } from "./auth.js";

const EXPIRED_MESSAGE = "Sua sessão expirou. Entre de novo para continuar.";

/** Lançado quando a sessão acabou: o modal de login já foi aberto. */
export class SessionExpired extends Error {}

function expire() {
  clearSession();
  openLogin(EXPIRED_MESSAGE);
  return new SessionExpired(EXPIRED_MESSAGE);
}

/**
 * Única porta de entrada para /api/cart e /api/orders.
 * Acrescenta "Authorization: Bearer <token>"; em 401 (ou token já vencido)
 * apaga a sessão, abre o login com "Sua sessão expirou" e lança SessionExpired.
 * O token nunca vai para nenhuma outra rota.
 */
export async function authFetch(url, { method = "GET", body } = {}) {
  const session = getSession();
  if (!session) throw expire();

  const headers = { Accept: "application/json", Authorization: `Bearer ${session.token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const response = await fetch(url, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 401) throw expire();
  return response;
}

/** Corpo JSON da resposta, ou null (204, corpo vazio ou não-JSON). */
export async function readJSON(response) {
  try { return await response.json(); } catch { return null; }
}
