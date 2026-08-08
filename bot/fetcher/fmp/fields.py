"""Lookup tolerante de campos de FMP.

Mismo criterio que `statements.py` con yfinance: nunca se accede a una clave
exacta, se prueba una lista de alias. FMP renombró campos entre `/api/v3/` y
`/stable/` (`mktCap`→`marketCap`, `epsdiluted`→`epsDiluted`,
`totalStockholdersEquity`→`totalEquity`), y así el adapter sobrevive al que use
la key del usuario sin que tengamos que adivinar cuál es.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Optional, Sequence


def num(obj: dict[str, Any], aliases: Sequence[str]) -> Optional[float]:
    """Primer alias presente convertido a float. None si ninguno sirve."""
    for key in aliases:
        if key not in obj:
            continue
        value = obj[key]
        if value is None or isinstance(value, bool):
            continue
        try:
            out = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isnan(out) and not math.isinf(out):
            return out
    return None


def text(obj: dict[str, Any], aliases: Sequence[str]) -> Optional[str]:
    for key in aliases:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def parse_date(obj: dict[str, Any], aliases: Sequence[str] = ("date",)) -> Optional[date]:
    for key in aliases:
        value = obj.get(key)
        if not value:
            continue
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
    return None


# --- income statement -------------------------------------------------------

REVENUE = ("revenue", "totalRevenue")
COST_OF_REVENUE = ("costOfRevenue",)
GROSS_PROFIT = ("grossProfit",)
OPERATING_INCOME = ("operatingIncome",)
# FMP agregó `ebit` en stable; en v3 no estaba, así que operatingIncome hace de
# proxy —es lo que ya hacía el adapter de yfinance—.
EBIT = ("ebit", "operatingIncome")
EBITDA = ("ebitda",)  # el monto; `ebitdaratio` es otra cosa y no se quiere
NET_INCOME = ("netIncome", "bottomLineNetIncome")
EPS_DILUTED = ("epsDiluted", "epsdiluted", "eps")
INTEREST_EXPENSE = ("interestExpense",)
PRETAX_INCOME = ("incomeBeforeTax", "pretaxIncome")
TAX_PROVISION = ("incomeTaxExpense",)
DILUTED_SHARES = ("weightedAverageShsOutDil", "weightedAverageShsOut")

# --- balance sheet ----------------------------------------------------------

CASH = ("cashAndShortTermInvestments", "cashAndCashEquivalents")
TOTAL_DEBT = ("totalDebt",)
LONG_TERM_DEBT = ("longTermDebt",)
SHORT_TERM_DEBT = ("shortTermDebt",)
EQUITY = ("totalStockholdersEquity", "totalEquity", "totalStockholderEquity")
CURRENT_ASSETS = ("totalCurrentAssets",)
CURRENT_LIABILITIES = ("totalCurrentLiabilities",)
SHARES_OUTSTANDING = ("weightedAverageShsOutDil", "weightedAverageShsOut")
TOTAL_ASSETS = ("totalAssets",)
RETAINED_EARNINGS = ("retainedEarnings",)

# --- cash flow --------------------------------------------------------------

OPERATING_CASH_FLOW = ("operatingCashFlow", "netCashProvidedByOperatingActivities")
CAPEX = ("capitalExpenditure",)
FREE_CASH_FLOW = ("freeCashFlow",)
STOCK_BASED_COMP = ("stockBasedCompensation",)
BUYBACKS = ("commonStockRepurchased", "netStockRepurchase")
DIVIDENDS_PAID = ("dividendsPaid", "commonDividendsPaid", "netDividendsPaid")

# --- profile ----------------------------------------------------------------

COMPANY_NAME = ("companyName", "name")
SECTOR = ("sector",)
INDUSTRY = ("industry",)
CURRENCY = ("currency", "reportedCurrency")
PRICE = ("price",)
MARKET_CAP = ("marketCap", "mktCap")
BETA = ("beta",)
LAST_DIVIDEND = ("lastDividend", "lastDiv")


def total_debt(balance: dict[str, Any]) -> Optional[float]:
    """Deuda total, o la suma de sus partes si no viene la línea agregada."""
    direct = num(balance, TOTAL_DEBT)
    if direct is not None:
        return direct
    long_term = num(balance, LONG_TERM_DEBT)
    short_term = num(balance, SHORT_TERM_DEBT)
    if long_term is None and short_term is None:
        return None
    return (long_term or 0.0) + (short_term or 0.0)


def free_cash_flow(cashflow: dict[str, Any]) -> Optional[float]:
    """FCF reportado, o CFO menos capex. FMP reporta capex con signo negativo."""
    direct = num(cashflow, FREE_CASH_FLOW)
    if direct is not None:
        return direct
    cfo = num(cashflow, OPERATING_CASH_FLOW)
    if cfo is None:
        return None
    capex = num(cashflow, CAPEX)
    if capex is None:
        return None
    return cfo + capex if capex < 0 else cfo - capex
