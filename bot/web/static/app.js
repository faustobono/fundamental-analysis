// Screener fundamental — front end sin framework ni build step.
//
// El servidor manda cada número ya etiquetado y con su formato, así que acá no
// hay reglas de negocio: sólo pintar.

const $ = (sel) => document.querySelector(sel);

const els = {
  form: $("#controls"),
  tickers: $("#tickers"),
  method: $("#method"),
  methodHint: $("#method-hint"),
  methodInfo: $("#method-info"),
  topn: $("#topn"),
  cache: $("#cache"),
  run: $("#run"),
  presets: $("#presets"),
  status: $("#status"),
  results: $("#results"),
};

// Los mega-caps de mayor volumen/importancia de EE.UU. — el mismo universo
// que se auto-corre al abrir la web (ver el final de este archivo). Sólo
// EE.UU. a propósito: hoy los CEDEARs fallan en producción contra FMP con un
// error de plan pago (HTTP 402, ver DECISIONS.md), así que meterlos acá haría
// que la primera carga muestre "sin datos" para varias empresas.
const TOP_VOLUME = "AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA, JPM";

const PRESETS = {
  "Top volumen": TOP_VOLUME,
  "Tech US": "AAPL, MSFT, NVDA, AMD, INTC, GOOGL, META",
  "Bancos AR": "GGAL.BA, BMA.BA, BBAR.BA, SUPV.BA",
  "Energía": "XOM, CVX, YPFD.BA, VIST.BA, PAMP.BA",
  "Mixto": "AAPL, MSFT, NVDA, JPM, BAC, XOM, CVX, GGAL.BA",
};

const METHOD_HINTS = {
  percentile: "Robusto a outliers",
  zscore: "Premia magnitud, no sólo orden",
};

// --- formato ----------------------------------------------------------------
// formatValue vive en format.js, compartido con brief.js.

function formatScore(metric, method) {
  return method === "percentile"
    ? `p${Math.round(metric.score * 100)}`
    : `z${metric.score >= 0 ? "+" : ""}${metric.score.toFixed(2)}`;
}

// --- render -----------------------------------------------------------------

function renderMetric(metric, method) {
  const row = document.createElement("div");
  row.className = "metric";
  row.innerHTML = `
    <span class="metric-label">
      <span class="metric-label-text">${metric.label} <span class="metric-dir">${metric.higher_is_better ? "↑" : "↓"}</span></span>
      ${infoButtonHTML(metric.name)}
    </span>
    <span class="metric-value"></span>
    <span class="bar-track"><span class="bar-fill"></span></span>
    <span class="metric-pct"></span>`;

  row.querySelector(".metric-value").textContent = formatValue(metric.raw, metric.format);
  const fill = row.querySelector(".bar-fill");
  fill.style.width = `${metric.bar * 100}%`;
  if (metric.bar < 0.34) fill.classList.add("low");

  row.querySelector(".metric-pct").textContent =
    `${formatScore(metric, method)}/${metric.peers}`;
  row.title = `${metric.label}: ${metric.higher_is_better ? "más es mejor" : "menos es mejor"} · ` +
    `${metric.peers} peer(s) del sector con dato`;
  return row;
}

function renderMissing(missing) {
  const row = document.createElement("div");
  row.className = "metric metric-missing";
  row.innerHTML = `
    <span class="metric-label">
      <span class="metric-label-text">${missing.label}</span>
      ${infoButtonHTML("missing_metric")}
    </span>
    <span class="metric-value">sin dato</span>
    <span class="bar-track"></span>
    <span class="metric-pct">—</span>`;
  return row;
}

function detailItem(label, value, isNa, infoKey) {
  return `<div class="detail-item">
    <dt>${label}${infoKey ? infoButtonHTML(infoKey) : ""}</dt>
    <dd class="${isNa ? "na" : ""}">${value}</dd>
  </div>`;
}

