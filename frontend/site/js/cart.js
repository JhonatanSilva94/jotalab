import { CART_URL, CART_ITEMS_URL, ORDERS_URL } from "./config.js";
import { authFetch, readJSON, SessionExpired } from "./api.js";
import { openLogin } from "./auth.js";
import { openOrders } from "./orders.js";
import { getProductMap } from "./products.js";
import { getSession, onSessionChange } from "./session.js";
import { bump, hideMessage, money, setupDialog, showMessage } from "./ui.js";

const MAX_QUANTITY = 99; // mesmo limite do service-cart

const drawer = document.getElementById("cart-drawer");
const cartButton = document.getElementById("cart-button");
const countEl = document.getElementById("cart-count");
const lines = document.getElementById("cart-lines");
const totalEl = document.getElementById("cart-total");
const message = document.getElementById("cart-message");
const checkoutButton = document.getElementById("checkout-button");
const lineTemplate = document.getElementById("cart-line-template");
const emptyNotice = document.getElementById("cart-empty-notice");
const views = {
  items: document.getElementById("cart-view-items"),
  empty: document.getElementById("cart-view-empty"),
  success: document.getElementById("cart-view-success"),
};

let items = [];            // [{productId, quantity}] como o service-cart devolve
let products = new Map();  // productId -> produto (nome, imagem, preço)
let pendingCheckout = false; // tentou finalizar sem login: ao entrar, volta para o carrinho
let checkingOut = false;

// ---------- Carrinho sem login (no navegador) ----------
// { owner, items }:
//   owner = null    -> carrinho montado por um visitante
//   owner = userId  -> cópia do carrinho da conta, feita ao sair. Continua
//                      visível e editável deslogado e, ao entrar de novo na
//                      MESMA conta, vale exatamente como está.
// Nunca vai para o servidor sem login.
const GUEST_KEY = "jotalab.guestCart";
let currentUserId = null; // dono do carrinho mostrado enquanto logado

const validItem = (i) => typeof i?.productId === "string" && Number.isInteger(i.quantity) && i.quantity > 0;

function readGuestCart() {
  try {
    const data = JSON.parse(localStorage.getItem(GUEST_KEY) ?? "null");
    if (Array.isArray(data)) return { owner: null, items: data.filter(validItem) }; // formato antigo
    if (data && Array.isArray(data.items)) {
      return { owner: typeof data.owner === "string" ? data.owner : null, items: data.items.filter(validItem) };
    }
  } catch { /* corrompido: começa vazio */ }
  return { owner: null, items: [] };
}

const readGuest = () => readGuestCart().items;

function writeGuest(list, owner = readGuestCart().owner) {
  try {
    // Visitante sem itens: nada a guardar. Com dono, guarda até vazio
    // (esvaziar deslogado também precisa valer ao entrar de novo).
    if (!list.length && !owner) localStorage.removeItem(GUEST_KEY);
    else localStorage.setItem(GUEST_KEY, JSON.stringify({ owner, items: list }));
  } catch { /* sem storage: o carrinho vive só nesta página */ }
}

function dropGuest() {
  try { localStorage.removeItem(GUEST_KEY); } catch { /* nada a fazer */ }
}

const CHECKOUT_ERRORS = {
  400: "Seu carrinho está vazio.",
  402: "Pagamento recusado. Seu carrinho continua salvo: tente de novo ou revise os itens.",
  409: "Produto sem estoque suficiente. Ajuste a quantidade e tente de novo.",
  503: "Serviço indisponível, tente novamente em instantes.",
};

// ---------- Contador no cabeçalho ----------

function renderCount({ animate = false } = {}) {
  const count = items.reduce((sum, i) => sum + i.quantity, 0);
  const changed = countEl.textContent !== String(count);
  countEl.textContent = String(count);
  cartButton.setAttribute("aria-label", `Carrinho, ${count} ${count === 1 ? "item" : "itens"}`);
  if (animate && changed) bump(countEl);
}

async function loadCart() {
  if (!getSession()) {
    items = readGuest();
    renderCount();
    return;
  }
  try {
    currentUserId = getSession()?.userId ?? null;
    const response = await authFetch(CART_URL);
    if (response.ok) items = (await readJSON(response))?.items ?? [];
  } catch (error) {
    if (!(error instanceof SessionExpired)) console.error(error);
  }
  renderCount();
}

