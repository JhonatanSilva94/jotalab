// Pequenas peças de interface usadas por mais de um módulo.

export const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });

/** Centavos -> "R$ 129,90" */
export const money = (cents) => brl.format(cents / 100);

/**
 * Fecha o <dialog> num clique no fundo escurecido. O clique precisa COMEÇAR
 * no fundo: arrastar uma seleção de texto e soltar fora do painel dispara o
 * "click" no <dialog> (ancestral comum) e isso não pode fechar nada.
 * Também liga todos os botões [data-close] de dentro dele.
 */
export function setupDialog(dialog) {
  let pressStartedOnBackdrop = false;
  dialog.addEventListener("pointerdown", (e) => {
    pressStartedOnBackdrop = e.target === dialog;
  });
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog && pressStartedOnBackdrop) dialog.close();
    pressStartedOnBackdrop = false;
  });
  dialog.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => dialog.close()));
}

/**
 * Mostra uma mensagem num elemento [hidden]. Esconde e mostra de novo para a
 * entrada (@starting-style) tocar também quando o mesmo texto se repete:
 * o usuário vê que a nova tentativa teve resposta.
 */
export function showMessage(el, text) {
  el.hidden = true;
  el.textContent = text;
  void el.offsetWidth;
  el.hidden = false;
}

export function hideMessage(el) {
  el.hidden = true;
  el.textContent = "";
}

const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

/** Pequeno "pulo" de feedback (ex.: contador do carrinho mudou). */
export function bump(el) {
  if (reduceMotion.matches) return;
  el.animate(
    [{ transform: "scale(1)" }, { transform: "scale(1.25)" }, { transform: "scale(1)" }],
    { duration: 260, easing: "cubic-bezier(0.23, 1, 0.32, 1)" },
  );
}