function renderDetail(score) {
  const snap = score.snapshot;
  const box = document.createElement("div");

  const tags = [];
  if (snap.is_cedear) {
    tags.push(
      `<span class="badge badge-info">vía ${snap.source_ticker}</span>${infoButtonHTML("cedear")}`
    );
  }
  if (snap.currency_mismatch) {
    tags.push(
      `<span class="badge badge-warn">${snap.currency} / ${snap.quote_currency}</span>` +
        infoButtonHTML("currency_mismatch")
    );
  }

  const ratios = snap.ratios
    .map((r) => detailItem(r.label, formatValue(r.value, r.format), r.value === null, r.name))
    .join("");

  const scale = [
    ["Market cap", snap.scale.market_cap, "money", null],
    ["Ingresos", snap.scale.revenue, "money", "revenue"],
    ["Free cash flow", snap.scale.free_cash_flow, "money", "free_cash_flow"],
    ["Deuda total", snap.scale.total_debt, "money", null],
    ["Patrimonio", snap.scale.total_equity, "money", null],
    ["Tasa efectiva", snap.scale.effective_tax_rate, "pct", "effective_tax_rate"],
  ]
    .map(([label, value, fmt, key]) => detailItem(label, formatValue(value, fmt), value === null, key))
    .join("");

  box.innerHTML = `
    <p class="meta-line">
      ${snap.industry ? `<span>${snap.industry}</span> ·` : ""}
      <span>cobertura ${Math.round(score.coverage * 100)}%${infoButtonHTML("coverage")}</span> ·
      <span>${snap.currency ?? "?"}</span> ·
      <span>traído ${new Date(snap.as_of).toLocaleString("es-AR")}</span>
      ${tags.length ? `· ${tags.join(" ")}` : ""}
    </p>
    <h4 class="detail-section-title">Ratios</h4>
    <dl class="detail-grid">${ratios}</dl>
    <h4 class="detail-section-title">Escala</h4>
    <dl class="detail-grid">${scale}</dl>
    ${snap.warnings.length
      ? `<h4 class="detail-section-title">Calidad del dato</h4>
         <ul class="warnings">${snap.warnings.map((w) => `<li>${w}</li>`).join("")}</ul>`
      : ""}`;
  return box;
}

function renderCard(score, method) {
  const card = document.getElementById("tpl-card").content.cloneNode(true).firstElementChild;
  card.dataset.rank = score.rank;

  card.querySelector(".rank").textContent = `#${score.rank}`;
  card.querySelector(".ticker").textContent = score.ticker;
  card.querySelector(".company").textContent = score.company_name ?? "";
  card.querySelector(".composite-value").textContent =
    method === "percentile" ? score.composite.toFixed(3) : score.composite.toFixed(2);
  card.querySelector(".composite-label").insertAdjacentHTML("beforeend", infoButtonHTML("composite_score"));

  const metrics = card.querySelector(".metrics");
  score.metrics.forEach((m) => metrics.appendChild(renderMetric(m, method)));
  score.missing.forEach((m) => metrics.appendChild(renderMissing(m)));

  // El detalle se arma recién al abrir: con 100 tickers, construirlo de entrada
  // sería trabajo tirado para tarjetas que nadie va a expandir.
  const head = card.querySelector(".card-head");
  const body = card.querySelector(".card-body");
  head.addEventListener("click", () => {
    const open = head.getAttribute("aria-expanded") === "true";
    head.setAttribute("aria-expanded", String(!open));
    body.hidden = open;
    if (!open && !body.dataset.filled) {
      body.appendChild(renderDetail(score));
      body.dataset.filled = "1";
    }
  });

  return card;
}

function renderSector(sector, method, topN) {
  const node = document.getElementById("tpl-sector").content.cloneNode(true).firstElementChild;
  node.querySelector(".sector-name").textContent = sector.sector;
  node.querySelector(".sector-count").textContent =
    `${sector.peer_count} empresa${sector.peer_count === 1 ? "" : "s"}`;

  if (sector.thin) {
    const badge = node.querySelector(".sector-thin");
    badge.hidden = false;
    badge.insertAdjacentHTML("afterend", infoButtonHTML("thin_sector"));
  }

  const cards = node.querySelector(".cards");
  const shown = topN > 0 ? sector.ranked.slice(0, topN) : sector.ranked;
  shown.forEach((score) => cards.appendChild(renderCard(score, method)));

  if (sector.unrankable.length) {
    const box = node.querySelector(".unrankable");
    box.hidden = false;
    box.querySelector("ul").innerHTML = sector.unrankable
      .map((u) => `<li><code>${u.ticker}</code>${u.reason}</li>`)
      .join("");
  }

  return node;
}

