"""Normalización del snapshot: unidades, sanity checks y moneda.

Esta capa es la que le permite al scorer asumir un contrato fijo:
  - un valor presente es un valor con sentido económico (los absurdos se
    convierten en `None` + warning, no se "arreglan" inventando);
  - `sector` está canonizado, así el agrupamiento por sector no se parte en dos
    grupos por diferencia de nomenclatura entre fuentes;
  - los montos absolutos están en la moneda pedida, si es que se pidió una.

Las unidades (porcentaje vs fracción) NO se corrigen acá: son una rareza de cada
proveedor y las arregla su adapter, que es el único que sabe qué significa cada
campo. Adivinar la unidad por magnitud no funciona —un dividend yield de 0.32 es
igual de plausible leído como 32% que como 0.32%—; el normalizer sólo verifica
que el resultado caiga en un rango creíble.

Es una función pura: mismo snapshot de entrada, mismo de salida. Sin red.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..models import QUOTE_MONETARY_FIELDS, STATEMENT_MONETARY_FIELDS, FundamentalSnapshot
from .fx import FxProvider, NullFxProvider

logger = logging.getLogger(__name__)

#: Rangos plausibles por métrica. Fuera de esto el dato es error del proveedor
#: (o una situación contable que el ratio no describe), y entra al ranking como
#: faltante en vez de contaminarlo.
PLAUSIBLE_RANGES: dict[str, tuple[float, float]] = {
    "pe": (0.0, 1000.0),
    "pb": (0.0, 200.0),
    "roe": (-10.0, 10.0),
    "roic": (-5.0, 5.0),
    "debt_to_equity": (0.0, 100.0),
    "debt_to_equity_prev": (0.0, 100.0),
    "fcf_yield": (-5.0, 5.0),
    "revenue_growth_yoy": (-1.0, 50.0),
    "gross_margin": (-5.0, 1.0),
    "net_margin": (-10.0, 5.0),
    "dividend_yield": (0.0, 1.0),
}

#: Sinónimos de sector entre fuentes. La clave es lo que puede venir; el valor,
#: la etiqueta canónica.
SECTOR_ALIASES: dict[str, str] = {
    "financial": "Financial Services",
    "financials": "Financial Services",
    "financial services": "Financial Services",
    "banks": "Financial Services",
    "tech": "Technology",
    "information technology": "Technology",
    "technology": "Technology",
    "consumer cyclical": "Consumer Cyclical",
    "consumer discretionary": "Consumer Cyclical",
    "consumer defensive": "Consumer Defensive",
    "consumer staples": "Consumer Defensive",
    "health care": "Healthcare",
    "healthcare": "Healthcare",
    "basic materials": "Basic Materials",
    "materials": "Basic Materials",
    "communication services": "Communication Services",
    "telecommunication services": "Communication Services",
    "real estate": "Real Estate",
    "energy": "Energy",
    "utilities": "Utilities",
    "industrials": "Industrials",
}

UNKNOWN_SECTOR = "Unknown"


def canonical_sector(sector: Optional[str]) -> str:
    if not sector or not sector.strip():
        return UNKNOWN_SECTOR
    key = sector.strip().lower()
    return SECTOR_ALIASES.get(key, sector.strip())


def normalize(
    snapshot: FundamentalSnapshot,
    *,
    target_currency: Optional[str] = None,
    fx: Optional[FxProvider] = None,
) -> FundamentalSnapshot:
    """Devuelve una versión canónica de `snapshot`.

    Si `target_currency` es None (default) no se toca la moneda; los ratios
    siguen siendo comparables igual.
    """
    changes: dict[str, object] = {}
    warnings = list(snapshot.warnings)

    changes["ticker"] = snapshot.ticker.upper()
    changes["source_ticker"] = snapshot.source_ticker.upper()
    changes["sector"] = canonical_sector(snapshot.sector)
    if snapshot.currency:
        changes["currency"] = snapshot.currency.upper()
    if snapshot.quote_currency:
        changes["quote_currency"] = snapshot.quote_currency.upper()

    # --- sanity checks -----------------------------------------------------
    for name, (low, high) in PLAUSIBLE_RANGES.items():
        value = changes.get(name, getattr(snapshot, name))
        if value is None:
            continue
        value = float(value)  # type: ignore[arg-type]
        if not (low <= value <= high):
            changes[name] = None
            warnings.append(f"{name}={value:g} fuera del rango plausible [{low:g}, {high:g}]; se descarta")

    # --- moneda ------------------------------------------------------------
    # Los montos del balance y los de mercado pueden estar en monedas distintas
    # (un ADR argentino reporta en ARS y cotiza en USD), así que se convierten
    # como dos grupos separados y cada uno con su propia cotización.
    if target_currency:
        target = target_currency.upper()
        provider = fx or NullFxProvider()
        groups = (
            ("currency", snapshot.currency, STATEMENT_MONETARY_FIELDS),
            ("quote_currency", snapshot.quote_currency or snapshot.currency, QUOTE_MONETARY_FIELDS),
        )
        for currency_field, source_currency, money_fields in groups:
            source = (source_currency or "").upper()
            if not source:
                warnings.append(f"no se convirtió a {target}: el snapshot no declara {currency_field}")
                continue
            if source == target:
                changes[currency_field] = target
                continue
            rate = provider.rate(source, target)
            if rate is None:
                warnings.append(f"no se convirtió a {target}: sin cotización {source}->{target}")
                continue
            for name in money_fields:
                value = getattr(snapshot, name)
                if value is not None:
                    changes[name] = value * rate
            changes[currency_field] = target
            warnings.append(f"montos convertidos {source}->{target} @ {rate:g}")

    changes["warnings"] = tuple(warnings)
    return snapshot.replace(**changes)
