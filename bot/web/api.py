"""Serialización del screener para el front end.

El front es tonto a propósito: acá se resuelve todo lo que necesita para pintar
—etiquetas, formato de cada número, ancho de la barra— así que agregar una
métrica no obliga a tocar JavaScript.
"""

from __future__ import annotations

import time
from typing import Any, Optional, Sequence

from ..fetcher.service import build_service
from ..models import FundamentalSnapshot
from ..scorer.metrics import Method, MetricSpec
from ..scorer.sector_scorer import SectorScorer, TickerScore

#: Cómo se muestra cada número. 'pct' es fracción a porcentaje, 'x' son veces,
#: 'delta_x' es una variación en veces (lleva signo), 'money' es un monto.
RATIO_FORMAT: dict[str, str] = {
    "pe": "x",
    "pb": "x",
    "roe": "pct",
    "roic": "pct",
    "debt_to_equity": "x",
    "debt_to_equity_trend": "delta_x",
    "fcf_yield": "pct",
    "revenue_growth_yoy": "pct",
    "gross_margin": "pct",
    "net_margin": "pct",
    "dividend_yield": "pct",
}

RATIO_LABEL: dict[str, str] = {
    "pe": "P/E",
    "pb": "P/B",
    "roe": "ROE",
    "roic": "ROIC",
    "debt_to_equity": "Deuda / Patrimonio",
    "debt_to_equity_trend": "Tendencia D/E",
    "fcf_yield": "FCF yield",
    "revenue_growth_yoy": "Crecimiento ingresos YoY",
    "gross_margin": "Margen bruto",
    "net_margin": "Margen neto",
    "dividend_yield": "Dividend yield",
}

#: Los z-scores son ilimitados; para la barra se recorta a ±2.5 desvíos, que en
#: una muestra normal cubre el 99%.
ZSCORE_BAR_RANGE = 2.5


def _bar(score: float, method: Method) -> float:
    """Posición de la barra en [0, 1]. El percentil ya lo está; el z-score no."""
    if method is Method.PERCENTILE:
        return max(0.0, min(1.0, score))
    return max(0.0, min(1.0, (score + ZSCORE_BAR_RANGE) / (2 * ZSCORE_BAR_RANGE)))


def _snapshot_payload(snapshot: FundamentalSnapshot) -> dict[str, Any]:
    return {
        "ticker": snapshot.ticker,
        "source_ticker": snapshot.source_ticker,
        "company_name": snapshot.company_name,
        "sector": snapshot.sector,
        "industry": snapshot.industry,
        "currency": snapshot.currency,
        "quote_currency": snapshot.quote_currency,
        "currency_mismatch": snapshot.has_currency_mismatch,
        "is_cedear": snapshot.is_cedear,
        "as_of": snapshot.as_of.isoformat(),
        "source": snapshot.source,
        "warnings": list(snapshot.warnings),
        "ratios": [
            {
                "name": name,
                "label": RATIO_LABEL.get(name, name),
                "value": snapshot.metric(name),
                "format": RATIO_FORMAT.get(name, "num"),
            }
            for name in (
                "pe",
                "pb",
                "roe",
                "roic",
                "fcf_yield",
                "debt_to_equity",
                "debt_to_equity_trend",
                "revenue_growth_yoy",
                "gross_margin",
                "net_margin",
                "dividend_yield",
            )
        ],
        "scale": {
            "market_cap": snapshot.market_cap,
            "revenue": snapshot.revenue,
            "free_cash_flow": snapshot.free_cash_flow,
            "total_debt": snapshot.total_debt,
            "total_equity": snapshot.total_equity,
            "effective_tax_rate": snapshot.effective_tax_rate,
        },
    }


def _score_payload(score: TickerScore, method: Method) -> dict[str, Any]:
    return {
        "rank": score.rank,
        "ticker": score.ticker,
        "company_name": score.snapshot.company_name,
        "composite": round(score.composite, 4),
        "coverage": round(score.coverage, 4),
        "missing": [
            {"name": name, "label": RATIO_LABEL.get(name, name)} for name in score.missing
        ],
        "metrics": [
            {
                "name": m.name,
                "label": m.label,
                "raw": m.raw,
                "score": round(m.score, 4),
                "bar": round(_bar(m.score, method), 4),
                "peers": m.peers,
                "higher_is_better": m.higher_is_better,
                "format": RATIO_FORMAT.get(m.name, "num"),
            }
            for m in score.metrics.values()
        ],
        "snapshot": _snapshot_payload(score.snapshot),
    }


def run_screen(
    tickers: Sequence[str],
    *,
    metrics: Sequence[MetricSpec],
    method: Method = Method.PERCENTILE,
    top_n: int = 0,
    min_peers: int = 3,
    min_metrics: int = 2,
    use_cache: bool = True,
    cache_ttl_hours: float = 24.0,
) -> dict[str, Any]:
    """Corre el pipeline completo y devuelve el payload que consume el front.

    `top_n=0` devuelve el sector entero; el front filtra en cliente para que
    cambiar el top-N no obligue a refetchear.
    """
    started = time.monotonic()

    service, cache = build_service(ttl_hours=cache_ttl_hours, use_cache=use_cache)
    try:
        result = service.get_many(tickers)
    finally:
        cache.close()

    scoring = SectorScorer(
        metrics, method=method, min_peers=min_peers, min_metrics=min_metrics
    ).score(result.snapshots)

    sectors = [
        {
            "sector": sector,
            "thin": ranking.thin,
            "peer_count": ranking.peer_count,
            "ranked": [_score_payload(s, method) for s in ranking.top(top_n)],
            "unrankable": [
                {"ticker": s.ticker, "company_name": s.company_name, "reason": reason}
                for s, reason in ranking.unrankable
            ],
        }
        for sector, ranking in sorted(scoring.sectors.items())
    ]

    return {
        "meta": {
            "requested": len(tickers),
            "ok": result.ok,
            "failed": len(result.failures),
            "cache_hits": result.cache_hits,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "method": method.value,
            "min_peers": min_peers,
            "min_metrics": min_metrics,
            "metrics": [
                {
                    "name": m.name,
                    "label": m.display,
                    "weight": m.weight,
                    "higher_is_better": m.higher_is_better,
                }
                for m in metrics
            ],
        },
        "sectors": sectors,
        "failures": [{"ticker": t, "reason": r} for t, r in result.failures],
    }


def parse_tickers(raw: Optional[str]) -> list[str]:
    """Acepta comas, espacios y saltos de línea; deduplica manteniendo el orden."""
    if not raw:
        return []
    separators = str.maketrans({",": " ", "\n": " ", "\r": " ", "\t": " ", ";": " "})
    seen: set[str] = set()
    out: list[str] = []
    for token in raw.translate(separators).split():
        key = token.strip().upper()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out
