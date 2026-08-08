"""Fetch extendido de un ticker: series contables, precios y estimaciones.

El adapter normal trae una foto para rankear un universo. Esto trae todo lo que
necesita un informe de una sola empresa: cinco ejercicios, cinco años de precios
mensuales y las proyecciones de analistas.

Es más caro por ticker, así que corre de a uno y no sobre el universo entero.
Misma `ticker_factory` inyectable que el resto: los tests no tocan la red.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from ..models import NoDataError, UpstreamError
from .yfinance_adapter import TickerFactory, _default_ticker_factory

logger = logging.getLogger(__name__)

#: Ventana de precios para la mediana histórica de múltiplos.
PRICE_PERIOD = "5y"
PRICE_INTERVAL = "1mo"


@dataclass
class DeepData:
    """Todo lo crudo de un ticker, antes de calcular nada."""

    ticker: str
    info: dict[str, Any] = field(default_factory=dict)
    financials: Any = None
    balance_sheet: Any = None
    cashflow: Any = None
    prices: list[tuple[date, float]] = field(default_factory=list)
    earnings_estimate: Any = None
    revenue_estimate: Any = None
    growth_estimates: Any = None
    warnings: list[str] = field(default_factory=list)


def _extract_prices(handle: Any) -> list[tuple[date, float]]:
    """Cierres mensuales como `(fecha, precio)`."""
    try:
        frame = handle.history(period=PRICE_PERIOD, interval=PRICE_INTERVAL)
    except Exception as exc:  # noqa: BLE001
        logger.debug("sin histórico de precios: %s", exc)
        return []
    if frame is None or getattr(frame, "empty", True) or "Close" not in frame.columns:
        return []

    out: list[tuple[date, float]] = []
    for stamp, value in frame["Close"].items():
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        if price != price or price <= 0:  # NaN o no positivo
            continue
        moment = stamp.date() if hasattr(stamp, "date") else stamp
        if isinstance(moment, date):
            out.append((moment, price))
    return out


def _optional_attr(handle: Any, name: str) -> Any:
    """Las estimaciones no existen para todos los tickers; su ausencia no es error."""
    try:
        return getattr(handle, name, None)
    except Exception as exc:  # noqa: BLE001
        logger.debug("%s no disponible: %s", name, exc)
        return None


def fetch_deep(
    ticker: str,
    ticker_factory: TickerFactory = _default_ticker_factory,
    *,
    requested_as: Optional[str] = None,
) -> DeepData:
    """Trae todo lo necesario para un informe. Lanza si no hay nada utilizable."""
    label = requested_as or ticker
    try:
        handle = ticker_factory(ticker)
        info = dict(getattr(handle, "info", None) or {})
        financials = getattr(handle, "financials", None)
        balance_sheet = getattr(handle, "balance_sheet", None)
        cashflow = getattr(handle, "cashflow", None)
    except Exception as exc:  # noqa: BLE001
        raise UpstreamError(label, f"yfinance falló: {exc}") from exc

    if not info and financials is None:
        raise NoDataError(label, "yfinance no devolvió nada")

    warnings: list[str] = []
    prices = _extract_prices(handle)
    if not prices:
        warnings.append("sin histórico de precios: no hay medianas de valuación")

    return DeepData(
        ticker=label,
        info=info,
        financials=financials,
        balance_sheet=balance_sheet,
        cashflow=cashflow,
        prices=prices,
        earnings_estimate=_optional_attr(handle, "earnings_estimate"),
        revenue_estimate=_optional_attr(handle, "revenue_estimate"),
        growth_estimates=_optional_attr(handle, "growth_estimates"),
        warnings=warnings,
    )