function renderFailures(failures) {
  if (!failures.length) return null;
  const box = document.createElement("div");
  box.className = "failures";
  box.innerHTML = `
    <h3>${failures.length} ticker(s) sin datos</h3>
    <ul>${failures.map((f) => `<li><code>${f.ticker}</code>${f.reason}</li>`).join("")}</ul>`;
  return box;
}

function render(payload) {
  const method = payload.meta.method;
  const topN = Number(els.topn.value);
  els.results.replaceChildren();

  const withData = payload.sectors.filter((s) => s.ranked.length || s.unrankable.length);
  if (!withData.length) {
    els.results.innerHTML = `<p class="empty">No quedó ninguna empresa rankeable.</p>`;
  } else {
    withData.forEach((s) => els.results.appendChild(renderSector(s, method, topN)));
  }

  const failures = renderFailures(payload.failures);
  if (failures) els.results.appendChild(failures);
}

// --- status -----------------------------------------------------------------

function showLoading(count) {
  els.status.hidden = false;
  els.status.className = "status";
  els.status.innerHTML =
    `<span class="spinner"></span>
     <span>Trayendo <b>${count}</b> ticker(s)… la primera vez tarda; después sale de cache.</span>
     <span id="elapsed"></span>`;

  const started = Date.now();
  return setInterval(() => {
    const el = $("#elapsed");
    if (el) el.textContent = `${((Date.now() - started) / 1000).toFixed(0)}s`;
  }, 250);
}

function showSummary(meta) {
  els.status.className = "status";
  els.status.innerHTML =
    `<span><b>${meta.ok}</b> con datos</span>` +
    (meta.failed ? ` <span>· <b>${meta.failed}</b> sin datos</span>` : "") +
    ` <span>· <b>${meta.cache_hits}</b> de cache</span>` +
    ` <span>· ${(meta.elapsed_ms / 1000).toFixed(1)}s</span>` +
    ` <span>· ${meta.metrics.map((m) => m.label).join(", ")}</span>`;
}

function showError(message) {
  els.status.hidden = false;
  els.status.className = "status error";
  els.status.textContent = message;
}

// --- eventos ----------------------------------------------------------------

let lastPayload = null;

async function runScreen(event) {
  event?.preventDefault();
  const tickers = els.tickers.value.trim();
  if (!tickers) {
    showError("Escribí al menos un ticker.");
    return;
  }

  const params = new URLSearchParams({
    tickers,
    method: els.method.value,
    cache: els.cache.checked ? "1" : "0",
  });

  els.run.disabled = true;
  const timer = showLoading(tickers.split(/[\s,;]+/).filter(Boolean).length);

  try {
    const response = await fetch(`/api/screen?${params}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error ?? `HTTP ${response.status}`);

    lastPayload = payload;
    localStorage.setItem("tickers", tickers);
    render(payload);
    showSummary(payload.meta);
  } catch (error) {
    showError(`No se pudo analizar: ${error.message}`);
    els.results.replaceChildren();
  } finally {
    clearInterval(timer);
    els.run.disabled = false;
  }
}

// El top-N es un filtro de vista: re-renderizar no cuesta un fetch nuevo.
els.topn.addEventListener("change", () => lastPayload && render(lastPayload));

function updateMethodInfo() {
  els.methodHint.textContent = METHOD_HINTS[els.method.value];
  els.methodInfo.innerHTML = infoButtonHTML(
    els.method.value === "percentile" ? "percentile_method" : "zscore_method"
  );
}

els.method.addEventListener("change", updateMethodInfo);

els.form.addEventListener("submit", runScreen);

els.tickers.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") runScreen(e);
});

Object.entries(PRESETS).forEach(([name, tickers]) => {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = name;
  button.addEventListener("click", () => {
    els.tickers.value = tickers;
    els.tickers.focus();
  });
  els.presets.appendChild(button);
});

els.tickers.value = localStorage.getItem("tickers") ?? TOP_VOLUME;
updateMethodInfo();

// Auto-análisis al abrir: el objetivo es que la página ya tenga resultados
// sin que haga falta escribir nada ni apretar "Analizar" — con los tickers
// más importantes por default, o con la última búsqueda si ya usaste la web.
runScreen();
