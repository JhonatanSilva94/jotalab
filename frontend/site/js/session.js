// Sessão do usuário: token e nome no localStorage.
// Quem precisa reagir a login/logout escuta onSessionChange().
const TOKEN_KEY = "jotalab.token";
const NAME_KEY = "jotalab.name";
const EVENT = "jotalab:session";

// localStorage pode lançar (janela privada, site data bloqueado): cai para a memória.
let memory = { token: null, name: null };
const storage = {
  get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* fica só na memória */ } },
  remove: (k) => { try { localStorage.removeItem(k); } catch { /* nada a fazer */ } },
};

// Lê o payload do JWT só para exp/sub (quem VALIDA o token é o backend).
function readPayload(token) {
  try {
    return JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
  } catch {
    return null;
  }
}

/** Sessão válida ({token, name, userId}) ou null. Token vencido conta como deslogado. */
export function getSession() {
  const token = storage.get(TOKEN_KEY) ?? memory.token;
  const name = storage.get(NAME_KEY) ?? memory.name;
  if (!token || !name) return null;
  const payload = readPayload(token);
  if (!payload || typeof payload.sub !== "string" || (typeof payload.exp === "number" && payload.exp * 1000 <= Date.now())) {
    storage.remove(TOKEN_KEY);
    storage.remove(NAME_KEY);
    memory = { token: null, name: null };
    return null;
  }
  return { token, name, userId: payload.sub };
}

export function setSession(token, name) {
  memory = { token, name };
  storage.set(TOKEN_KEY, token);
  storage.set(NAME_KEY, name);
  window.dispatchEvent(new Event(EVENT));
}

export function clearSession() {
  memory = { token: null, name: null };
  storage.remove(TOKEN_KEY);
  storage.remove(NAME_KEY);
  window.dispatchEvent(new Event(EVENT));
}

export function onSessionChange(listener) {
  window.addEventListener(EVENT, listener);
}

// Login/logout em outra aba também atualiza esta.
window.addEventListener("storage", (e) => {
  if (e.key === TOKEN_KEY || e.key === NAME_KEY || e.key === null) window.dispatchEvent(new Event(EVENT));
});
