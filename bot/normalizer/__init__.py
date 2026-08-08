"""Normalización a modelo común: unidades, rangos plausibles, moneda, sector."""

from .fx import FxProvider, NullFxProvider, StaticFxProvider
from .normalize import (
    PLAUSIBLE_RANGES,
    UNKNOWN_SECTOR,
    canonical_sector,
    normalize,
)

__all__ = [
    "normalize",
    "canonical_sector",
    "PLAUSIBLE_RANGES",
    "UNKNOWN_SECTOR",
    "FxProvider",
    "StaticFxProvider",
    "NullFxProvider",
]
