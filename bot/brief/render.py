"""Render del bloque DATOS: las métricas de Capa 1 como tablas markdown.

Se elige markdown y no JSON porque el destino es un chat: una tabla de márgenes
por año se lee de un vistazo y consume menos tokens que el mismo dato anidado
en llaves.
"""

from __future__ import annotations

from datetime import date
from typing import Callable, Optional, Sequence

from ..analysis.profile import CompanyProfile
from ..analysis.series import FinancialHistory
from ..analysis.valuation import ValuationProfile

DASH = "—"


# --- formato ----------------------------------------------------------------


def pct(value: Optional[float], decimals: int = 1) -> str:
    return DASH if value is None else f"{value * 100:.{decimals}f}%"


def times(value: Optional[float]) -> str:
    return DASH if value is None else f"{value:.2f}×"


def num(value: Optional[float], decimals: int = 2) -> str:
    return DASH if value is None else f"{value:,.{decimals}f}"


def money(value: Optional[float]) -> str:
    """Montos en escala legible: 4.57 B, no 4572794322944."""
    if value is None:
        return DASH
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if magnitude >= limit:
            return f"{sign}{magnitude / limit:.2f} {suffix}"
    return f"{sign}{magnitude:.0f}"


def _year(period_end: Optional[date]) -> str:
    return str(period_end.year) if period_end else "?"


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    if not rows:
        return "_sin datos_\n"
    head = "| " + " | ".join(headers) + " |"
    sep = "|" + "|".join("---" for _ in headers) + "|"
    body = "\n".join("| " + " | ".join(r) + " |" for r in rows)
    return f"{head}\n{sep}\n{body}\n"


def _by_year(
    history: FinancialHistory,
    metrics: Sequence[tuple[str, str, Callable[[Optional[float]], str]]],
) -> str:
    """Tabla con un año por fila y una métrica por columna."""
    periods = list(reversed(history.periods))
    if not periods:
        return "_sin datos_\n"

    rows = []
    for period in periods:
        row = [_year(period.period_end)]
        for name, _, formatter in metrics:
            value = period.roic() if name == "roic" else getattr(period, name, None)
            row.append(formatter(value))
        rows.append(row)
    return _table(["Ejercicio", *(label for _, label, _ in metrics)], rows)


# --- secciones --------------------------------------------------------------


def render_identity(profile: CompanyProfile) -> str:
    s = profile.snapshot
    lines = [
        f"- **Empresa**: {s.company_name or DASH} (`{s.ticker}`)",
        f"- **Sector / industria**: {s.sector or DASH} / {s.industry or DASH}",
        f"- **Moneda del balance**: {s.currency or DASH}",
        f"- **Moneda de cotización**: {s.quote_currency or DASH}",
        f"- **Ejercicios disponibles**: {profile.years}",
        f"- **Datos traídos**: {s.as_of.strftime('%Y-%m-%d %H:%M UTC')}",
    ]
    if s.is_cedear:
        lines.append(
            f"- **⚠ CEDEAR/ADR**: los fundamentals son del subyacente `{s.source_ticker}`, "
            f"no del listado local `{s.ticker}`."
        )
    if s.has_currency_mismatch:
        lines.append(
            f"- **⚠ Monedas mixtas**: balance en {s.currency}, cotización en {s.quote_currency}."
        )
    return "\n".join(lines) + "\n"


def render_profitability(profile: CompanyProfile) -> str:
    table = _by_year(
        profile.history,
        [
            ("roic", "ROIC", pct),
            ("roe", "ROE", pct),
            ("gross_margin", "M. bruto", pct),
            ("operating_margin", "M. operativo", pct),
            ("net_margin", "M. neto", pct),
            ("effective_tax_rate", "Tasa efectiva", pct),
        ],
    )
    h = profile.history
    resumen = [
        f"- ROIC promedio del período: **{pct(h.average('roic'))}**",
        f"- Tendencia del ROIC (última − primera): **{pct(h.trend('roic'))}**",
        f"- Tendencia del margen operativo: **{pct(h.trend('operating_margin'))}**",
        f"- Tendencia del margen neto: **{pct(h.trend('net_margin'))}**",
    ]
    return table + "\n" + "\n".join(resumen) + "\n"


def render_growth(profile: CompanyProfile) -> str:
    h = profile.history
    table = _by_year(
        h,
        [
            ("revenue", "Ingresos", money),
            ("eps_diluted", "EPS diluido", lambda v: num(v, 2)),
            ("free_cash_flow", "FCF", money),
            ("fcf_after_sbc", "FCF − SBC", money),
            ("sbc_to_revenue", "SBC / ingresos", pct),
        ],
    )
    resumen = [
        f"- CAGR de ingresos: **{pct(h.cagr('revenue'))}**",
        f"- CAGR de EPS diluido: **{pct(h.cagr('eps_diluted'))}**",
        f"- CAGR de FCF: **{pct(h.cagr('free_cash_flow'))}**",
    ]
    if profile.growth:
        g = profile.growth
        resumen += [
            f"- Consenso próximo ejercicio — ingresos: **{pct(g.revenue_growth_next_year)}**, "
            f"beneficios: **{pct(g.earnings_growth_next_year)}** "
            f"({g.analyst_count or DASH} analistas)",
        ]
    return table + "\n" + "\n".join(resumen) + "\n"