// ---------- Chamadas ao service-cart ----------

/** Define a quantidade (o POST do cart substitui) e devolve true se deu certo.
 *  Sem login, altera o carrinho de visitante no navegador. */
async function setQuantity(productId, quantity) {
  if (!getSession()) {
    const guest = readGuest();
    const item = guest.find((i) => i.productId === productId);
    if (item) item.quantity = quantity;
    else guest.push({ productId, quantity });
    writeGuest(guest);
    items = guest;
  } else {
    const response = await authFetch(CART_ITEMS_URL, { method: "POST", body: { productId, quantity } });
    if (!response.ok) return false;
    items = (await readJSON(response))?.items ?? items;
  }
  renderCount({ animate: true });
  return true;
}

async function removeItem(productId) {
  if (!getSession()) {
    writeGuest(readGuest().filter((i) => i.productId !== productId));
  } else {
    const response = await authFetch(`${CART_ITEMS_URL}/${encodeURIComponent(productId)}`, { method: "DELETE" });
    if (!response.ok) return false;
  }
  items = items.filter((i) => i.productId !== productId);
  renderCount({ animate: true });
  return true;
}

/**
 * Ao entrar, leva o carrinho do navegador para o service-cart:
 *  - cópia da MESMA conta (feita ao sair): substitui o carrinho da conta,
 *    então remoções e mudanças feitas deslogado também valem;
 *  - montado por visitante: junta com o que a conta já tinha salvo (para o
 *    mesmo produto vale a quantidade do visitante);
 *  - cópia de OUTRA conta: descartada (não mistura carrinhos de pessoas diferentes).
 */
async function syncGuestCart(userId) {
  const { owner, items: local } = readGuestCart();
  if (owner && owner !== userId) return dropGuest();
  if (!owner && local.length === 0) return;

  let failed = false;
  try {
    for (const item of local) {
      const quantity = Math.min(item.quantity, MAX_QUANTITY);
      const response = await authFetch(CART_ITEMS_URL, { method: "POST", body: { productId: item.productId, quantity } });
      if (response.ok) items = (await readJSON(response))?.items ?? items;
      else if (response.status >= 500) failed = true; // 4xx (dado inválido): descarta o item
    }
    if (owner === userId) {
      // Substituir: tira da conta o que não está mais no carrinho local.
      const keep = new Set(local.map((i) => i.productId));
      const response = await authFetch(CART_URL);
      const server = response.ok ? (await readJSON(response))?.items ?? [] : null;
      if (!server) failed = true;
      for (const item of server ?? []) {
        if (keep.has(item.productId)) continue;
        const del = await authFetch(`${CART_ITEMS_URL}/${encodeURIComponent(item.productId)}`, { method: "DELETE" });
        if (!del.ok) failed = true;
      }
    }
  } catch (error) {
    if (error instanceof SessionExpired) return; // mantém o local para a próxima vez
    failed = true;
  }
  if (!failed) dropGuest(); // se algo falhou, o próximo login tenta de novo
}

// ---------- "Adicionar ao carrinho" na vitrine ----------

function flash(button, text) {
  const original = button.dataset.label ?? button.textContent;
  button.dataset.label = original;
  button.textContent = text;
  clearTimeout(button._flash);
  button._flash = setTimeout(() => { button.textContent = original; }, 1400);
}

export async function addToCart(productId, button) {
  const current = items.find((i) => i.productId === productId)?.quantity ?? 0;
  if (current >= MAX_QUANTITY) return flash(button, "Limite atingido");
  button.disabled = true;
  try {
    flash(button, (await setQuantity(productId, current + 1)) ? "Adicionado!" : "Não deu, tente de novo");
  } catch (error) {
    if (!(error instanceof SessionExpired)) {
      console.error(error);
      flash(button, "Sem conexão");
    }
  } finally {
    button.disabled = false;
  }
}

// ---------- Painel lateral ----------

function showView(name) {
  for (const [key, el] of Object.entries(views)) el.hidden = key !== name;
}

function lineFor(productId) {
  return [...lines.children].find((li) => li.dataset.productId === productId);
}

