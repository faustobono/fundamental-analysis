"""Series temporales de estados contables.

`FundamentalSnapshot` es una foto de un período. Casi todo lo que pide un
informe fundamental serio —tendencia de márgenes, ROIC promedio, si el FCF
crece— es una serie, no una foto. Este módulo agrega esa dimensión.

Un `AnnualPeriod` guarda las líneas crudas de un ejercicio; los ratios son
propiedades derivadas, así que nunca pueden desincronizarse del dato que los
origina. `FinancialHistory` es la secuencia, del ejercicio más reciente al más
viejo.

yfinance devuelve 5 ejercicios anuales. Diez años, que es lo que idealmente
querría un análisis de ciclo completo, no salen de esta fuente.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date, datetime
from typing import Any, Callable, Optional, Sequence

from ..fetcher import statements as st
from ..models import _clean

#: Cuántos ejercicios pide el informe. yfinance rara vez da más.
TARGET_YEARS = 5


@dataclass(frozen=True)
class AnnualPeriod:
    """Un ejercicio fiscal. Sólo líneas reportadas; los ratios se derivan."""

    period_end: Optional[date] = None

    # --- resultado ---------------------------------------------------------
    revenue: Optional[float] = None
    gross_profit: Optional[float] = None
    operating_income: Optional[float] = None
    ebit: Optional[float] = None
    ebitda: Optional[float] = None
    net_income: Optional[float] = None
    eps_diluted: Optional[float] = None
    interest_expense: Optional[float] = None
    pretax_income: Optional[float] = None
    tax_provision: Optional[float] = None

    # --- balance -----------------------------------------------------------
    cash: Optional[float] = None
    total_debt: Optional[float] = None
    total_equity: Optional[float] = None
    current_assets: Optional[float] = None
    current_liabilities: Optional[float] = None
    shares_outstanding: Optional[float] = None
    total_assets: Optional[float] = None
    retained_earnings: Optional[float] = None

    # --- flujo de fondos ---------------------------------------------------
    operating_cash_flow: Optional[float] = None
    capex: Optional[float] = None
    free_cash_flow: Optional[float] = None
    stock_based_comp: Optional[float] = None
    buybacks: Optional[float] = None
    dividends_paid: Optional[float] = None

    def __post_init__(self) -> None:
        for f in fields(self):
            if f.name == "period_end":
                continue
            object.__setattr__(self, f.name, _clean(getattr(self, f.name)))

    @property
    def has_data(self) -> bool:
        """Si el ejercicio tiene sustancia o es una columna fantasma.

        yfinance a veces devuelve un ejercicio extra con casi todas las filas en
        NaN (para AAPL, 6 valores de 39). Contarlo como año infla el período
        declarado y mete una fila de guiones en cada tabla.
        """
        return self.revenue is not None or self.net_income is not None

    # --- márgenes ----------------------------------------------------------

    @property
    def gross_margin(self) -> Optional[float]:
        # Un margen bruto de exactamente 0 es el relleno que Yahoo le pone a los
        # bancos, no un margen del 0%. Ver el adapter de yfinance.
        if not self.gross_profit:
            return None
        return _div(self.gross_profit, self.revenue)

    @property
    def operating_margin(self) -> Optional[float]:
        return _div(self.operating_income, self.revenue)

    @property
    def net_margin(self) -> Optional[float]:
        return _div(self.net_income, self.revenue)

    @property
    def fcf_margin(self) -> Optional[float]:
        return _div(self.free_cash_flow, self.revenue)

    # --- rentabilidad ------------------------------------------------------

    @property
    def effective_tax_rate(self) -> Optional[float]:
        if self.pretax_income is None or self.tax_provision is None or self.pretax_income <= 0:
            return None
        rate = self.tax_provision / self.pretax_income
        return rate if 0.0 <= rate <= 0.60 else None

    def roic(self, fallback_tax_rate: float = 0.21) -> Optional[float]:
        """NOPAT / capital invertido. Misma aproximación que el snapshot."""
        if self.ebit is None:
            return None
        invested = (self.total_debt or 0.0) + (self.total_equity or 0.0)
        if invested <= 0:
            return None
        tax = self.effective_tax_rate
        if tax is None:
            tax = fallback_tax_rate
        return (self.ebit * (1 - tax)) / invested

    @property
    def roe(self) -> Optional[float]:
        return _div(self.net_income, self.total_equity)

    @property
    def roa(self) -> Optional[float]:
        return _div(self.net_income, self.total_assets)

    @property
    def asset_turnover(self) -> Optional[float]:
        """Ingresos / activos totales. Cuánta venta genera cada peso de activo."""
        return _div(self.revenue, self.total_assets)

    # --- salud financiera --------------------------------------------------

    @property
    def net_debt(self) -> Optional[float]:
        if self.total_debt is None:
            return None
        return self.total_debt - (self.cash or 0.0)

    @property
    def net_debt_to_ebitda(self) -> Optional[float]:
        net = self.net_debt
        if net is None or not self.ebitda or self.ebitda <= 0:
            return None
        # Caja neta (deuda negativa) es una posición sana, no un ratio inválido.
        return net / self.ebitda

    @property
    def interest_coverage(self) -> Optional[float]:
        """EBIT / intereses. yfinance reporta el gasto con signo variable."""
        if self.ebit is None or not self.interest_expense:
            return None
        return self.ebit / abs(self.interest_expense)

    @property
    def current_ratio(self) -> Optional[float]:
        return _div(self.current_assets, self.current_liabilities)

    @property
    def cash_days(self) -> Optional[float]:
        """Días de operación que cubre la caja, a costo operativo corriente."""
        if self.cash is None or self.revenue is None or self.operating_income is None:
            return None
        opex = self.revenue - self.operating_income
        if opex <= 0:
            return None
        return self.cash / (opex / 365.0)

    @property
    def debt_to_equity(self) -> Optional[float]:
        return _div(self.total_debt, self.total_equity)

    @property
    def payout_ratio(self) -> Optional[float]:
        """Dividendos pagados sobre ganancia neta. None si la empresa pierde plata:
        un payout sobre una base negativa no tiene lectura (daría un % sin sentido)."""
        if self.dividends_paid is None or self.net_income is None or self.net_income <= 0:
            return None
        return abs(self.dividends_paid) / self.net_income

    # --- dilución ----------------------------------------------------------

    @property
    def sbc_to_revenue(self) -> Optional[float]:
        """SBC sobre ingresos. Un FCF lindo con SBC alto no es el mismo FCF."""
        if not self.stock_based_comp:
            return None
        return _div(abs(self.stock_based_comp), self.revenue)

    @property
    def fcf_after_sbc(self) -> Optional[float]:
        """FCF restando la compensación en acciones.

        El SBC no sale de caja, así que el FCF reportado lo ignora — pero diluye
        al accionista igual. Restarlo es la forma conservadora de leerlo.
        """
        if self.free_cash_flow is None or self.stock_based_comp is None:
            return None
        return self.free_cash_flow - abs(self.stock_based_comp)


@dataclass(frozen=True)
class FinancialHistory:
    """Ejercicios ordenados del más reciente al más viejo."""

    periods: tuple[AnnualPeriod, ...] = ()

    def __len__(self) -> int:
        return len(self.periods)

    @property
    def latest(self) -> Optional[AnnualPeriod]:
        return self.periods[0] if self.periods else None

    def series(self, metric: str, fallback_tax_rate: float = 0.21) -> list[tuple[Optional[date], Optional[float]]]:
        """Serie `(cierre, valor)` de una métrica, del más viejo al más reciente.

        Se invierte el orden a propósito: una tendencia se lee hacia adelante en
        el tiempo, no hacia atrás.
        """
        out = []
        for period in reversed(self.periods):
            if metric == "roic":
                value = period.roic(fallback_tax_rate)
            elif not hasattr(period, metric):
                raise KeyError(f"métrica desconocida: {metric}")
            else:
                value = getattr(period, metric)
            out.append((period.period_end, _clean(value)))
        return out

    def values(self, metric: str, fallback_tax_rate: float = 0.21) -> list[float]:
        """Sólo los valores presentes, en orden cronológico."""
        return [v for _, v in self.series(metric, fallback_tax_rate) if v is not None]

    def average(self, metric: str, fallback_tax_rate: float = 0.21) -> Optional[float]:
        values = self.values(metric, fallback_tax_rate)
        return sum(values) / len(values) if values else None

    def trend(self, metric: str, fallback_tax_rate: float = 0.21) -> Optional[float]:
        """Variación entre la primera y la última observación disponible.

        Deliberadamente simple: con 5 puntos, una regresión no dice más que la
        diferencia de puntas y sí sugiere una precisión que no hay.
        """
        values = self.values(metric, fallback_tax_rate)
        if len(values) < 2:
            return None
        return values[-1] - values[0]

    def cagr(self, metric: str) -> Optional[float]:
        """Tasa compuesta anual. None si la base no es positiva."""
        values = self.values(metric)
        if len(values) < 2 or values[0] <= 0 or values[-1] <= 0:
            return None
        years = len(values) - 1
        return (values[-1] / values[0]) ** (1 / years) - 1

    def yoy_growth(self, metric: str) -> list[tuple[Optional[date], Optional[float]]]:
        """Crecimiento año contra año. La base negativa no tiene lectura."""
        serie = self.series(metric)
        out: list[tuple[Optional[date], Optional[float]]] = []
        for (_, prev), (period_end, current) in zip(serie, serie[1:]):
            if prev is None or current is None or prev <= 0:
                out.append((period_end, None))
            else:
                out.append((period_end, (current - prev) / prev))
        return out


# --- extracción -------------------------------------------------------------


def _div(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or not denominator:
        return None
    return numerator / denominator


def _column_date(df: Any, index: int) -> Optional[date]:
    """Fecha de cierre de una columna.

    yfinance usa `pandas.Timestamp`, pero se aceptan también `date` y `datetime`
    planos: así los tests pueden armar DataFrames sin depender de pandas para
    construir las columnas.
    """
    try:
        value = df.columns[index]
    except (AttributeError, IndexError):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    for attr in ("date", "to_pydatetime"):
        if hasattr(value, attr):
            result = getattr(value, attr)()
            return result.date() if isinstance(result, datetime) else result
    return None


def build_history(
    financials: Any,
    balance_sheet: Any,
    cashflow: Any,
    max_years: int = TARGET_YEARS,
) -> FinancialHistory:
    """Arma la serie desde los tres DataFrames de yfinance.

    Se toma la cantidad de columnas del income statement como referencia: si el
    balance trae menos ejercicios, esos campos quedan en None en vez de correr
    el índice y mezclar años distintos en la misma fila.
    """
    years = min(st.column_count(financials) or 0, max_years)
    if years == 0:
        return FinancialHistory()

    periods: list[AnnualPeriod] = []
    for i in range(years):
        periods.append(
            AnnualPeriod(
                period_end=_column_date(financials, i),
                revenue=st.row_value(financials, st.REVENUE, i),
                gross_profit=st.row_value(financials, st.GROSS_PROFIT, i),
                operating_income=st.row_value(financials, st.OPERATING_INCOME, i),
                ebit=st.row_value(financials, st.EBIT, i),
                ebitda=st.row_value(financials, st.EBITDA, i),
                net_income=st.row_value(financials, st.NET_INCOME, i),
                eps_diluted=st.row_value(financials, st.EPS_DILUTED, i),
                interest_expense=st.row_value(financials, st.INTEREST_EXPENSE, i),
                pretax_income=st.row_value(financials, st.PRETAX_INCOME, i),
                tax_provision=st.row_value(financials, st.TAX_PROVISION, i),
                cash=st.row_value(balance_sheet, st.CASH, i),
                total_debt=st.total_debt(balance_sheet, i),
                total_equity=st.row_value(balance_sheet, st.EQUITY, i),
                current_assets=st.row_value(balance_sheet, st.CURRENT_ASSETS, i),
                current_liabilities=st.row_value(balance_sheet, st.CURRENT_LIABILITIES, i),
                shares_outstanding=st.row_value(balance_sheet, st.SHARES_OUTSTANDING, i),
                total_assets=st.row_value(balance_sheet, st.TOTAL_ASSETS, i),
                retained_earnings=st.row_value(balance_sheet, st.RETAINED_EARNINGS, i),
                operating_cash_flow=st.row_value(cashflow, st.OPERATING_CASH_FLOW, i),
                capex=st.row_value(cashflow, st.CAPEX, i),
                free_cash_flow=st.free_cash_flow(cashflow, i),
                stock_based_comp=st.row_value(cashflow, st.STOCK_BASED_COMP, i),
                buybacks=st.row_value(cashflow, st.BUYBACKS, i),
                dividends_paid=st.row_value(cashflow, st.DIVIDENDS_PAID, i),
            )
        )
    # Se descartan las columnas fantasma para que el conteo de ejercicios sea
    # honesto y las tablas no arranquen con una fila de guiones.
    return FinancialHistory(tuple(p for p in periods if p.has_data))


def latest_with(
    history: FinancialHistory, *required: str
) -> Optional[AnnualPeriod]:
    """Ejercicio más reciente que tenga todos los campos pedidos.

    Hace falta porque las empresas dejan de reportar líneas: Apple discontinuó
    `Interest Expense` como línea propia en 2024, pero la informó hasta 2023.
    Usar el último ejercicio a secas dejaría el dato en None teniéndolo.
    """
    for period in history.periods:
        if all(getattr(period, name, None) for name in required):
            return period
    return None


def describe_trend(
    values: Sequence[float],
    formatter: Callable[[float], str] = lambda v: f"{v:.1%}",
) -> str:
    """'12.0% → 18.4% (+6.4 pp)'. Para las tablas del informe."""
    if not values:
        return "sin datos"
    if len(values) == 1:
        return formatter(values[0])
    delta = values[-1] - values[0]
    return f"{formatter(values[0])} → {formatter(values[-1])} ({delta:+.1%} pp)"
