import { USERS_URL, LOGIN_URL } from "./config.js";
import { clearSession, getSession, onSessionChange, setSession } from "./session.js";
import { hideMessage, setupDialog, showMessage } from "./ui.js";

const dialog = document.getElementById("auth-dialog");
const form = document.getElementById("auth-form");
const title = document.getElementById("auth-title");
const submit = document.getElementById("auth-submit");
const formError = document.getElementById("auth-form-error");
const notice = document.getElementById("auth-notice");
const passwordInput = document.getElementById("auth-password");
const passwordHint = document.getElementById("auth-password-hint");
const nameField = form.querySelector('[data-field="name"]');
const modeButtons = dialog.querySelectorAll("[data-mode]");

const loginButton = document.getElementById("login-button");
const account = document.getElementById("account");
const accountName = document.getElementById("account-name");
const logoutButton = document.getElementById("logout-button");
const ordersButton = document.getElementById("orders-button");

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/; // a mesma regra do service-login
const GENERIC_ERROR = "Algo deu errado do nosso lado. Tente de novo em instantes.";
const FIELD_MESSAGES = {
  name: "Conta pra gente como você se chama.",
  email: "Digite um e-mail válido, como nome@exemplo.com.",
  password: "A senha precisa ter entre 8 e 72 caracteres.",
};

let mode = "login";
let opener = null; // quem abriu o modal: recebe o foco de volta

// ---------- Cabeçalho: logado x deslogado ----------

function renderHeader() {
  const session = getSession();
  accountName.textContent = session ? session.name : "";
  account.hidden = !session;
  loginButton.hidden = Boolean(session);
}

// ---------- Modal ----------

function setMode(next) {
  mode = next;
  const signup = mode === "signup";
  title.textContent = signup ? "Criar conta" : "Entrar";
  submit.textContent = signup ? "Criar conta" : "Entrar";
  nameField.hidden = !signup;
  passwordHint.hidden = !signup;
  passwordInput.autocomplete = signup ? "new-password" : "current-password";
  modeButtons.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
  clearErrors();
}

/** Abre o modal na aba Entrar. `message` aparece no topo (ex.: sessão expirou). */
export function openLogin(message = "") {
  if (message) showMessage(notice, message);
  else hideMessage(notice);
  if (dialog.open) return;
  opener = document.activeElement;
  setMode("login");
  dialog.showModal();
  form.elements.email.focus();
}

function setFieldError(field, message) {
  const input = form.elements[field];
  const error = document.getElementById(`auth-${field}-error`);
  if (!input || !error) return;
  error.textContent = message;
  input.setAttribute("aria-invalid", message ? "true" : "false");
}

function clearErrors() {
  ["name", "email", "password"].forEach((f) => setFieldError(f, ""));
  hideMessage(formError);
}

function validate(values) {
  const errors = {};
  if (mode === "signup" && !values.name) errors.name = FIELD_MESSAGES.name;
  if (!EMAIL_RE.test(values.email)) errors.email = FIELD_MESSAGES.email;
  if (mode === "signup") {
    const bytes = new TextEncoder().encode(values.password).length;
    if (values.password.length < 8 || bytes > 72) errors.password = FIELD_MESSAGES.password;
  } else if (!values.password) {
    errors.password = "Digite sua senha.";
  }
  return errors;
}

// 422 do service-login: {"detail": [{"field": "...", "msg": "..."}]}
function showValidationErrors(data) {
  const list = Array.isArray(data?.detail) ? data.detail : [];
  let shown = false;
  for (const { field } of list) {
    if (FIELD_MESSAGES[field]) {
      setFieldError(field, FIELD_MESSAGES[field]);
      shown = true;
    }
  }
  if (!shown) showMessage(formError, "Confira os dados e tente de novo.");
}

async function postJSON(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  let data = null;
  try { data = await response.json(); } catch { /* corpo vazio ou não-JSON */ }
  return { status: response.status, ok: response.ok, data };
}

function setBusy(busy) {
  submit.disabled = busy;
  submit.setAttribute("aria-busy", String(busy));
  if (busy) submit.textContent = mode === "signup" ? "Criando conta…" : "Entrando…";
  else submit.textContent = mode === "signup" ? "Criar conta" : "Entrar";
}

function signIn(token, name) {
  const back = opener;
  setSession(token, name); // avisa cabeçalho e carrinho (antes do close: quem escuta o close já vê logado)
  dialog.close();
  form.reset();
  // O botão "Entrar" some ao logar: nesse caso o foco vai para "Meus pedidos".
  const target = back && back.isConnected && !back.closest("[hidden]") ? back : ordersButton;
  target.focus();
}

async function handleSubmit(event) {
  event.preventDefault();
  clearErrors();

  const values = {
    name: form.elements.name.value.trim(),
    email: form.elements.email.value.trim().toLowerCase(),
    password: form.elements.password.value,
  };

  const errors = validate(values);
  const fields = Object.keys(errors);
  if (fields.length) {
    fields.forEach((f) => setFieldError(f, errors[f]));
    form.elements[fields[0]].focus();
    return;
  }

  setBusy(true);
  try {
    if (mode === "signup") {
      const created = await postJSON(USERS_URL, values);
      if (created.status === 409) {
        setFieldError("email", "Esse e-mail já tem cadastro. Use a opção Entrar.");
        form.elements.email.focus();
        return;
      }
      if (created.status === 422) return showValidationErrors(created.data);
      if (!created.ok) return showMessage(formError, GENERIC_ERROR);
    }

    // Login (direto, ou logo depois do cadastro para já entrar na conta)
    const res = await postJSON(LOGIN_URL, { email: values.email, password: values.password });
    if (res.status === 401) {
      showMessage(formError, "E-mail ou senha incorretos. Confira e tente de novo.");
      passwordInput.select();
      return;
    }
    if (res.status === 422) return showValidationErrors(res.data);
    if (!res.ok || !res.data?.token) return showMessage(formError, GENERIC_ERROR);

    signIn(res.data.token, res.data.name ?? values.name);
  } catch (error) {
    console.error(error);
    showMessage(formError, "Sem conexão com o servidor. Verifique sua internet e tente de novo.");
  } finally {
    setBusy(false);
  }
}

// ---------- Eventos ----------

setupDialog(dialog);
document.getElementById("auth-close").addEventListener("click", () => dialog.close());
loginButton.addEventListener("click", () => openLogin());
logoutButton.addEventListener("click", () => {
  clearSession();
  loginButton.focus();
});
modeButtons.forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
form.addEventListener("submit", handleSubmit);

// Digitar num campo limpa o erro dele
form.addEventListener("input", (e) => {
  if (e.target.name) setFieldError(e.target.name, "");
});

dialog.addEventListener("close", () => {
  clearErrors();
  hideMessage(notice);
  form.elements.password.value = "";
});

onSessionChange(renderHeader);
renderHeader();
