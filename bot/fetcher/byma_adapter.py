"""Adapter para tickers locales de BYMA (sufijo `.BA`).

yfinance sí devuelve precio para `GGAL.BA`, pero los fundamentals vienen vacíos
o inconsistentes. La estrategia es resolver el subyacente listado en EE.UU.
(el ADR de la empresa argentina, o la acción original en el caso de un CEDEAR)
y pedir los fundamentals ahí.

Esto es correcto sin ajustar por el ratio de conversión del CEDEAR: todos los
campos que rankea el scorer son ratios adimensionales. Los campos monetarios del
snapshot quedan en la moneda del subyacente (USD), lo cual se declara en
`currency` y el normalizer respeta.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..models import FundamentalSnapshot, UnmappedTickerError
from .yfinance_adapter import YFinanceAdapter

logger = logging.getLogger(__name__)

BYMA_SUFFIX = ".BA"
DEFAULT_MAP_PATH = Path(__file__).with_name("cedear_map.json")


@dataclass(frozen=True)
class UnderlyingRef:
    local_ticker: str
    underlying: str
    exchange: Optional[str] = None
    kind: Optional[str] = None
    name: Optional[str] = None


class CedearMap:
    """Tabla local .BA -> subyacente, cargada de JSON."""

    def __init__(
        self,
        mappings: dict[str, dict[str, str]],
        no_underlying: Optional[dict[str, str]] = None,
    ):
        self._mappings = {k.upper(): v for k, v in mappings.items()}
        self._no_underlying = {k.upper(): v for k, v in (no_underlying or {}).items()}

    @classmethod
    def load(cls, path: Path | str = DEFAULT_MAP_PATH) -> "CedearMap":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(data.get("mappings", {}), data.get("no_underlying", {}))

    def resolve(self, ticker: str) -> UnderlyingRef:
        key = ticker.upper()
        entry = self._mappings.get(key)
        if entry:
            return UnderlyingRef(
                local_ticker=key,
                underlying=entry["underlying"],
                exchange=entry.get("exchange"),
                kind=entry.get("kind"),
                name=entry.get("name"),
            )
        reason = self._no_underlying.get(key)
        if reason:
            raise UnmappedTickerError(ticker, f"sin subyacente en EE.UU.: {reason}")
        raise UnmappedTickerError(
            ticker,
            "no está en cedear_map.json; agregalo a mano si tiene ADR o CEDEAR",
        )

    def __contains__(self, ticker: str) -> bool:
        return ticker.upper() in self._mappings

    def __len__(self) -> int:
        return len(self._mappings)


def is_byma_ticker(ticker: str) -> bool:
    return ticker.upper().endswith(BYMA_SUFFIX)


def resolve_symbol(
    ticker: str, cedear_map: Optional[CedearMap] = None
) -> tuple[str, Optional[str]]:
    """Símbolo a pedir en yfinance y, si aplica, el ticker local original.

    Devuelve `(symbol, requested_as)`. Para un ticker que no es `.BA`,
    `requested_as` es `None` y `symbol` es el mismo ticker. Para un CEDEAR/ADR,
    `symbol` es el subyacente y `requested_as` es el ticker local pedido —así
    lo que sea que consuma el resultado sabe qué etiqueta mostrarle al usuario.

    Comparte la resolución entre la CLI y la web: las dos necesitan lo mismo
    antes de pedirle nada a `build_profile` o al service del screener.
    """
    key = ticker.strip().upper()
    if not is_byma_ticker(key):
        return key, None
    ref = (cedear_map or CedearMap.load()).resolve(key)
    return ref.underlying, key


class BymaAdapter:
    """Resuelve el subyacente y delega el fetch en el adapter de yfinance."""

    source = "byma"

    def __init__(
        self,
        cedear_map: Optional[CedearMap] = None,
        underlying_adapter: Optional[YFinanceAdapter] = None,
    ):
        self._map = cedear_map if cedear_map is not None else CedearMap.load()
        self._underlying = underlying_adapter or YFinanceAdapter()

    def fetch(self, ticker: str) -> FundamentalSnapshot:
        ref = self._map.resolve(ticker)
        logger.debug("%s -> %s (%s)", ticker, ref.underlying, ref.kind)

        # El snapshot se etiqueta con el ticker local que pidió el usuario, pero
        # guarda el símbolo realmente consultado en `source_ticker`.
        snapshot = self._underlying.fetch(ref.underlying, requested_as=ref.local_ticker)

        note = (
            f"fundamentals del subyacente {ref.underlying}"
            f"{f' ({ref.exchange})' if ref.exchange else ''}"
            f", no del listado local {ref.local_ticker}"
        )
        return snapshot.replace(source=self.source).with_warning(note)
