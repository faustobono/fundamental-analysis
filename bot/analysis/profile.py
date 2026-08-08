"""`CompanyProfile`: todo lo que la Capa 1 puede calcular de una empresa.

Es el payload que después consume la Capa 2 (el análisis cualitativo). La regla
que ordena este módulo: **acá no se estima nada que no se pueda verificar**.
Si un dato no sale de los estados contables o del precio, no se inventa — se
deja en `None` y el informe lo marca como faltante.

Por eso no hay WACC. Calcularlo exige una tasa libre de riesgo y un equity risk
premium, que son supuestos que envejecen en silencio: los mismos motivos por
los que el `FxProvider` no trae un tipo de cambio hardcodeado. Lo que sí se
entrega son sus componentes verificables —beta, costo de deuda derivado del
balance, estructura de capital y tasa efectiva— para que el spread ROIC-WACC se
arme arriba, con supuestos explícitos y marcados.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from ..fetcher.deep import DeepData, fetch_deep
from ..fetcher.yfinance_adapter import DEFAULT_TAX_RATE, TickerFactory, YFinanceAdapter, _default_ticker_factory, _info_value
from ..models import FundamentalSnapshot
from ..normalizer.normalize import normalize
from .series import FinancialHistory, build_history, latest_with
from .valuation import ValuationProfile, build_valuation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CostOfCapitalInputs:
    """Insumos verificables del costo de capital. **No incluye el WACC.**

    Falta a propósito la tasa libre de riesgo y el equity risk premium: son
    supuestos de mercado, no datos de la empresa. Quien arme el WACC tiene que
    ponerlos explícitamente y marcar el resultado como estimado.
    """

    beta: Optional[float] = None
    cost_of_debt: Optional[float] = None
    """Intereses del ejercicio sobre deuda total. Es el costo histórico, no el marginal."""

    cost_of_debt_year: Optional[int] = None
    """Ejercicio del que salió el costo de deuda. Puede no ser el último."""

    effective_tax_rate: Optional[float] = None
    debt_weight: Optional[float] = None
    equity_weight: Optional[float] = None
    market_cap: Optional[float] = None
    total_debt: Optional[float] = None

    @property
    def missing_for_wacc(self) -> tuple[str, ...]:
        """Lo que hay que aportar desde afuera para poder calcularlo."""
        return ("tasa_libre_de_riesgo", "equity_risk_premium")

    def to_dict(self) -> dict:
        return {
            "beta": self.beta,
            "costo_de_deuda": self.cost_of_debt,
            "costo_de_deuda_ejercicio": self.cost_of_debt_year,
            "tasa_impositiva_efectiva": self.effective_tax_rate,
            "peso_deuda": self.debt_weight,
            "peso_equity": self.equity_weight,
            "market_cap": self.market_cap,
            "deuda_total": self.total_debt,
            "falta_para_calcular_wacc": list(self.missing_for_wacc),
            "nota": (
                "El WACC no se calcula acá a propósito: exige tasa libre de riesgo y "
                "equity risk premium, que son supuestos de mercado y envejecen. "
                "Calculalo arriba con supuestos explícitos y marcalo [ESTIMADO]."
            ),
        }


@dataclass(frozen=True)
class GrowthOutlook:
    """Proyecciones de analistas. Son opinión de terceros, no dato contable."""

    revenue_growth_next_year: Optional[float] = None
    earnings_growth_next_year: Optional[float] = None
    analyst_count: Optional[int] = None
    target_mean_price: Optional[float] = None
    target_low: Optional[float] = None
    target_high: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "crecimiento_ingresos_proximo_ano": self.revenue_growth_next_year,
            "crecimiento_beneficios_proximo_ano": self.earnings_growth_next_year,
            "cantidad_de_analistas": self.analyst_count,
            "precio_objetivo_promedio": self.target_mean_price,
            "precio_objetivo_min": self.target_low,
            "precio_objetivo_max": self.target_high,
            "nota": "Consenso de analistas: es opinión de terceros, no un dato del balance.",
        }


@dataclass(frozen=True)
class CompanyProfile:
    snapshot: FundamentalSnapshot
    history: FinancialHistory
    valuation: Optional[ValuationProfile] = None
    cost_of_capital: Optional[CostOfCapitalInputs] = None
    growth: Optional[GrowthOutlook] = None
    warnings: tuple[str, ...] = ()

    @property
    def ticker(self) -> str:
        return self.snapshot.ticker

    @property
    def years(self) -> int:
        return len(self.history)

    def gaps(self) -> list[str]:
        """Lo que el informe pide y esta fuente no puede dar. Se declara, no se rellena."""
        missing = [
            "Segmentos de ingreso: yfinance no los publica.",
            "Moat / ventaja competitiva: no es un dato contable.",
        ]
        if self.years < 5:
            missing.append(f"Sólo {self.years} ejercicio(s) disponible(s); el informe pide 5.")
        if self.valuation is None:
            if self.snapshot.has_currency_mismatch:
                missing.append(
                    "Sin múltiplos de valuación: balance y cotización están en monedas "
                    "distintas y cruzarlos no da un número válido."
                )
            else:
                missing.append("Sin medianas históricas de valuación: falta el histórico de precios.")
        if self.cost_of_capital is None or self.cost_of_capital.beta is None:
            missing.append("Sin beta: no se puede estimar el costo de equity.")
        return missing


def _cost_of_capital(data: DeepData, history: FinancialHistory) -> CostOfCapitalInputs:
    latest = history.latest
    market_cap = _info_value(data.info, "marketCap")
    total_debt = latest.total_debt if latest else None

    # Costo histórico: intereses pagados sobre el stock de deuda. El costo
    # marginal (a qué tasa se endeudaría hoy) requiere datos de mercado.
    # Se busca hacia atrás porque hay empresas que dejan de reportar la línea.
    cost_of_debt = cost_of_debt_year = None
    source = latest_with(history, "interest_expense", "total_debt")
    if source:
        cost_of_debt = abs(source.interest_expense) / source.total_debt
        cost_of_debt_year = source.period_end.year if source.period_end else None

    debt_weight = equity_weight = None
    if market_cap and total_debt is not None:
        total_capital = market_cap + total_debt
        if total_capital > 0:
            debt_weight = total_debt / total_capital
            equity_weight = market_cap / total_capital

    return CostOfCapitalInputs(
        beta=_info_value(data.info, "beta"),
        cost_of_debt=cost_of_debt,
        cost_of_debt_year=cost_of_debt_year,
        effective_tax_rate=latest.effective_tax_rate if latest else None,
        debt_weight=debt_weight,
        equity_weight=equity_weight,
        market_cap=market_cap,
        total_debt=total_debt,
    )


def _frame_value(frame: Any, row: str, column: str) -> Optional[float]:
    """Lectura tolerante de los DataFrames de estimaciones."""
    if frame is None or getattr(frame, "empty", True):
        return None
    try:
        value = float(frame.loc[row, column])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return value if value == value else None


def _growth(data: DeepData) -> GrowthOutlook:
    targets = data.info
    return GrowthOutlook(
        # '+1y' es el próximo ejercicio en la grilla de yfinance.
        revenue_growth_next_year=_frame_value(data.revenue_estimate, "+1y", "growth"),
        earnings_growth_next_year=_frame_value(data.earnings_estimate, "+1y", "growth"),
        analyst_count=int(targets["numberOfAnalystOpinions"])
        if targets.get("numberOfAnalystOpinions")
        else None,
        target_mean_price=_info_value(targets, "targetMeanPrice"),
        target_low=_info_value(targets, "targetLowPrice"),
        target_high=_info_value(targets, "targetHighPrice"),
    )


def build_profile(
    ticker: str,
    ticker_factory: TickerFactory = _default_ticker_factory,
    *,
    requested_as: Optional[str] = None,
    max_years: int = 5,
) -> CompanyProfile:
    """Trae y calcula todo lo verificable de una empresa."""
    data = fetch_deep(ticker, ticker_factory, requested_as=requested_as)

    snapshot = normalize(
        YFinanceAdapter(ticker_factory=ticker_factory).fetch(ticker, requested_as=requested_as)
    )
    history = build_history(data.financials, data.balance_sheet, data.cashflow, max_years)

    warnings = list(snapshot.warnings) + data.warnings

    valuation = None
    if snapshot.has_currency_mismatch:
        # market_cap sale de precio × acciones, en la moneda de cotización; los
        # fundamentals del ejercicio están en la moneda del balance. Para un
        # ADR/CEDEAR argentino son ARS contra USD: dividir uno por otro no da un
        # múltiplo raro, da ruido — un FCF yield de -30000% no es un dato
        # distorsionado, es basura. Antes esto se calculaba igual y sólo se
        # avisaba por texto que "podía estar distorsionado"; ahora directamente
        # no se calcula.
        warnings.append(
            "Balance y cotización en monedas distintas: no se calculan múltiplos de "
            "valuación (P/E, EV/EBITDA, EV/Revenue, FCF yield, P/B, P/S). Cruzar precio "
            "en una moneda con fundamentals en otra no da un múltiplo válido."
        )
    else:
        current_price = _info_value(data.info, "currentPrice", "regularMarketPrice", "previousClose")
        valuation = build_valuation(
            history,
            data.prices,
            current_price,
            _info_value(data.info, "sharesOutstanding"),
        )

    return CompanyProfile(
        snapshot=snapshot,
        history=history,
        valuation=valuation,
        cost_of_capital=_cost_of_capital(data, history),
        growth=_growth(data),
        warnings=tuple(dict.fromkeys(warnings)),
    )


__all__ = [
    "CompanyProfile",
    "CostOfCapitalInputs",
    "GrowthOutlook",
    "build_profile",
    "DEFAULT_TAX_RATE",
]
