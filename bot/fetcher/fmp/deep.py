"""Análisis profundo (`brief`) desde FMP.

Arma el `CompanyProfile` completo —snapshot, series de N ejercicios, valuación
vs. historia propia, componentes del costo de capital, proyecciones— y lo pasa
por `assemble_profile`, el mismo ensamblador que usa la ruta de yfinance. Todo
lo específico de FMP (traer y mapear los campos) vive acá; la lógica de armado
es común, así las dos fuentes no se desincronizan.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Optional

from ...analysis.profile import CompanyProfile, GrowthOutlook, assemble_profile
from ...analysis.series import AnnualPeriod, FinancialHistory
from ...models import NoDataError
from ...normalizer.normalize import normalize
from . import fields as f
from .adapter import FmpAdapter
from .client import FmpClient

logger = logging.getLogger(__name__)


def _annual_period(inc: dict[str, Any], bal: dict[str, Any], cf: dict[str, Any]) -> AnnualPeriod:
    return AnnualPeriod(
        period_end=f.parse_date(inc) or f.parse_date(bal),
        revenue=f.num(inc, f.REVENUE),
        gross_profit=f.num(inc, f.GROSS_PROFIT),
        operating_income=f.num(inc, f.OPERATING_INCOME),
        ebit=f.num(inc, f.EBIT),
        ebitda=f.num(inc, f.EBITDA),
        net_income=f.num(inc, f.NET_INCOME),
        eps_diluted=f.num(inc, f.EPS_DILUTED),
        interest_expense=f.num(inc, f.INTEREST_EXPENSE),
        pretax_income=f.num(inc, f.PRETAX_INCOME),
        tax_provision=f.num(inc, f.TAX_PROVISION),
        cash=f.num(bal, f.CASH),
        total_debt=f.total_debt(bal),
        total_equity=f.num(bal, f.EQUITY),
        current_assets=f.num(bal, f.CURRENT_ASSETS),
        current_liabilities=f.num(bal, f.CURRENT_LIABILITIES),
        shares_outstanding=f.num(inc, f.DILUTED_SHARES) or f.num(bal, f.SHARES_OUTSTANDING),
        operating_cash_flow=f.num(cf, f.OPERATING_CASH_FLOW),
        capex=f.num(cf, f.CAPEX),
        free_cash_flow=f.free_cash_flow(cf),
        stock_based_comp=f.num(cf, f.STOCK_BASED_COMP),
        buybacks=f.num(cf, f.BUYBACKS),
    )


def _history_from_fmp(
    income: list[dict[str, Any]],
    balance: list[dict[str, Any]],
    cashflow: list[dict[str, Any]],
) -> FinancialHistory:
    """Une los tres estados por año fiscal. El income statement es el que manda.

    FMP los devuelve del más reciente al más viejo y alineados por ejercicio,
    pero se emparejan por año igual: si al balance le falta un ejercicio, esa
    fila queda con esos campos en None en vez de correrse y mezclar años.
    """
    by_year_bal = {f.parse_date(b).year: b for b in balance if f.parse_date(b)}
    by_year_cf = {f.parse_date(c).year: c for c in cashflow if f.parse_date(c)}

    periods: list[AnnualPeriod] = []
    for inc in income:
        end = f.parse_date(inc)
        if end is None:
            continue
        periods.append(_annual_period(inc, by_year_bal.get(end.year, {}), by_year_cf.get(end.year, {})))

    return FinancialHistory(tuple(p for p in periods if p.has_data))


def _monthly_prices(rows: list[dict[str, Any]]) -> list[tuple[date, float]]:
    """Un cierre por mes desde la serie diaria de FMP.

    La valuación histórica trabaja con ~60 puntos mensuales; mandarle 1260 días
    sería trabajo tirado. Se toma el último cierre de cada mes.
    """
    best: dict[tuple[int, int], tuple[date, float]] = {}
    for row in rows:
        day = f.parse_date(row)
        close = f.num(row, ("close", "adjClose"))
        if day is None or close is None or close <= 0:
            continue
        key = (day.year, day.month)
        if key not in best or day > best[key][0]:
            best[key] = (day, close)
    return sorted(best.values())


def _fetch_prices(client: FmpClient, ticker: str, years: int) -> list[tuple[date, float]]:
    """Precios mensuales de los últimos `years` años. Vacío si FMP no los da."""
    frm = date(date.today().year - years, date.today().month, 1).isoformat()
    try:
        data = client.get("historical-price-eod/full", ticker=ticker, symbol=ticker, **{"from": frm})
    except Exception as exc:  # noqa: BLE001
        logger.debug("%s: sin histórico de precios en FMP (%s)", ticker, exc)
        return []
    # stable devuelve una lista; algunas variantes envuelven en {"historical": [...]}.
    rows = data.get("historical", []) if isinstance(data, dict) else data
    return _monthly_prices(rows if isinstance(rows, list) else [])


def _growth(client: FmpClient, ticker: str, profile: dict[str, Any]) -> GrowthOutlook:
    """Proyecciones de analistas. Best-effort: en free suelen estar gateadas.

    Cualquier fallo cae a None sin romper el informe — son opinión de terceros,
    no un dato del balance, y el bot ya las trata como opcionales.
    """
    revenue_growth = earnings_growth = None
    analyst_count = None
    try:
        estimates = client.get_list("analyst-estimates", ticker=ticker, symbol=ticker, period="annual", limit=1)
        if estimates:
            est = estimates[0]
            rev = f.num(est, ("estimatedRevenueAvg", "revenueAvg"))
            eps = f.num(est, ("estimatedEpsAvg", "epsAvg"))
            analyst_count = f.num(est, ("numberAnalystEstimatedRevenue", "numAnalystsRevenue"))
            # FMP da el nivel estimado, no la tasa; sin el histórico alineado no
            # se puede derivar el crecimiento acá, así que se deja el nivel fuera
            # y sólo se reporta lo que es tasa si el endpoint la trae.
            revenue_growth = f.num(est, ("estimatedRevenueGrowth",))
            earnings_growth = f.num(est, ("estimatedEpsGrowth",))
            _ = (rev, eps)  # disponibles si más adelante se quiere derivar la tasa
    except Exception as exc:  # noqa: BLE001
        logger.debug("%s: sin estimaciones en FMP (%s)", ticker, exc)

    return GrowthOutlook(
        revenue_growth_next_year=revenue_growth,
        earnings_growth_next_year=earnings_growth,
        analyst_count=int(analyst_count) if analyst_count else None,
        target_mean_price=None,
        target_low=None,
        target_high=None,
    )


def build_profile_fmp(
    ticker: str,
    client: Optional[FmpClient] = None,
    *,
    requested_as: Optional[str] = None,
    max_years: int = 5,
) -> CompanyProfile:
    """Trae y calcula todo lo verificable de una empresa, desde FMP."""
    client = client or FmpClient()
    label = requested_as or ticker

    profile = client.get_one("profile", ticker=label, symbol=ticker)
    if profile is None:
        raise NoDataError(label, "FMP no tiene perfil para este ticker")

    income = client.get_list("income-statement", ticker=label, symbol=ticker, period="annual", limit=max_years)
    balance = client.get_list("balance-sheet-statement", ticker=label, symbol=ticker, period="annual", limit=max_years)
    cashflow = client.get_list("cash-flow-statement", ticker=label, symbol=ticker, period="annual", limit=max_years)

    snapshot = normalize(FmpAdapter(client).fetch(ticker, requested_as=requested_as))
    history = _history_from_fmp(income, balance, cashflow)
    prices = _fetch_prices(client, ticker, max_years)

    return assemble_profile(
        snapshot,
        history,
        prices,
        beta=f.num(profile, f.BETA),
        market_cap=f.num(profile, f.MARKET_CAP),
        current_price=f.num(profile, f.PRICE),
        current_shares=history.latest.shares_outstanding if history.latest else None,
        growth=_growth(client, ticker, profile),
        provider="fmp",
    )
