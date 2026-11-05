import { getProducts } from "./products.js";
import { addToCart } from "./cart.js";
import { money } from "./ui.js";
import "./auth.js";
import "./orders.js";

const grid = document.getElementById("product-grid");
const count = document.getElementById("product-count");
const template = document.getElementById("product-template");

// Monta o card com textContent (nunca innerHTML): nada que vem do backend
// é interpretado como HTML.
function renderCard(product, index) {
  const node = template.content.firstElementChild.cloneNode(true);
  node.style.setProperty("--i", index);

  const img = node.querySelector(".card-img");
  img.alt = product.name;
  img.src = product.imageUrl;

  node.querySelector(".card-series").textContent = product.series ?? "";
  node.querySelector(".card-name").textContent = product.name;
  node.querySelector(".card-price").textContent = money(product.price); // price vem em centavos

  const button = node.querySelector(".add-button");
  button.setAttribute("aria-label", `Adicionar ${product.name} ao carrinho`);
  button.dataset.productId = product.productId;

  return node;
}

function renderMessage(title, text) {
  const li = document.createElement("li");
  li.className = "grid-message";
  const h = document.createElement("p");
  h.className = "grid-message-title";
  h.textContent = title;
  const p = document.createElement("p");
  p.textContent = text;
  li.append(h, p);
  grid.replaceChildren(li);
}

async function init() {
  try {
    const products = await getProducts();
    if (products.length === 0) {
      renderMessage("Nenhum produto", "Nenhum produto cadastrado por enquanto.");
      count.textContent = "";
    } else {
      grid.replaceChildren(...products.map(renderCard));
      count.textContent = products.length === 1 ? "1 produto" : `${products.length} produtos`;
    }
  } catch (error) {
    console.error(error);
    renderMessage("Não deu pra carregar os produtos", "Tente recarregar a página em instantes.");
    count.textContent = "";
  } finally {
    grid.setAttribute("aria-busy", "false");
  }
}

// "Adicionar ao carrinho": um único ouvinte para todos os cards.
grid.addEventListener("click", (e) => {
  const button = e.target.closest(".add-button");
  if (button?.dataset.productId) addToCart(button.dataset.productId, button);
});

init();
