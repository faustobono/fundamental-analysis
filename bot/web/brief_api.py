"""Serialización del análisis profundo (`brief`) para la web.

Mismo criterio que `api.py`: el server resuelve etiquetas y formato, el front
sólo pinta. La diferencia con el screener es que acá el resultado es de un
único ticker con series de varios ejercicios, no un ranking de un universo.

No pasa por LLM en ningún punto — es exactamente lo que arma
`python -m bot brief <ticker> --data-only`, pero como JSON en vez de markdown,
para que la web lo renderice sin que el usuario tenga que pegar nada en un chat.
"""

from __future__ import annotations

from typing import Any, Optional

from ..analysis.profile import CompanyProfile, company_profile
from ..analysis.series import FinancialHistory
from ..analysis.valuation import ValuationProfile
from ..fetcher.byma_adapter import resolve_symbol
from ..fetcher.cache import (
    DEFAULT_CACHE_PATH,
    DEFAULT_TTL_HOURS,
    NullPayloadCache,
    PayloadCache,
    brief_key,
)

#: (nombre del campo en AnnualPeriod, etiqueta, formato). 'roic' es la única
#: métrica derivada por método en vez de atributo; se resuelve aparte.
PROFITABILITY_METRICS: tuple[tuple[str, str, str], ...] = (
    ("roic", "ROIC", "pct"),
    ("roe", "ROE", "pct"),
    ("roa", "ROA", "pct"),
    ("gross_margin", "Margen bruto", "pct"),
    ("operating_margin", "Margen operativo", "pct"),
    ("net_margin", "Margen neto", "pct"),
    ("effective_tax_rate", "Tasa impositiva efectiva", "pct"),
    ("asset_turnover", "Rotación de activos", "x"),
)

GROWTH_METRICS: tuple[tuple[str, str, str], ...] = (
    ("revenue", "Ingresos", "money"),
    ("eps_diluted", "EPS diluido", "num"),
    ("free_cash_flow", "FCF", "money"),
    ("fcf_margin", "Margen FCF", "pct"),
    ("fcf_after_sbc", "FCF − SBC", "money"),
    ("sbc_to_revenue", "SBC / ingresos", "pct"),
    ("shares_outstanding", "Acciones en circulación", "money"),
    ("payout_ratio", "Payout ratio", "pct"),
)

HEALTH_METRICS: tuple[tuple[str, str, str], ...] = (
    ("net_debt", "Deuda neta", "money"),
    ("net_debt_to_ebitda", "Deuda neta / EBITDA", "x"),
    ("interest_coverage", "Cobertura de intereses", "x"),
    ("current_ratio", "Liquidez corriente", "x"),
    ("cash_days", "Días de caja", "days"),
    ("debt_to_equity", "Deuda / patrimonio", "x"),
)

VALUATION_LABEL: dict[str, str] = {
    "pe": "P/E",
    "ev_ebitda": "EV/EBITDA",
    "ev_revenue": "EV/Revenue",
    "fcf_yield": "FCF yield",
    "pb": "P/B",
    "ps": "P/S",
}

#: Todos son 'x' salvo el yield, que es un porcentaje.
VALUATION_FORMAT: dict[str, str] = {"fcf_yield": "pct"}


def _series_table(
    history: FinancialHistory, metrics: tuple[tuple[str, str, str], ...]
) -> dict[str, Any]:
    """Una fila por métrica, un valor por ejercicio — la forma que espera la tabla del front."""
    periods = list(reversed(history.periods))  # más viejo primero, se lee hacia adelante
    return {
        "years": [p.period_end.year if p.period_end else None for p in periods],
        "rows": [
            {
                "name": name,
                "label": label,
                "format": fmt,
                "values": [
                    (period.roic() if name == "roic" else getattr(period, name, None))
                    for period in periods
                ],
            }
            for name, label, fmt in metrics
        ],
    }


