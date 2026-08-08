// Análisis profundo de una empresa — mismas tablas que `bot brief --data-only`,
// pero pintadas acá en vez de tener que pegarlas en un chat. No llama a
// ningún LLM: todo lo que se ve salió de `/api/brief`, que es determinístico.
//
// Envuelto en un IIFE porque `format.js` y `app.js` ya declaran globals de
// nivel de script (`formatValue`, `formatMoney`); un `const` de nivel superior
// acá chocaría con esos.

(function () {
  const $ = (sel) => document.querySelector(sel);

  const els = {
    form: $("#brief-controls"),
    ticker: $("#brief-ticker"),
    years: $("#brief-years"),
    run: $("#brief-run"),
    presets: $("#brief-presets"),
    status: $("#brief-status"),
    results: $("#brief-results"),
  };

  const PRESETS = ["AAPL", "MSFT", "NVDA", "GGAL.BA", "YPFD.BA", "JPM"];

  const VERDICT_CLASS = { barato: "badge-good", caro: "badge-bad", justo: "badge-info" };

  // --- helpers de render -----------------------------------------------------

  function scrollWrap(table) {
    const wrap = document.createElement("div");
    wrap.className = "table-scroll";
    wrap.appendChild(table);
    return wrap;
  }

  function seriesTable(section) {
    if (!section.years.length) {
      const p = document.createElement("p");
      p.className = "empty-inline";
      p.textContent = "Sin ejercicios disponibles.";
      return p;
    }
    const table = document.createElement("table");
    table.className = "data-table";
    table.innerHTML =
      `<thead><tr><th>Ejercicio</th>${section.rows
        .map((r) => `<th>${r.label}${infoButtonHTML(r.name)}</th>`)
        .join("")}</tr></thead>`;
    const tbody = document.createElement("tbody");
    section.years.forEach((year, i) => {
      const cells = section.rows
        .map((r) => `<td>${formatValue(r.values[i], r.format)}</td>`)
        .join("");
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${year ?? "?"}</td>${cells}`;
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    return scrollWrap(table);
  }

  function statLine(label, value) {
    const span = document.createElement("span");
    span.className = "stat";
    span.innerHTML = `<span class="stat-label">${label}</span><span class="stat-value">${value}</span>`;
    return span;
  }

  function summaryStrip(items) {
    const box = document.createElement("div");
    box.className = "stat-strip";
    items.forEach(([label, value]) => box.appendChild(statLine(label, value)));
    return box;
  }

  function section(title, note) {
    const box = document.createElement("section");
    box.className = "brief-section";
    const h = document.createElement("h3");
    h.textContent = title;
    box.appendChild(h);
    if (note) {
      const p = document.createElement("p");
      p.className = "section-note";
      p.textContent = note;
      box.appendChild(p);
    }
    return box;
  }

  // --- secciones ---------------------------------------------------------

  function renderIdentity(data) {
    const id = data.identity;
    const box = section("Identidad");
    const tags = [];
    if (id.is_cedear) {
      tags.push(`<span class="badge badge-info">vía ${id.source_ticker}</span>${infoButtonHTML("cedear")}`);
    }
    if (id.currency_mismatch) {
      tags.push(
        `<span class="badge badge-warn">${id.currency} / ${id.quote_currency}</span>` +
          infoButtonHTML("currency_mismatch")
      );
    }
    box.insertAdjacentHTML(
      "beforeend",
      `<p class="identity-line">
        <strong>${id.company_name ?? id.ticker}</strong> (<code>${id.ticker}</code>)
        ${tags.join(" ")}
      </p>
      <p class="meta-line">
        ${id.sector ?? "—"} / ${id.industry ?? "—"} ·
        balance en ${id.currency ?? "—"} · cotiza en ${id.quote_currency ?? "—"} ·
        ${id.years_available} ejercicio(s) disponibles ·
        traído ${new Date(id.as_of).toLocaleString("es-AR")}
      </p>`
    );
    return box;
  }

  function renderProfitability(data) {
    const box = section(
      "Rentabilidad y calidad",
      "ROIC ≈ EBIT × (1 − tasa efectiva) / (deuda + patrimonio). Aproximación para comparar entre empresas, no un WACC-vs-ROIC de valuación."
    );
    box.appendChild(seriesTable(data.profitability));
    const s = data.summary;
    box.appendChild(
      summaryStrip([
        [`ROIC promedio${infoButtonHTML("roic")}`, formatValue(s.roic_avg, "pct")],
        [`Tendencia ROIC${infoButtonHTML("roic")}`, formatValue(s.roic_trend, "pct")],
        [`Tendencia margen operativo${infoButtonHTML("operating_margin")}`, formatValue(s.operating_margin_trend, "pct")],
        [`Tendencia margen neto${infoButtonHTML("net_margin")}`, formatValue(s.net_margin_trend, "pct")],
      ])
    );
    return box;
  }

  function renderGrowth(data) {
    const box = section("Crecimiento");
    box.appendChild(seriesTable(data.growth));
    const s = data.summary;
    const items = [
      [`CAGR ingresos${infoButtonHTML("cagr_revenue")}`, formatValue(s.revenue_cagr, "pct")],
      [`CAGR EPS diluido${infoButtonHTML("cagr_eps")}`, formatValue(s.eps_cagr, "pct")],
      [`CAGR FCF${infoButtonHTML("cagr_fcf")}`, formatValue(s.fcf_cagr, "pct")],
    ];
    if (s.next_year_revenue_growth !== undefined) {
      items.push([
        `Consenso próx. ejercicio (${s.analyst_count ?? "—"} analistas)${infoButtonHTML("analyst_consensus")}`,
        `ingresos ${formatValue(s.next_year_revenue_growth, "pct")} · beneficios ${formatValue(s.next_year_earnings_growth, "pct")}`,
      ]);
    }
    box.appendChild(summaryStrip(items));
    return box;
  }

  function renderHealth(data) {
    const box = section("Salud financiera");
    box.appendChild(seriesTable(data.health));
    return box;
  }

  function renderValuation(data) {
    const box = section(
      "Valuación: actual vs. su propia historia",
      "Múltiplos sobre el último ejercicio anual (no TTM), cruzados con precios cuyo balance ya era público en esa fecha — sin esto se valuaría con información que todavía no existía."
    );
    const v = data.valuation;
    if (!v) {
      box.insertAdjacentHTML(
        "beforeend",
        `<p class="empty-inline">Sin múltiplos de valuación — ver "Lo que no está" abajo para el motivo.</p>`
      );
      return box;
    }
    const table = document.createElement("table");
    table.className = "data-table";
    table.innerHTML = `<thead><tr>
      <th>Múltiplo</th><th>Actual</th><th>Mediana</th><th>p25</th><th>p75</th>
      <th>Percentil${infoButtonHTML("valuation_percentile")}</th>
      <th>vs mediana${infoButtonHTML("valuation_vs_median")}</th>
      <th>Lectura${infoButtonHTML("valuation_verdict")}</th>
    </tr></thead>`;
    const tbody = document.createElement("tbody");
    v.bands.forEach((b) => {
      const pct = b.percentile === null ? "—" : `p${Math.round(b.percentile * 100)}`;
      const vsMedian = b.vs_median === null ? "—" : `${b.vs_median >= 0 ? "+" : ""}${(b.vs_median * 100).toFixed(0)}%`;
      const verdictClass = VERDICT_CLASS[b.verdict] ?? "badge-info";
      const verdict = b.verdict ? `<span class="badge ${verdictClass}">${b.verdict}</span>` : "—";
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${b.label}${infoButtonHTML(b.metric)}</td>
        <td>${formatValue(b.current, b.format)}</td>
        <td>${formatValue(b.median, b.format)}</td>
        <td>${formatValue(b.p25, b.format)}</td>
        <td>${formatValue(b.p75, b.format)}</td>
        <td>${pct}</td>
        <td>${vsMedian}</td>
        <td>${verdict}</td>`;
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    box.appendChild(scrollWrap(table));
    box.insertAdjacentHTML(
      "beforeend",
      `<p class="section-note">${v.observations} observaciones mensuales · rezago de publicación ${v.lag_days} días.</p>`
    );
    return box;
  }

  function renderCostOfCapital(data) {
    const box = section(
      "Componentes del costo de capital",
      "El WACC no está calculado a propósito: exige tasa libre de riesgo y equity risk premium, que son supuestos de mercado y envejecen en silencio."
    );
    const c = data.cost_of_capital;
    if (!c) {
      box.insertAdjacentHTML("beforeend", `<p class="empty-inline">Sin datos.</p>`);
      return box;
    }
    const debtLabel =
      (c.costo_de_deuda_ejercicio
        ? `Costo de deuda (histórico) — ejercicio ${c.costo_de_deuda_ejercicio}`
        : "Costo de deuda (histórico)") + infoButtonHTML("cost_of_debt");
    box.appendChild(
      summaryStrip([
        [`Beta${infoButtonHTML("beta")}`, formatValue(c.beta, "num")],
        [debtLabel, formatValue(c.costo_de_deuda, "pct")],
        [`Tasa impositiva efectiva${infoButtonHTML("effective_tax_rate")}`, formatValue(c.tasa_impositiva_efectiva, "pct")],
        [`Peso deuda / equity${infoButtonHTML("capital_weights")}`, `${formatValue(c.peso_deuda, "pct")} / ${formatValue(c.peso_equity, "pct")}`],
        ["Market cap", formatValue(c.market_cap, "money")],
        ["Deuda total", formatValue(c.deuda_total, "money")],
      ])
    );
    return box;
  }

  const ALTMAN_ZONE_CLASS = { segura: "badge-good", gris: "badge-info", riesgo: "badge-bad" };

  function renderFinancialStrength(data) {
    const box = section(
      "Fortaleza financiera y riesgo",
      "Altman Z-Score estima riesgo de quiebra; Piotroski F-Score mide fortaleza fundamental año contra año. Dos modelos de screening clásicos, no un diagnóstico definitivo."
    );
    const fs = data.financial_strength;
    if (!fs || (fs.altman_z_score === null && fs.piotroski_score === null)) {
      box.insertAdjacentHTML("beforeend", `<p class="empty-inline">Sin datos suficientes para calcularlos.</p>`);
      return box;
    }

    const items = [];
    if (fs.altman_z_score !== null) {
      const zoneClass = ALTMAN_ZONE_CLASS[fs.altman_zone] ?? "badge-info";
      items.push([
        `Altman Z-Score${infoButtonHTML("altman_z_score")}`,
        `${fs.altman_z_score.toFixed(2)} <span class="badge ${zoneClass}">${fs.altman_zone}</span>`,
      ]);
    }
    if (fs.piotroski_score !== null) {
      items.push([
        `Piotroski F-Score${infoButtonHTML("piotroski_f_score")}`,
        `${fs.piotroski_score}/${fs.piotroski_max_score}`,
      ]);
    }
    box.appendChild(summaryStrip(items));

    if (fs.piotroski_criteria && fs.piotroski_criteria.length) {
      const ul = document.createElement("ul");
      ul.className = "piotroski-checklist";
      fs.piotroski_criteria.forEach((c) => {
        const li = document.createElement("li");
        const mark = c.cumplido === true ? "✓" : c.cumplido === false ? "✗" : "—";
        li.className = c.cumplido === true ? "ok" : c.cumplido === false ? "bad" : "na";
        li.textContent = `${mark} ${c.etiqueta}`;
        ul.appendChild(li);
      });
      box.appendChild(ul);
    }
    return box;
  }

  function renderGaps(data) {
    if (!data.gaps.length && !data.warnings.length) return null;
    const box = section("Lo que no está / calidad del dato");
    const ul = document.createElement("ul");
    ul.className = "warnings";
    data.gaps.forEach((g) => {
      const li = document.createElement("li");
      li.className = "gap";
      li.textContent = g;
      ul.appendChild(li);
    });
    data.warnings.forEach((w) => {
      const li = document.createElement("li");
      li.textContent = w;
      ul.appendChild(li);
    });
    box.appendChild(ul);
    return box;
  }

  function render(data) {
    els.results.replaceChildren(
      renderIdentity(data),
      renderProfitability(data),
      renderGrowth(data),
      renderHealth(data),
      renderValuation(data),
      renderCostOfCapital(data),
      renderFinancialStrength(data),
      renderGaps(data)
    );
  }

  // --- status --------------------------------------------------------------

  function showLoading(ticker) {
    els.status.hidden = false;
    els.status.className = "status";
    els.status.innerHTML = `<span class="spinner"></span><span>Trayendo 5 años de balances y precios de <b>${ticker}</b>…</span>`;
  }

  function showSummary(data) {
    els.status.className = "status";
    const id = data.identity;
    els.status.innerHTML =
      `<span><b>${id.years_available}</b> ejercicio(s)</span>` +
      (data.valuation ? ` · <span><b>${data.valuation.observations}</b> observaciones de precio</span>` : "") +
      (data.gaps.length ? ` · <span>${data.gaps.length} dato(s) faltante(s)</span>` : "");
  }

  function showError(message) {
    els.status.hidden = false;
    els.status.className = "status error";
    els.status.textContent = message;
  }

  // --- eventos ---------------------------------------------------------------

  async function run(event) {
    event?.preventDefault();
    const ticker = els.ticker.value.trim().toUpperCase();
    if (!ticker) {
      showError("Escribí un ticker.");
      return;
    }

    els.run.disabled = true;
    showLoading(ticker);

    try {
      const params = new URLSearchParams({ ticker, years: els.years.value });
      const response = await fetch(`/api/brief?${params}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error ?? `HTTP ${response.status}`);

      localStorage.setItem("brief_ticker", ticker);
      render(data);
      showSummary(data);
    } catch (error) {
      showError(`No se pudo analizar ${ticker}: ${error.message}`);
      els.results.replaceChildren();
    } finally {
      els.run.disabled = false;
    }
  }

  els.form.addEventListener("submit", run);

  PRESETS.forEach((ticker) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = ticker;
    button.addEventListener("click", () => {
      els.ticker.value = ticker;
      run();
    });
    els.presets.appendChild(button);
  });

  // Auto-análisis al abrir, igual que el screener: la pestaña ya arranca con
  // un informe cargado (AAPL por default) en vez de un formulario vacío.
  els.ticker.value = localStorage.getItem("brief_ticker") ?? "AAPL";
  run();
})();