function renderTotal() {
  const total = items.reduce((sum, i) => sum + (products.get(i.productId)?.price ?? 0) * i.quantity, 0);
  totalEl.textContent = money(total);
}

function fillLine(li, item) {
  const product = products.get(item.productId);
  li.querySelector(".stepper-value").textContent = String(item.quantity);
  li.querySelector('[data-action="dec"]').disabled = item.quantity <= 1;
  li.querySelector('[data-action="inc"]').disabled = item.quantity >= MAX_QUANTITY;
  li.querySelector(".cart-line-subtotal").textContent = product ? money(product.price * item.quantity) : "";
}

function renderLine(item) {
  const li = lineTemplate.content.firstElementChild.cloneNode(true);
  const product = products.get(item.productId);
  const name = product?.name ?? item.productId;
  li.dataset.productId = item.productId;

  // textContent (nunca innerHTML): nada do backend vira HTML.
  const img = li.querySelector(".cart-line-img");
  if (product?.imageUrl) img.src = product.imageUrl;
  else img.remove();
  li.querySelector(".cart-line-name").textContent = name;
  li.querySelector(".cart-line-price").textContent = product ? `${money(product.price)} cada` : "Produto indisponível";
  li.querySelector(".stepper").setAttribute("aria-label", `Quantidade de ${name}`);
  li.querySelector('[data-action="dec"]').setAttribute("aria-label", `Diminuir quantidade de ${name}`);
  li.querySelector('[data-action="inc"]').setAttribute("aria-label", `Aumentar quantidade de ${name}`);
  li.querySelector('[data-action="remove"]').setAttribute("aria-label", `Remover ${name} do carrinho`);
  fillLine(li, item);
  return li;
}

function renderDrawer() {
  hideMessage(message);
  hideMessage(emptyNotice);
  if (items.length === 0) return showView("empty");
  lines.replaceChildren(...items.map(renderLine));
  renderTotal();
  showView("items");
}

export async function openCart() {
  [products] = await Promise.all([getProductMap(), loadCart()]);
  renderDrawer();
  if (!drawer.open) drawer.showModal();
}

function setLineBusy(li, busy) {
  li.querySelectorAll("button").forEach((b) => { b.disabled = busy; });
  li.toggleAttribute("data-busy", busy);
}

async function handleLineAction(event) {
  const button = event.target.closest("[data-action]");
  const li = button?.closest(".cart-line");
  if (!button || !li || checkingOut) return;
  const productId = li.dataset.productId;
  const item = items.find((i) => i.productId === productId);
  if (!item) return;

  hideMessage(message);
  setLineBusy(li, true);
  try {
    if (button.dataset.action === "remove") {
      if (!(await removeItem(productId))) throw new Error("remove falhou");
      // Foco vai para a linha seguinte (ou anterior) para quem navega por teclado.
      const next = li.nextElementSibling ?? li.previousElementSibling;
      li.remove();
      if (items.length === 0) {
        showView("empty");
        drawer.querySelector("#cart-view-empty [data-close]").focus();
      } else {
        next?.querySelector('[data-action="remove"]').focus();
      }
    } else {
      const delta = button.dataset.action === "inc" ? 1 : -1;
      const quantity = Math.min(MAX_QUANTITY, Math.max(1, item.quantity + delta));
      if (!(await setQuantity(productId, quantity))) throw new Error("update falhou");
      const updated = items.find((i) => i.productId === productId);
      if (updated) fillLine(li, updated);
    }
    renderTotal();
  } catch (error) {
    if (error instanceof SessionExpired) return;
    console.error(error);
    showMessage(message, "Não deu para atualizar o carrinho. Tente de novo.");
  } finally {
    if (li.isConnected) {
      setLineBusy(li, false);
      const current = items.find((i) => i.productId === productId);
      if (current) fillLine(li, current); // reaplica os limites do +/-
      if (button.isConnected && !button.disabled) button.focus();
    }
  }
}

// ---------- Checkout ----------