def _valuation_payload(valuation: Optional[ValuationProfile]) -> Optional[dict[str, Any]]:
    if valuation is None:
        return None
    return {
        "current": valuation.current.to_dict(),
        "bands": [
            {
                "metric": name,
                "label": VALUATION_LABEL.get(name, name),
                "format": VALUATION_FORMAT.get(name, "x"),
                "current": band.current,
                "median": band.median,
                "p25": band.p25,
                "p75": band.p75,
                "min": band.minimum,
                "max": band.maximum,
                "percentile": band.percentile,
                "vs_median": band.vs_median,
                "verdict": band.verdict,
                "observations": band.observations,
            }
            for name, band in valuation.bands.items()
        ],
        "observations": valuation.observations,
        "lag_days": valuation.lag_days,
    }


def _summary(profile: CompanyProfile) -> dict[str, Any]:
    h = profile.history
    summary: dict[str, Any] = {
        "roic_avg": h.average("roic"),
        "roic_trend": h.trend("roic"),
        "operating_margin_trend": h.trend("operating_margin"),
        "net_margin_trend": h.trend("net_margin"),
        "revenue_cagr": h.cagr("revenue"),
        "eps_cagr": h.cagr("eps_diluted"),
        "fcf_cagr": h.cagr("free_cash_flow"),
    }
    if profile.growth:
        g = profile.growth
        summary["next_year_revenue_growth"] = g.revenue_growth_next_year
        summary["next_year_earnings_growth"] = g.earnings_growth_next_year
        summary["analyst_count"] = g.analyst_count
        summary["target_mean_price"] = g.target_mean_price
        summary["target_low"] = g.target_low
        summary["target_high"] = g.target_high
    return summary


def run_brief(
    ticker: str,
    *,
    years: int = 5,
    provider: str = "yfinance",
    use_cache: bool = True,
    cache_ttl_hours: float = DEFAULT_TTL_HOURS,
    cache_path: Optional[str] = None,
) -> dict[str, Any]:
    """Análisis profundo de un ticker, del cache si está vigente. Propaga `FetchError`.

    El cache importa más acá que en el screener: un brief son 5 ejercicios de
    balances más el histórico de precios, y la web lo pide sola al abrir. Sin
    esto, cada visita gasta esa cuota de nuevo para el mismo ticker.

    Sólo se cachea el resultado exitoso: un `FetchError` (ticker inexistente,
    CEDEAR sin mapear, proveedor caído) se propaga sin dejar nada guardado.
    """
    key = brief_key(ticker, years, provider)
    cache = (
        PayloadCache(cache_path or DEFAULT_CACHE_PATH, ttl_hours=cache_ttl_hours)
        if use_cache
        else NullPayloadCache()
    )
    try:
        cached = cache.get(key)
        if cached is not None:
            return {**cached, "cached": True}
        payload = _build_brief(ticker, years=years, provider=provider)
        cache.put(key, payload)
    finally:
        cache.close()
    return {**payload, "cached": False}


def _build_brief(ticker: str, *, years: int, provider: str) -> dict[str, Any]:
    """Trae y calcula todo el análisis profundo, sin pasar por el cache."""
    symbol, requested_as = resolve_symbol(ticker)
    profile = company_profile(
        symbol, provider=provider, requested_as=requested_as, max_years=years
    )
    s = profile.snapshot

    return {
        "identity": {
            "ticker": s.ticker,
            "company_name": s.company_name,
            "sector": s.sector,
            "industry": s.industry,
            "currency": s.currency,
            "quote_currency": s.quote_currency,
            "currency_mismatch": s.has_currency_mismatch,
            "is_cedear": s.is_cedear,
            "source_ticker": s.source_ticker,
            "years_available": profile.years,
            "as_of": s.as_of.isoformat(),
        },
        "profitability": _series_table(profile.history, PROFITABILITY_METRICS),
        "growth": _series_table(profile.history, GROWTH_METRICS),
        "health": _series_table(profile.history, HEALTH_METRICS),
        "summary": _summary(profile),
        "valuation": _valuation_payload(profile.valuation),
        "cost_of_capital": profile.cost_of_capital.to_dict() if profile.cost_of_capital else None,
        "financial_strength": profile.financial_strength.to_dict() if profile.financial_strength else None,
        "gaps": profile.gaps(),
        "warnings": list(profile.warnings),
    }