def render_health(profile: CompanyProfile) -> str:
    return _by_year(
        profile.history,
        [
            ("net_debt", "Deuda neta", money),
            ("net_debt_to_ebitda", "Deuda neta / EBITDA", times),
            ("interest_coverage", "Cobertura intereses", times),
            ("current_ratio", "Liquidez corriente", times),
            ("cash_days", "Días de caja", lambda v: DASH if v is None else f"{v:.0f}"),
            ("debt_to_equity", "Deuda / patrimonio", times),
        ],
    )


def render_valuation(valuation: Optional[ValuationProfile]) -> str:
    if valuation is None:
        # La razón concreta (sin precios, o monedas mixtas) está en la sección
        # "LO QUE NO ESTÁ": acá no se duplica porque este helper no tiene el
        # profile completo, sólo el resultado.
        return "_Sin múltiplos de valuación — ver 'LO QUE NO ESTÁ' para el motivo._\n"

    labels = {
        "pe": "P/E",
        "ev_ebitda": "EV/EBITDA",
        "ev_revenue": "EV/Revenue",
        "fcf_yield": "FCF yield",
        "pb": "P/B",
        "ps": "P/S",
    }
    formatters = {"fcf_yield": pct}

    rows = []
    for name, band in valuation.bands.items():
        fmt = formatters.get(name, times)
        rows.append(
            [
                labels.get(name, name),
                fmt(band.current),
                fmt(band.median),
                fmt(band.p25),
                fmt(band.p75),
                DASH if band.percentile is None else f"p{band.percentile * 100:.0f}",
                DASH if band.vs_median is None else f"{band.vs_median:+.0%}",
                band.verdict or DASH,
            ]
        )

    table = _table(
        ["Múltiplo", "Actual", "Mediana", "p25", "p75", "Percentil", "vs mediana", "Lectura"],
        rows,
    )
    nota = (
        f"\n_{valuation.observations} observaciones mensuales. Los múltiplos históricos usan "
        f"los fundamentals que ya eran públicos en cada fecha (rezago de "
        f"{valuation.lag_days} días desde el cierre de ejercicio), para no valuar con "
        f"información que todavía no se había publicado._\n"
    )
    return table + nota


def render_cost_of_capital(profile: CompanyProfile) -> str:
    c = profile.cost_of_capital
    if c is None:
        return "_Sin datos._\n"
    rows = [
        ["Beta", num(c.beta)],
        [
            "Costo de deuda (histórico)"
            + (f" — ejercicio {c.cost_of_debt_year}" if c.cost_of_debt_year else ""),
            pct(c.cost_of_debt),
        ],
        ["Tasa impositiva efectiva", pct(c.effective_tax_rate)],
        ["Peso de la deuda", pct(c.debt_weight)],
        ["Peso del equity", pct(c.equity_weight)],
        ["Market cap", money(c.market_cap)],
        ["Deuda total", money(c.total_debt)],
    ]
    return _table(["Componente", "Valor"], rows) + (
        "\n_**El WACC no está calculado a propósito.** Requiere tasa libre de riesgo y "
        "equity risk premium, que son supuestos de mercado y envejecen en silencio. "
        "Declará los que uses y marcá el resultado [ESTIMADO]._\n"
    )


def render_gaps(profile: CompanyProfile) -> str:
    lines = [f"- {gap}" for gap in profile.gaps()]
    lines += [f"- ⚠ {w}" for w in profile.warnings]
    return "\n".join(lines) + "\n"


def render_data_block(profile: CompanyProfile) -> str:
    """El bloque DATOS completo que se le pasa al modelo."""
    source = profile.snapshot.source
    if source == "fmp":
        source_lines = [
            "- FMP (Financial Modeling Prep): estados contables anuales, precios mensuales,",
            "  consenso de analistas cuando está disponible.",
        ]
    else:
        source_lines = [
            "- yfinance (Yahoo Finance): estados contables anuales, precios mensuales,",
            "  consenso de analistas.",
        ]

    return "\n".join(
        [
            "=" * 70,
            "DATOS (Capa 1 — calculados desde los estados contables, determinístico)",
            "=" * 70,
            "",
            "## Identidad",
            "",
            render_identity(profile),
            "## Rentabilidad y calidad",
            "",
            render_profitability(profile),
            "## Crecimiento",
            "",
            render_growth(profile),
            "## Salud financiera",
            "",
            render_health(profile),
            "## Valuación: actual vs. su propia historia",
            "",
            render_valuation(profile.valuation),
            "## Componentes del costo de capital",
            "",
            render_cost_of_capital(profile),
            "## LO QUE NO ESTÁ",
            "",
            render_gaps(profile),
            "## Fuente",
            "",
            *source_lines,
            "- Todos los ratios fueron calculados por el bot desde las líneas contables,",
            "  no tomados precalculados de la fuente.",
            "",
        ]
    )
