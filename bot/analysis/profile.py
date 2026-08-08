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
from datetime import date
from typing import Any, Optional

from ..fetcher.deep import DeepData, fetch_deep
from ..fetcher.yfinance_adapter import DEFAULT_TAX_RATE, TickerFactory, YFinanceAdapter, _default_ticker_factory, _info_value
from ..models import FundamentalSnapshot
from ..normalizer.normalize import normalize
from .scores import AltmanResult, PiotroskiResult, altman_z_score, piotroski_f_score
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
class FinancialStrength:
    """Altman Z-Score (riesgo de quiebra) y Piotroski F-Score (fortaleza
    fundamental), los dos modelos de screening clásicos que un trader espera
    ver junto al resto del análisis. Cada uno queda en `None` si falta algún
    insumo verificable — ver `bot/analysis/scores.py`."""

    altman: Optional[AltmanResult] = None
    piotroski: Optional[PiotroskiResult] = None

    def to_dict(self) -> dict:
        return {
            "altman_z_score": self.altman.z_score if self.altman else None,
            "altman_zone": self.altman.zone if self.altman else None,
            "piotroski_score": self.piotroski.score if self.piotroski else None,
            "piotroski_max_score": self.piotroski.max_score if self.piotroski else None,
            "piotroski_criteria": [
                {"nombre": c.name, "etiqueta": c.label, "cumplido": c.passed}
                for c in self.piotroski.criteria
            ]
            if self.piotroski
            else [],
        }


@dataclass(frozen=True)
class CompanyProfile:
    snapshot: FundamentalSnapshot
    history: FinancialHistory
    valuation: Optional[ValuationProfile] = None
    cost_of_capital: Optional[CostOfCapitalInputs] = None
    growth: Optional[GrowthOutlook] = None
    financial_strength: Optional[FinancialStrength] = None
    warnings: tuple[str, ...] = ()
    provider: str = "yfinance"
    """Quién trajo los datos. No es `snapshot.source`: ese campo se pisa a
    "byma" para un CEDEAR/ADR, y acá se necesita saber la fuente real de los
    datos (yfinance o FMP) para no acusar al proveedor equivocado en `gaps()`."""

    @property
    def ticker(self) -> str:
        return self.snapshot.ticker

    @property
    def years(self) -> int:
        return len(self.history)

    def gaps(self) -> list[str]:
        """Lo que el informe pide y esta fuente no puede dar. Se declara, no se rellena."""
        missing = [
            f"Segmentos de ingreso: el adapter de {self.provider} no los trae.",
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


def _cost_of_capital(
    history: FinancialHistory,
    *,
    beta: Optional[float],
    market_cap: Optional[float],
) -> CostOfCapitalInputs:
    latest = history.latest
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
        beta=beta,
        cost_of_debt=cost_of_debt,
        cost_of_debt_year=cost_of_debt_year,
        effective_tax_rate=latest.effective_tax_rate if latest else None,
        debt_weight=debt_weight,
        equity_weight=equity_weight,
        market_cap=market_cap,
        total_debt=total_debt,
    )


def _financial_strength(history: FinancialHistory, market_cap: Optional[float]) -> Optional[FinancialStrength]:
    latest = history.latest
    if latest is None:
        return None
    prior = history.periods[1] if len(history.periods) > 1 else None
    altman = altman_z_score(latest, market_cap)
    piotroski = piotroski_f_score(latest, prior)
    if altman is None and piotroski is None:
        return None
    return FinancialStrength(altman=altman, piotroski=piotroski)


def assemble_profile(
    snapshot: FundamentalSnapshot,
    history: FinancialHistory,
    prices: list[tuple[date, float]],
    *,
    beta: Optional[float],
    market_cap: Optional[float],
    current_price: Optional[float],
    current_shares: Optional[float],
    growth: Optional["GrowthOutlook"] = None,
    extra_warnings: Optional[list[str]] = None,
    provider: str = "yfinance",
) -> CompanyProfile:
    """Arma el `CompanyProfile` desde piezas ya traídas, sin saber el proveedor.

    Es el punto donde convergen yfinance y FMP: todo lo de arriba (traer y mapear)
    es específico del proveedor; todo lo de acá para abajo (la valuación, el costo
    de capital, la guarda de monedas mixtas, los warnings) es común. Poner esta
    lógica una sola vez es lo que evita que las dos fuentes se desincronicen.
    """
    warnings = list(snapshot.warnings) + list(extra_warnings or [])

    valuation = None
    if snapshot.has_currency_mismatch:
        # market_cap sale de precio × acciones, en la moneda de cotización; los
        # fundamentals del ejercicio están en la del balance. Para un ADR/CEDEAR
        # argentino son ARS contra USD: dividir uno por otro no da un múltiplo
        # raro, da ruido (un FCF yield de -30000% no es un dato distorsionado, es
        # basura), así que no se calcula.
        warnings.append(
            "Balance y cotización en monedas distintas: no se calculan múltiplos de "
            "valuación (P/E, EV/EBITDA, EV/Revenue, FCF yield, P/B, P/S). Cruzar precio "
            "en una moneda con fundamentals en otra no da un múltiplo válido."
        )
    else:
        valuation = build_valuation(history, prices, current_price, current_shares)

    return CompanyProfile(
        snapshot=snapshot,
        history=history,
        valuation=valuation,
        cost_of_capital=_cost_of_capital(history, beta=beta, market_cap=market_cap),
        growth=growth,
        financial_strength=_financial_strength(history, market_cap),
        warnings=tuple(dict.fromkeys(warnings)),
        provider=provider,
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


def company_profile(
    ticker: str,
    *,
    provider: str = "yfinance",
    requested_as: Optional[str] = None,
    max_years: int = 5,
) -> CompanyProfile:
    """Arma el perfil desde el proveedor elegido. Punto único de dispatch.

    El import de FMP es diferido a propósito: `fetcher.fmp.deep` importa este
    módulo, así que traerlo arriba sería un ciclo. Adentro de la función no.
    """
    if provider == "fmp":
        from ..fetcher.fmp import build_profile_fmp

        return build_profile_fmp(ticker, requested_as=requested_as, max_years=max_years)
    return build_profile(ticker, requested_as=requested_as, max_years=max_years)


def build_profile(
    ticker: str,
    ticker_factory: TickerFactory = _default_ticker_factory,
    *,
    requested_as: Optional[str] = None,
    max_years: int = 5,
) -> CompanyProfile:
    """Trae y calcula todo lo verificable de una empresa, desde yfinance."""
    data = fetch_deep(ticker, ticker_factory, requested_as=requested_as)

    snapshot = normalize(
        YFinanceAdapter(ticker_factory=ticker_factory).fetch(ticker, requested_as=requested_as)
    )
    history = build_history(data.financials, data.balance_sheet, data.cashflow, max_years)

    return assemble_profile(
        snapshot,
        history,
        data.prices,
        beta=_info_value(data.info, "beta"),
        market_cap=_info_value(data.info, "marketCap"),
        current_price=_info_value(data.info, "currentPrice", "regularMarketPrice", "previousClose"),
        current_shares=_info_value(data.info, "sharesOutstanding"),
        growth=_growth(data),
        extra_warnings=data.warnings,
    )


__all__ = [
    "CompanyProfile",
    "CostOfCapitalInputs",
    "FinancialStrength",
    "GrowthOutlook",
    "assemble_profile",
    "build_profile",
    "company_profile",
    "DEFAULT_TAX_RATE",
]
