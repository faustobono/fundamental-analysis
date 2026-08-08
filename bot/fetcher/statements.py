"""Acceso tolerante a los DataFrames de estados contables de yfinance.

yfinance devuelve `financials` / `balance_sheet` / `cashflow` como DataFrames
donde las filas son líneas del balance y las columnas son cierres de ejercicio,
ordenadas del más reciente al más viejo. Los nombres de fila cambian entre
versiones de yfinance y entre empresas (un banco no reporta 'Gross Profit'), así
que nunca indexamos por nombre exacto: buscamos por lista de alias,
case-insensitive.
"""

from __future__ import annotations

import math
from typing import Any, Optional, Sequence

try:  # pragma: no cover - pandas siempre está en runtime, pero los tests
    import pandas as pd  # unitarios no deberían depender de eso.
except ImportError:  # pragma: no cover
    pd = None  # type: ignore[assignment]


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(math.isnan(float(value)))
    except (TypeError, ValueError):
        return True


def is_empty(df: Any) -> bool:
    """True si el estado contable no vino o vino vacío."""
    if df is None:
        return True
    empty = getattr(df, "empty", None)
    if empty is not None:
        return bool(empty)
    return len(df) == 0


def column_count(df: Any) -> int:
    if is_empty(df):
        return 0
    return len(df.columns)


def row_value(df: Any, aliases: Sequence[str], column: int = 0) -> Optional[float]:
    """Valor de la primera fila que matchee alguno de los `aliases`.

    `column=0` es el ejercicio más reciente, `column=1` el anterior, etc.
    Devuelve None si no hay fila, no hay columna, o el valor es NaN.
    """
    if is_empty(df) or column >= column_count(df):
        return None

    normalized = {str(idx).strip().lower(): idx for idx in df.index}
    for alias in aliases:
        key = alias.strip().lower()
        if key not in normalized:
            continue
        try:
            value = df.loc[normalized[key]].iloc[column]
        except (KeyError, IndexError):
            continue
        # Una empresa puede reportar la misma línea dos veces (índice duplicado);
        # en ese caso .loc devuelve una Series y nos quedamos con el primer dato
        # no nulo.
        if pd is not None and isinstance(value, pd.Series):
            value = next((v for v in value if not _is_missing(v)), None)
        if not _is_missing(value):
            return float(value)
    return None


# --- alias por línea contable ---------------------------------------------
# Ordenados por preferencia: el primero que exista gana.

REVENUE = ("Total Revenue", "Operating Revenue", "Revenue")
GROSS_PROFIT = ("Gross Profit",)
COST_OF_REVENUE = ("Cost Of Revenue", "Cost of Revenue")
EBIT = ("EBIT", "Operating Income", "Total Operating Income As Reported")
OPERATING_INCOME = ("Operating Income", "Total Operating Income As Reported", "EBIT")
EBITDA = ("EBITDA", "Normalized EBITDA")
INTEREST_EXPENSE = (
    "Interest Expense",
    "Interest Expense Non Operating",
    "Net Interest Income",
)
EPS_DILUTED = ("Diluted EPS", "Basic EPS")
DILUTED_SHARES = ("Diluted Average Shares", "Basic Average Shares")
NET_INCOME = (
    "Net Income",
    "Net Income Common Stockholders",
    "Net Income Continuous Operations",
    "Net Income From Continuing Operation Net Minority Interest",
)
PRETAX_INCOME = ("Pretax Income", "Income Before Tax")
TAX_PROVISION = ("Tax Provision", "Income Tax Expense")

TOTAL_DEBT = ("Total Debt",)
LONG_TERM_DEBT = ("Long Term Debt", "Long Term Debt And Capital Lease Obligation")
SHORT_TERM_DEBT = (
    "Current Debt",
    "Short Term Debt",
    "Current Debt And Capital Lease Obligation",
)
EQUITY = (
    "Stockholders Equity",
    "Total Stockholder Equity",
    "Common Stock Equity",
    "Total Equity Gross Minority Interest",
)
CASH = (
    "Cash Cash Equivalents And Short Term Investments",
    "Cash And Cash Equivalents",
    "Cash Financial",
)
CURRENT_ASSETS = ("Current Assets", "Total Current Assets")
CURRENT_LIABILITIES = ("Current Liabilities", "Total Current Liabilities")
SHARES_OUTSTANDING = ("Ordinary Shares Number", "Share Issued", "Common Stock Shares Outstanding")

FREE_CASH_FLOW = ("Free Cash Flow",)
OPERATING_CASH_FLOW = (
    "Operating Cash Flow",
    "Total Cash From Operating Activities",
    "Cash Flow From Continuing Operating Activities",
)
CAPEX = ("Capital Expenditure", "Capital Expenditures")
STOCK_BASED_COMP = ("Stock Based Compensation",)
BUYBACKS = ("Repurchase Of Capital Stock",)


def total_debt(balance_sheet: Any, column: int = 0) -> Optional[float]:
    """Deuda total, reconstruida desde sus partes si no viene la línea agregada."""
    direct = row_value(balance_sheet, TOTAL_DEBT, column)
    if direct is not None:
        return direct
    long_term = row_value(balance_sheet, LONG_TERM_DEBT, column)
    short_term = row_value(balance_sheet, SHORT_TERM_DEBT, column)
    if long_term is None and short_term is None:
        return None
    return (long_term or 0.0) + (short_term or 0.0)


def free_cash_flow(cashflow: Any, column: int = 0) -> Optional[float]:
    """FCF reportado, o CFO menos capex.

    yfinance reporta capex con signo negativo, así que se suma.
    """
    direct = row_value(cashflow, FREE_CASH_FLOW, column)
    if direct is not None:
        return direct
    cfo = row_value(cashflow, OPERATING_CASH_FLOW, column)
    if cfo is None:
        return None
    capex = row_value(cashflow, CAPEX, column)
    if capex is None:
        return None
    return cfo + capex if capex < 0 else cfo - capex
