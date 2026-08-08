"""Adapter de yfinance a `FundamentalSnapshot`.

Criterio general: cuando un ratio se puede calcular desde los estados contables
*y* además viene precocido en `.info`, gana el calculado. `.info` es un scrape
de Yahoo con unidades inconsistentes entre campos y entre versiones; el balance
es más aburrido pero más auditable. `.info` queda como fallback.

El adapter no hace red por sí mismo: recibe una `ticker_factory` (por defecto
`yfinance.Ticker`) que los tests reemplazan por un doble.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from ..models import FundamentalSnapshot, NoDataError, UpstreamError
from . import statements as st

logger = logging.getLogger(__name__)

#: Tasa efectiva por defecto cuando el income statement no permite derivarla.
#: Es el corporate rate federal de EE.UU.; para no-US es una aproximación gruesa
#: y por eso deja warning en el snapshot.
DEFAULT_TAX_RATE = 0.21

#: Fuera de este rango la tasa derivada es ruido contable (créditos fiscales,
#: pérdidas, one-offs) y conviene el default.
TAX_RATE_BOUNDS = (0.0, 0.60)

TickerFactory = Callable[[str], Any]


def _default_ticker_factory(symbol: str) -> Any:
    import yfinance as yf  # import diferido: los tests no necesitan yfinance

    return yf.Ticker(symbol)


def _info_value(info: dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        value = info.get(key)
        if value is None:
            continue
        try:
            out = float(value)
        except (TypeError, ValueError):
            continue
        if out == out and abs(out) != float("inf"):  # descarta NaN/inf
            return out
    return None


def _safe_div(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


class YFinanceAdapter:
    """Trae fundamentals de un ticker listado en un mercado que Yahoo cubre bien."""

    source = "yfinance"

    def __init__(self, ticker_factory: TickerFactory = _default_ticker_factory):
        self._ticker_factory = ticker_factory

    def fetch(self, ticker: str, *, requested_as: Optional[str] = None) -> FundamentalSnapshot:
        """Devuelve el snapshot de `ticker`.

        `requested_as` permite que un adapter que resuelve símbolos (BYMA) diga
        "consultá GGAL pero etiquetalo como GGAL.BA".

        Lanza `UpstreamError` si el proveedor falla y `NoDataError` si responde
        pero no hay nada utilizable.
        """
        label = requested_as or ticker
        try:
            handle = self._ticker_factory(ticker)
            info = dict(getattr(handle, "info", None) or {})
            financials = getattr(handle, "financials", None)
            balance_sheet = getattr(handle, "balance_sheet", None)
            cashflow = getattr(handle, "cashflow", None)
        except Exception as exc:  # yfinance tira de todo: HTTPError, KeyError, JSON...
            raise UpstreamError(label, f"yfinance falló: {exc}") from exc

        if not info and st.is_empty(financials) and st.is_empty(balance_sheet):
            raise NoDataError(label, "yfinance no devolvió ni .info ni estados contables")

        warnings: list[str] = []

        # --- insumos crudos ------------------------------------------------
        revenue = st.row_value(financials, st.REVENUE)
        revenue_prev = st.row_value(financials, st.REVENUE, column=1)
        gross_profit = st.row_value(financials, st.GROSS_PROFIT)
        if gross_profit is None and revenue is not None:
            cost = st.row_value(financials, st.COST_OF_REVENUE)
            if cost is not None:
                gross_profit = revenue - cost
        ebit = st.row_value(financials, st.EBIT)
        net_income = st.row_value(financials, st.NET_INCOME)

        equity = st.row_value(balance_sheet, st.EQUITY)
        equity_prev = st.row_value(balance_sheet, st.EQUITY, column=1)
        debt = st.total_debt(balance_sheet)
        debt_prev = st.total_debt(balance_sheet, column=1)

        fcf = st.free_cash_flow(cashflow)

        market_cap = _info_value(info, "marketCap")
        price = _info_value(info, "currentPrice", "regularMarketPrice", "previousClose")

        statement_currency = (info.get("financialCurrency") or info.get("currency") or None)
        quote_currency = (info.get("currency") or None)
        # Un ADR argentino cotiza en USD y reporta en ARS. Todo ratio que cruce
        # una punta de mercado con una del balance queda mal por el tipo de
        # cambio, así que directamente no se calcula.
        mixed_currencies = bool(
            statement_currency and quote_currency and statement_currency != quote_currency
        )
        if mixed_currencies:
            warnings.append(
                f"balance en {statement_currency} y cotización en {quote_currency}: "
                "se omiten los ratios que mezclan mercado con balance"
            )

        tax_rate, tax_warning = self._effective_tax_rate(financials)
        if tax_warning:
            warnings.append(tax_warning)

        # --- ratios --------------------------------------------------------
        pe = _info_value(info, "trailingPE")
        if pe is None:
            eps = _info_value(info, "trailingEps")
            if eps is not None and eps > 0:
                pe = _safe_div(price, eps)
        if pe is not None and pe <= 0:
            # Un P/E negativo es "la empresa pierde plata", no un múltiplo caro
            # ni barato. Dejarlo entrar al ranking daría señal invertida.
            warnings.append("P/E negativo o cero descartado (earnings negativos)")
            pe = None

        # priceToBook lo calcula Yahoo con ambas puntas en la misma moneda, así
        # que sirve incluso con monedas mixtas; el fallback casero no.
        pb = _info_value(info, "priceToBook")
        if pb is None and not mixed_currencies:
            pb = _safe_div(market_cap, equity)

        roe = _safe_div(net_income, equity)
        if roe is None:
            roe = _info_value(info, "returnOnEquity")

        roic, roic_warning = self._roic(ebit, tax_rate, debt, equity)
        if roic_warning:
            warnings.append(roic_warning)

        debt_to_equity = _safe_div(debt, equity)
        if debt_to_equity is None:
            raw_de = _info_value(info, "debtToEquity")
            if raw_de is not None:
                # yfinance reporta debtToEquity en porcentaje (165.2 == 1.652x).
                debt_to_equity = raw_de / 100.0
                warnings.append("debt_to_equity tomado de .info (no del balance)")
        debt_to_equity_prev = _safe_div(debt_prev, equity_prev)

        fcf_yield = None if mixed_currencies else _safe_div(fcf, market_cap)

        revenue_growth = None
        if revenue is not None and revenue_prev not in (None, 0):
            if revenue_prev > 0:
                revenue_growth = (revenue - revenue_prev) / revenue_prev
            else:
                # Base negativa: el porcentaje no tiene interpretación económica.
                warnings.append("revenue_growth_yoy omitido: revenue previo <= 0")
        if revenue_growth is None:
            revenue_growth = _info_value(info, "revenueGrowth")

        gross_margin = _safe_div(gross_profit, revenue)
        if gross_margin is None:
            gross_margin = _info_value(info, "grossMargins")
        if gross_margin == 0:
            # Los bancos no tienen costo de ventas, y tanto el income statement
            # como .info les completan el margen bruto con 0. Un 0% no es
            # información: es un campo que el negocio no reporta, y dejarlo
            # entrar al ranking los mostraría como los peores del sector.
            warnings.append("margen bruto reportado en 0; el ratio no aplica a este negocio")
            gross_margin = None

        net_margin = _safe_div(net_income, revenue)
        if net_margin is None:
            net_margin = _info_value(info, "profitMargins")

        dividend_yield = self._dividend_yield(info, price)

        snapshot = FundamentalSnapshot(
            ticker=label,
            source_ticker=ticker,
            source=self.source,
            as_of=datetime.now(timezone.utc),
            sector=info.get("sector"),
            industry=info.get("industry"),
            company_name=info.get("longName") or info.get("shortName"),
            currency=statement_currency,
            quote_currency=quote_currency,
            pe=pe,
            pb=pb,
            fcf_yield=fcf_yield,
            dividend_yield=dividend_yield,
            roe=roe,
            roic=roic,
            gross_margin=gross_margin,
            net_margin=net_margin,
            debt_to_equity=debt_to_equity,
            debt_to_equity_prev=debt_to_equity_prev,
            revenue_growth_yoy=revenue_growth,
            market_cap=market_cap,
            price=price,
            revenue=revenue,
            ebit=ebit,
            free_cash_flow=fcf,
            total_debt=debt,
            total_equity=equity,
            effective_tax_rate=tax_rate,
            warnings=tuple(warnings),
        )

        if all(snapshot.metric(name) is None for name in ("pe", "pb", "roe", "roic", "fcf_yield")):
            raise NoDataError(label, "ninguna métrica de valuación o rentabilidad disponible")

        if snapshot.sector is None:
            logger.warning("%s: sin sector; no va a poder rankearse contra peers", label)

        return snapshot

    # --- cálculos derivados -----------------------------------------------

    @staticmethod
    def _dividend_yield(info: dict[str, Any], price: Optional[float]) -> Optional[float]:
        """Dividend yield en fracción.

        `.info` mezcla unidades entre campos: `dividendYield` viene en porcentaje
        (0.32 == 0.32%) y `trailingAnnualDividendYield` en fracción (0.0031).
        Elegir por magnitud es imposible —0.32 es un yield válido tanto leído
        como 32% como 0.32%—, así que se prioriza reconstruirlo desde el
        dividendo por acción y el precio, donde la unidad es inequívoca.
        """
        rate = _info_value(info, "dividendRate", "trailingAnnualDividendRate")
        if rate is not None and price:
            return rate / price
        fraction = _info_value(info, "trailingAnnualDividendYield")
        if fraction is not None:
            return fraction
        percent = _info_value(info, "dividendYield")
        if percent is not None:
            return percent / 100.0
        return None

    @staticmethod
    def _effective_tax_rate(financials: Any) -> tuple[float, Optional[str]]:
        pretax = st.row_value(financials, st.PRETAX_INCOME)
        tax = st.row_value(financials, st.TAX_PROVISION)
        if pretax is None or tax is None or pretax <= 0:
            return DEFAULT_TAX_RATE, f"tax rate estimado en {DEFAULT_TAX_RATE:.0%} (no derivable del income statement)"
        rate = tax / pretax
        low, high = TAX_RATE_BOUNDS
        if not (low <= rate <= high):
            return DEFAULT_TAX_RATE, (
                f"tax rate derivado ({rate:.1%}) fuera de rango; se usa {DEFAULT_TAX_RATE:.0%}"
            )
        return rate, None

    @staticmethod
    def _roic(
        ebit: Optional[float],
        tax_rate: float,
        debt: Optional[float],
        equity: Optional[float],
    ) -> tuple[Optional[float], Optional[str]]:
        """ROIC ≈ NOPAT / capital invertido, con NOPAT = EBIT * (1 - tax).

        Aproximación deliberada: el capital invertido "de libro" resta caja
        excedente y excluye goodwill, y NOPAT usa la tasa efectiva y no la
        marginal. Sirve para comparar empresas del mismo sector entre sí, que es
        el único uso que le da el scorer; no es un WACC-vs-ROIC de valuación.
        """
        if ebit is None:
            return None, None
        invested_capital = (debt or 0.0) + (equity or 0.0)
        if invested_capital <= 0:
            return None, "ROIC omitido: capital invertido no positivo"
        warning = None
        if debt is None or equity is None:
            warning = "ROIC aproximado: falta deuda o patrimonio en el balance"
        return (ebit * (1 - tax_rate)) / invested_capital, warning
