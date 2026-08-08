"""Dobles de prueba para yfinance.

Ningún test toca la red. `FakeTicker` imita la superficie de `yfinance.Ticker`
que consume el adapter: `.info`, `.financials`, `.balance_sheet`, `.cashflow`.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

import pandas as pd
import pytest

PERIODS = (pd.Timestamp("2024-12-31"), pd.Timestamp("2023-12-31"))


def frame(rows: dict[str, Sequence[Optional[float]]], periods: Sequence[Any] = PERIODS) -> pd.DataFrame:
    """DataFrame estilo yfinance: filas = líneas contables, columnas = ejercicios.

    La columna 0 es el ejercicio más reciente.
    """
    width = max(len(v) for v in rows.values()) if rows else len(periods)
    cols = list(periods)[:width]
    padded = {k: list(v) + [None] * (len(cols) - len(v)) for k, v in rows.items()}
    return pd.DataFrame(padded, index=cols).T


class FakeTicker:
    """Doble de `yfinance.Ticker`."""

    def __init__(
        self,
        info: Optional[dict[str, Any]] = None,
        financials: Optional[pd.DataFrame] = None,
        balance_sheet: Optional[pd.DataFrame] = None,
        cashflow: Optional[pd.DataFrame] = None,
        raises: Optional[Exception] = None,
    ):
        self._raises = raises
        self.info = info if info is not None else {}
        self.financials = financials if financials is not None else pd.DataFrame()
        self.balance_sheet = balance_sheet if balance_sheet is not None else pd.DataFrame()
        self.cashflow = cashflow if cashflow is not None else pd.DataFrame()

    def __getattribute__(self, name: str) -> Any:
        raises = object.__getattribute__(self, "_raises")
        if raises is not None and name in ("info", "financials", "balance_sheet", "cashflow"):
            raise raises
        return object.__getattribute__(self, name)


def factory_for(tickers: dict[str, FakeTicker]):
    """Devuelve una `ticker_factory` que sirve dobles desde un dict."""

    def _factory(symbol: str) -> FakeTicker:
        if symbol not in tickers:
            raise KeyError(f"ticker desconocido en el doble: {symbol}")
        return tickers[symbol]

    return _factory


# --- fixtures ---------------------------------------------------------------

HEALTHY_INFO = {
    "sector": "Technology",
    "industry": "Consumer Electronics",
    "longName": "Testco Inc.",
    "currency": "USD",
    "financialCurrency": "USD",
    "marketCap": 500_000.0,
    "currentPrice": 50.0,
    "trailingPE": 22.0,
    "priceToBook": 8.3,
    "dividendYield": 0.005,
}

HEALTHY_FINANCIALS = {
    "Total Revenue": [100_000.0, 90_000.0],
    "Gross Profit": [45_000.0, 40_000.0],
    "EBIT": [30_000.0, 26_000.0],
    "Pretax Income": [28_000.0, 24_000.0],
    "Tax Provision": [5_600.0, 4_800.0],
    "Net Income": [22_400.0, 19_200.0],
}

HEALTHY_BALANCE = {
    "Total Debt": [30_000.0, 33_000.0],
    "Stockholders Equity": [60_000.0, 55_000.0],
}

HEALTHY_CASHFLOW = {
    "Operating Cash Flow": [30_000.0, 27_000.0],
    "Capital Expenditure": [-5_000.0, -4_000.0],
    "Free Cash Flow": [25_000.0, 23_000.0],
}


@pytest.fixture
def healthy_ticker() -> FakeTicker:
    """Empresa con todos los estados contables completos."""
    return FakeTicker(
        info=dict(HEALTHY_INFO),
        financials=frame(HEALTHY_FINANCIALS),
        balance_sheet=frame(HEALTHY_BALANCE),
        cashflow=frame(HEALTHY_CASHFLOW),
    )


@pytest.fixture
def healthy_factory(healthy_ticker: FakeTicker):
    return factory_for({"TEST": healthy_ticker})
