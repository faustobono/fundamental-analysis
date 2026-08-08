// Botón "i" de info + popover. Un solo listener delegado en `document`, no uno
// por botón — el resto del front arma casi todo con `innerHTML` (strings), así
// que el botón se inserta como HTML (`infoButtonHTML(key)`) en vez de requerir
// un nodo DOM que alguien tenga que engancharle un listener a mano.
//
// Envuelto en un IIFE por la misma razón que brief.js y tabs.js: evitar chocar
// `const`/`let` de nivel de script con los otros archivos.

(function () {
  const POPOVER_ID = "info-popover";
  const MARGIN = 8;
  let currentTrigger = null;

  function ensurePopover() {
    let el = document.getElementById(POPOVER_ID);
    if (el) return el;

    el = document.createElement("div");
    el.id = POPOVER_ID;
    el.className = "info-popover";
    el.hidden = true;
    el.setAttribute("role", "dialog");
    el.innerHTML = `
      <button type="button" class="info-popover-close" aria-label="Cerrar">×</button>
      <h4 class="info-popover-title"></h4>
      <p class="info-popover-body"></p>`;
    document.body.appendChild(el);
    el.querySelector(".info-popover-close").addEventListener("click", closePopover);
    return el;
  }

  function closePopover() {
    const el = document.getElementById(POPOVER_ID);
    if (!el) return;
    el.hidden = true;
    delete el.dataset.openFor;
    currentTrigger = null;
  }

  /** Reposiciona si está abierto, en vez de cerrarlo — un scroll de un par de
   * píxeles (o el scroll-into-view de un click automatizado) no debería
   * hacer desaparecer algo que el usuario recién abrió. Si el botón que lo
   * abrió ya no está en el DOM (se re-renderizó la sección), ahí sí se cierra:
   * no hay contra qué reposicionar. */
  function repositionOrClose() {
    const el = document.getElementById(POPOVER_ID);
    if (!el || el.hidden) return;
    if (!currentTrigger || !currentTrigger.isConnected) {
      closePopover();
      return;
    }
    positionPopover(el, currentTrigger);
  }

  function positionPopover(el, trigger) {
    const rect = trigger.getBoundingClientRect();
    const popW = el.offsetWidth;
    const popH = el.offsetHeight;

    let left = rect.left + window.scrollX;
    const maxLeft = window.scrollX + document.documentElement.clientWidth - popW - MARGIN;
    left = Math.max(window.scrollX + MARGIN, Math.min(left, maxLeft));

    let top = rect.bottom + window.scrollY + MARGIN;
    // Si no entra abajo del botón, se muestra arriba.
    if (rect.bottom + MARGIN + popH > window.scrollY + window.innerHeight) {
      top = rect.top + window.scrollY - popH - MARGIN;
    }

    el.style.left = `${left}px`;
    el.style.top = `${top}px`;
  }

  function openPopover(trigger, key) {
    const entry = typeof glossaryEntry === "function" ? glossaryEntry(key) : null;
    if (!entry) return;

    const el = ensurePopover();
    if (el.dataset.openFor === key && !el.hidden) {
      // Mismo botón clickeado de nuevo: toggle a cerrado.
      closePopover();
      return;
    }

    el.querySelector(".info-popover-title").textContent = entry.titulo;
    el.querySelector(".info-popover-body").textContent = entry.texto;
    el.dataset.openFor = key;
    el.hidden = false;
    currentTrigger = trigger;
    positionPopover(el, trigger);
  }

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest(".info-btn");
    if (trigger) {
      event.preventDefault();
      event.stopPropagation();
      openPopover(trigger, trigger.dataset.infoKey);
      return;
    }
    const popover = document.getElementById(POPOVER_ID);
    if (popover && !popover.hidden && !popover.contains(event.target)) {
      closePopover();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closePopover();
  });

  window.addEventListener("resize", repositionOrClose);
  window.addEventListener("scroll", repositionOrClose, true);

  /** HTML de un botón "i" para insertar dentro de un template string. */
  window.infoButtonHTML = function infoButtonHTML(key) {
    const entry = typeof GLOSSARY !== "undefined" ? GLOSSARY[key] : null;
    if (!entry) {
      if (key) console.warn(`glosario: falta la entrada "${key}"`);
      return "";
    }
    return (
      `<button type="button" class="info-btn" data-info-key="${key}" ` +
      `aria-label="Info: ${entry.titulo}">i</button>`
    );
  };
})();
