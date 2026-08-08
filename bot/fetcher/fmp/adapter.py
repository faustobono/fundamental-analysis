"""Adapter de FMP a `FundamentalSnapshot` (capa del screener).

Calca la lógica de ratios y warnings del adapter de yfinance: mismo tratamiento
del margen bruto en 0 de los bancos, del P/E negativo, del desfasaje de monedas
de un ADR y de la tasa efectiva absurda. Así el snapshot significa lo mismo sin
importar de qué proveedor vino, que es la razón de que exista `FundamentalSnapshot`.

Diferencia con yfinance: FMP no precocina ratios inconsistentes en unidades, así
que acá todo se calcula de las líneas contables crudas. No hay fallback a un
`.info` con unidades mezcladas porque no hay `.info`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from ...models import FundamentalSnapshot, NoDataError
from ..yfinance_adapter import DEFAULT_TAX_RATE, TAX_RATE_BOUNDS, _safe_div
from . import fields as f
from .client import FmpClient

logger = logging.getLogger(__name__)


class FmpAdapter:
    """Trae fundamentals de FMP. Free tier: 5 años anuales de empresas US."""

    source = "fmp"

    def __init__(self, client: Optional[FmpClient] = None):
        self._client = client or FmpClient()

    def fetch(self, ticker: str, *, requested_as: Optional[str] = None) -> FundamentalSnapshot:
        label = requested_as or ticker
        client = self._client

        profile = client.get_one("profile", ticker=label, symbol=ticker)
        if profile is None:
            raise NoDataError(label, "FMP no tiene perfil para este ticker")

        # limit=2: el ejercicio actual y el anterior alcanzan para el snapshot
        # (crecimiento YoY y tendencia de deuda son de un período).
        income = client.get_list("income-statement", ticker=label, symbol=ticker, period="annual", limit=2)
        balance = client.get_list("balance-sheet-statement", ticker=label, symbol=ticker, period="annual", limit=2)
        cashflow = client.get_list("cash-flow-statement", ticker=label, symbol=ticker, period="annual", limit=1)

        inc = income[0] if income else {}
        inc_prev = income[1] if len(income) > 1 else {}
        bal = balance[0] if balance else {}
        bal_prev = balance[1] if len(balance) > 1 else {}
        cf = cashflow[0] if cashflow else {}

        warnings: list[str] = []

        # --- insumos crudos ------------------------------------------------
        revenue = f.num(inc, f.REVENUE)
        revenue_prev = f.num(inc_prev, f.REVENUE)
        gross_profit = f.num(inc, f.GROSS_PROFIT)
        ebit = f.num(inc, f.EBIT)
        net_income = f.num(inc, f.NET_INCOME)

        equity = f.num(bal, f.EQUITY)
        equity_prev = f.num(bal_prev, f.EQUITY)
        debt = f.total_debt(bal)
        debt_prev = f.total_debt(bal_prev)
        fcf = f.free_cash_flow(cf)

        market_cap = f.num(profile, f.MARKET_CAP)
        price = f.num(profile, f.PRICE)

        statement_currency = f.text(inc, f.CURRENCY) or f.text(profile, f.CURRENCY)
        quote_currency = f.text(profile, ("currency",))
        mixed_currencies = bool(
            statement_currency and quote_currency and statement_currency != quote_currency
        )
        if mixed_currencies:
            warnings.append(
                f"balance en {statement_currency} y cotización en {quote_currency}: "
                "se omiten los ratios que mezclan mercado con balance"
            )

        tax_rate, tax_warning = self._effective_tax_rate(inc)
        if tax_warning:
            warnings.append(tax_warning)

        # --- ratios --------------------------------------------------------
        eps = f.num(inc, f.EPS_DILUTED)
        pe = None
        if price is not None and eps is not None and eps > 0:
            pe = price / eps
        if pe is not None and pe <= 0:
            warnings.append("P/E negativo o cero descartado (earnings negativos)")
            pe = None

        pb = None if mixed_currencies else _safe_div(market_cap, equity)

        roe = _safe_div(net_income, equity)
        roic, roic_warning = self._roic(ebit, tax_rate, debt, equity)
        if roic_warning:
            warnings.append(roic_warning)

        debt_to_equity = _safe_div(debt, equity)
        debt_to_equity_prev = _safe_div(debt_prev, equity_prev)

        fcf_yield = None if mixed_currencies else _safe_div(fcf, market_cap)

        revenue_growth = None
        if revenue is not None and revenue_prev not in (None, 0):
            if revenue_prev > 0:
                revenue_growth = (revenue - revenue_prev) / revenue_prev
            else:
                warnings.append("revenue_growth_yoy omitido: revenue previo <= 0")

        gross_margin = _safe_div(gross_profit, revenue)
        if gross_margin == 0:
            # Igual que en yfinance: los bancos no tienen costo de ventas y el
            # margen bruto de 0% no es información, es un campo que no aplica.
            warnings.append("margen bruto reportado en 0; el ratio no aplica a este negocio")
            gross_margin = None

        net_margin = _safe_div(net_income, revenue)

        # FMP da un dividendo por acción en el perfil; el yield es sobre el
        # precio. OJO: no está 100% claro si `lastDividend` es el último pago o
        # el anualizado — misma ambigüedad de unidad que el dividendYield de
        # Yahoo. El smoke-test contra la API real lo verifica (un yield 4x fuera
        # de escala delata un pago trimestral tratado como anual). No es métrica
        # del scorer por defecto, así que el riesgo es acotado.
        last_dividend = f.num(profile, f.LAST_DIVIDEND)
        dividend_yield = None
        if last_dividend and price:
            dividend_yield = last_dividend / price

        snapshot = FundamentalSnapshot(
            ticker=label,
            source_ticker=ticker,
            source=self.source,
            as_of=datetime.now(timezone.utc),
            sector=f.text(profile, f.SECTOR),
            industry=f.text(profile, f.INDUSTRY),
            company_name=f.text(profile, f.COMPANY_NAME),
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

    # --- cálculos derivados (idénticos al adapter de yfinance) --------------

    @staticmethod
    def _effective_tax_rate(income: dict[str, Any]) -> tuple[float, Optional[str]]:
        pretax = f.num(income, f.PRETAX_INCOME)
        tax = f.num(income, f.TAX_PROVISION)
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
        if ebit is None:
            return None, None
        invested_capital = (debt or 0.0) + (equity or 0.0)
        if invested_capital <= 0:
            return None, "ROIC omitido: capital invertido no positivo"
        warning = None
        if debt is None or equity is None:
            warning = "ROIC aproximado: falta deuda o patrimonio en el balance"
        return (ebit * (1 - tax_rate)) / invested_capital, warning
