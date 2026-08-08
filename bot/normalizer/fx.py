"""Conversión de moneda para los campos monetarios del snapshot.

Sólo aplica a montos absolutos (market cap, revenue, deuda...). Los ratios son
adimensionales y por definición no se convierten: un P/E es el mismo número en
pesos que en dólares mientras numerador y denominador estén en la misma moneda.

Por defecto el pipeline NO convierte nada: cada snapshot se queda en su moneda
declarada y la comparación sectorial sigue siendo válida porque se hace sobre
ratios. La conversión existe para cuando se quieran mostrar magnitudes juntas.
"""

from __future__ import annotations

from typing import Optional, Protocol


class FxProvider(Protocol):
    """Fuente de tipos de cambio. Inyectable para no atarse a una API."""

    def rate(self, from_currency: str, to_currency: str) -> Optional[float]:
        """Cuántas unidades de `to_currency` compra 1 de `from_currency`."""
        ...


class StaticFxProvider:
    """Tipos de cambio fijos, provistos por el llamador.

    Determinístico y sin red: es lo que usan los tests y el modo offline. Los
    valores los pone quien lo instancia, a propósito — hardcodear un USD/ARS en
    el código fuente garantiza que quede viejo y mienta en silencio.
    """

    def __init__(self, rates_to_usd: dict[str, float]):
        # rates_to_usd[X] = cuántos USD vale 1 unidad de X.
        self._rates = {"USD": 1.0, **{k.upper(): float(v) for k, v in rates_to_usd.items()}}

    def rate(self, from_currency: str, to_currency: str) -> Optional[float]:
        src = self._rates.get((from_currency or "").upper())
        dst = self._rates.get((to_currency or "").upper())
        if src is None or dst is None or dst == 0:
            return None
        return src / dst


class NullFxProvider:
    """No convierte nada; sólo la identidad."""

    def rate(self, from_currency: str, to_currency: str) -> Optional[float]:
        if (from_currency or "").upper() == (to_currency or "").upper():
            return 1.0
        return None
