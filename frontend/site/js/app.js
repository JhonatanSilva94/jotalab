import { PRODUCTS_URL } from "./config.js";

const grid = document.getElementById("product-grid");
const count = document.getElementById("product-count");
const template = document.getElementById("product-template");

const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });

async function fetchProducts() {
  const response = await fetch(PRODUCTS_URL, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`HTTP ${response.status} ao buscar ${PRODUCTS_URL}`);
  const data = await response.json();
  if (!Array.isArray(data)) throw new Error("Resposta de produtos não é uma lista");
  return data;
}

// Monta o card com textContent (nunca innerHTML): quando os dados vierem
// da API, nada que vier do backend é interpretado como HTML.
function renderCard(product, index) {
  const node = template.content.firstElementChild.cloneNode(true);
  node.style.setProperty("--i", index);

  const img = node.querySelector(".card-img");
  img.alt = product.name;
  img.src = product.imageUrl;

  node.querySelector(".card-series").textContent = product.series ?? "";
  node.querySelector(".card-name").textContent = product.name;
  node.querySelector(".card-price").textContent = brl.format(product.price / 100) // price vem em centavos;

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
    const products = await fetchProducts();
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

init();
