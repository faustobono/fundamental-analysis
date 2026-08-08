// Navegación entre vistas. Sin router: dos <main> en la misma página, se
// alterna cuál está hidden. El hash de la URL persiste la pestaña activa para
// que un refresh o un link directo abran donde corresponde.

(function () {
  const tabs = [...document.querySelectorAll("#tabs .tab")];
  const views = {
    screener: document.getElementById("view-screener"),
    brief: document.getElementById("view-brief"),
  };

  function activate(name) {
    if (!views[name]) name = "screener";
    for (const [key, el] of Object.entries(views)) el.hidden = key !== name;
    for (const tab of tabs) {
      const active = tab.dataset.view === name;
      tab.setAttribute("aria-current", active ? "page" : "false");
    }
    if (location.hash.slice(1) !== name) history.replaceState(null, "", `#${name}`);
  }

  tabs.forEach((tab) => tab.addEventListener("click", () => activate(tab.dataset.view)));
  window.addEventListener("hashchange", () => activate(location.hash.slice(1)));

  activate(location.hash.slice(1) || "screener");
})();