function setCheckingOut(busy) {
  checkingOut = busy;
  checkoutButton.disabled = busy;
  checkoutButton.setAttribute("aria-busy", String(busy));
  checkoutButton.textContent = busy ? "Finalizando…" : "Finalizar compra";
  lines.querySelectorAll("button").forEach((b) => { b.disabled = busy; });
  if (!busy) [...lines.children].forEach((li) => {
    const item = items.find((i) => i.productId === li.dataset.productId);
    if (item) fillLine(li, item);
  });
}

function checkoutError(status, data) {
  const detail = typeof data?.detail === "string" ? data.detail : "";
  if (status === 409) {
    // O orders diz qual produto faltou: "estoque insuficiente para p-003"
    const id = detail.match(/para (\S+)$/)?.[1];
    const name = id && products.get(id)?.name;
    if (name) return `Produto sem estoque suficiente: ${name}. Ajuste a quantidade e tente de novo.`;
  }
  if (status === 503 && detail.includes("pendente")) {
    // Pagamento sem confirmação: tentar de novo poderia gerar outro pedido.
    return "Não conseguimos confirmar o pagamento. Seu pedido ficou pendente para conferência: não é preciso tentar de novo.";
  }
  return CHECKOUT_ERRORS[status] ?? "Algo deu errado. Tente novamente.";
}

async function checkout() {
  if (checkingOut) return;
  hideMessage(message);
  if (!getSession()) {
    // Só aqui o login é exigido. O carrinho fica igual: ao entrar, ele é
    // juntado à conta e o painel continua aberto para finalizar.
    pendingCheckout = true;
    openLogin("Entre na sua conta para finalizar a compra. Seu carrinho continua o mesmo.");
    return;
  }
  setCheckingOut(true);
  try {
    const response = await authFetch(ORDERS_URL, { method: "POST" });
    const data = await readJSON(response);
    if (response.status === 201 && data) {
      items = [];
      renderCount({ animate: true });
      document.getElementById("success-order-id").textContent = data.orderId;
      document.getElementById("success-total").textContent = money(data.total); // total oficial do orders
      showView("success");
      document.getElementById("success-orders-button").focus();
      return;
    }
    const text = checkoutError(response.status, data);
    const pending = response.status === 503 && String(data?.detail ?? "").includes("pendente");
    if (response.status === 400 || pending) {
      // 400: o carrinho já estava vazio. Pendente: os itens foram para o pedido
      // em conferência e o carrinho foi esvaziado. Recarrega e avisa no painel vazio.
      await loadCart();
      renderDrawer();
      if (items.length === 0) return showMessage(emptyNotice, text);
    }
    showMessage(message, text);
  } catch (error) {
    if (error instanceof SessionExpired) return;
    console.error(error);
    showMessage(message, "Sem conexão com o servidor. Verifique sua internet e tente de novo.");
  } finally {
    setCheckingOut(false);
  }
}

// ---------- Eventos ----------

setupDialog(drawer);
// Fechou o login sem entrar: esquece que ia finalizar.
document.getElementById("auth-dialog").addEventListener("close", () => {
  if (!getSession()) pendingCheckout = false;
});
cartButton.addEventListener("click", openCart);
lines.addEventListener("click", handleLineAction);
checkoutButton.addEventListener("click", checkout);
document.getElementById("success-orders-button").addEventListener("click", () => {
  drawer.close();
  openOrders();
});

onSessionChange(async () => {
  const session = getSession();
  if (!session) {
    // Saiu (ou a sessão expirou): o carrinho continua igual na tela. Vira uma
    // cópia no navegador marcada com o dono; ao entrar de novo, ela vale.
    if (currentUserId) writeGuest(items, currentUserId);
    currentUserId = null;
    items = readGuest();
    renderCount();
    if (drawer.open) renderDrawer();
    return;
  }
  if (session.userId === currentUserId) return; // mesmo login (ex.: evento de outra aba)
  await syncGuestCart(session.userId);
  await loadCart();
  if (drawer.open) {
    renderDrawer();
    // Veio do "Finalizar compra": o carrinho está pronto, é só confirmar.
    if (pendingCheckout && items.length) checkoutButton.focus();
  }
  pendingCheckout = false;
});

// Ao abrir a página já logado, termina uma sincronização que tenha falhado antes.
(async () => {
  const session = getSession();
  if (session) await syncGuestCart(session.userId);
  await loadCart();
})();
