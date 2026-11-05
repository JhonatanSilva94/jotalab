import { PRODUCTS_URL } from "./config.js";

// Uma única busca ao catálogo, compartilhada pela vitrine e pelo carrinho
// (o service-cart só guarda productId e quantity).
let pending = null;

async function fetchProducts() {
  const response = await fetch(PRODUCTS_URL, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`HTTP ${response.status} ao buscar ${PRODUCTS_URL}`);
  const data = await response.json();
  if (!Array.isArray(data)) throw new Error("Resposta de produtos não é uma lista");
  return data;
}

/** Lista de produtos (cacheada). Se falhar, a próxima chamada tenta de novo. */
export function getProducts() {
  pending ??= fetchProducts().catch((error) => {
    pending = null;
    throw error;
  });
  return pending;
}

/** Map productId -> produto; vazio se o catálogo estiver fora do ar. */
export async function getProductMap() {
  try {
    return new Map((await getProducts()).map((p) => [p.productId, p]));
  } catch (error) {
    console.error(error);
    return new Map();
  }
}
