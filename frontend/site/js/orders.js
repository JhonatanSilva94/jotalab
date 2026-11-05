import { ORDERS_URL } from "./config.js";
import { authFetch, readJSON, SessionExpired } from "./api.js";
import { onSessionChange, getSession } from "./session.js";
import { money, setupDialog } from "./ui.js";

const dialog = document.getElementById("orders-dialog");
const list = document.getElementById("order-list");
const statusEl = document.getElementById("orders-status");
const template = document.getElementById("order-row-template");

// Rótulo e cor de cada status do service-orders.
const STATUS = {
  PAID: { label: "Pago", tone: "ok" },
  PAYMENT_FAILED: { label: "Pagamento recusado", tone: "fail" },
  PENDING: { label: "Em conferência", tone: "warn" },
  CANCELLED: { label: "Cancelado", tone: "muted" },
};

const dateFormat = new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" });

function renderRow(order) {
  const li = template.content.firstElementChild.cloneNode(true);
  const status = STATUS[order.status] ?? { label: order.status, tone: "muted" };
  li.querySelector(".order-id").textContent = `Pedido ${order.orderId}`;
  const date = new Date(order.createdAt);
  li.querySelector(".order-date").textContent = Number.isNaN(date.getTime()) ? "" : dateFormat.format(date);
  li.querySelector(".order-total").textContent = money(order.total);
  const badge = li.querySelector(".order-badge");
  badge.textContent = status.label;
  badge.dataset.tone = status.tone;
  return li;
}

export async function openOrders() {
  if (!getSession()) return;
  list.replaceChildren();
  statusEl.textContent = "Carregando pedidos…";
  if (!dialog.open) dialog.showModal();
  try {
    const response = await authFetch(ORDERS_URL);
    const orders = response.ok ? await readJSON(response) : null;
    if (!Array.isArray(orders)) throw new Error(`HTTP ${response.status}`);
    statusEl.textContent = orders.length ? "" : "Você ainda não fez nenhum pedido.";
    list.replaceChildren(...orders.map(renderRow)); // o orders já devolve do mais recente ao mais antigo
  } catch (error) {
    if (error instanceof SessionExpired) {
      dialog.close();
      return;
    }
    console.error(error);
    statusEl.textContent = "Não deu para carregar seus pedidos. Tente de novo em instantes.";
  }
}

setupDialog(dialog);
document.getElementById("orders-button").addEventListener("click", openOrders);
onSessionChange(() => {
  if (!getSession() && dialog.open) dialog.close();
});
