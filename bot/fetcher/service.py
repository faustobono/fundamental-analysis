"""Orquestación del fetch: ruteo por mercado, cache y tolerancia a fallas.

Es el único punto que el resto del bot usa para conseguir snapshots. Reglas:
  - `XXXX.BA` va al adapter de BYMA, el resto a yfinance;
  - se consulta el cache antes de salir a la red, y se guarda el resultado ya
    normalizado;
  - un ticker que falla NO corta el batch: se registra en `failures` y se sigue.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, Optional, Protocol

from ..models import FetchError, FundamentalSnapshot, UpstreamError
from ..normalizer.normalize import normalize
from ..normalizer.fx import FxProvider
from .byma_adapter import BymaAdapter, is_byma_ticker
from .cache import NullCache, SnapshotCache
from .yfinance_adapter import YFinanceAdapter

logger = logging.getLogger(__name__)


class SnapshotSource(Protocol):
    def fetch(self, ticker: str, *, requested_as: Optional[str] = None) -> FundamentalSnapshot: ...


class SnapshotStore(Protocol):
    def get(self, ticker: str) -> Optional[FundamentalSnapshot]: ...
    def put(self, snapshot: FundamentalSnapshot) -> None: ...


@dataclass
class BatchResult:
    """Resultado de un batch: lo que salió bien y lo que no, por separado."""

    snapshots: list[FundamentalSnapshot] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)
    cache_hits: int = 0

    @property
    def ok(self) -> int:
        return len(self.snapshots)

    def by_sector(self) -> dict[str, list[FundamentalSnapshot]]:
        grouped: dict[str, list[FundamentalSnapshot]] = {}
        for snapshot in self.snapshots:
            grouped.setdefault(snapshot.sector or "Unknown", []).append(snapshot)
        return grouped


class FundamentalsService:
    def __init__(
        self,
        cache: SnapshotStore | None = None,
        yfinance_adapter: Optional[SnapshotSource] = None,
        byma_adapter: Optional[SnapshotSource] = None,
        *,
        target_currency: Optional[str] = None,
        fx: Optional[FxProvider] = None,
    ):
        self._cache: SnapshotStore = cache if cache is not None else NullCache()
        self._yfinance = yfinance_adapter or YFinanceAdapter()
        self._byma = byma_adapter or BymaAdapter()
        self._target_currency = target_currency
        self._fx = fx

    # --- API ---------------------------------------------------------------

    def get(self, ticker: str, *, use_cache: bool = True) -> FundamentalSnapshot:
        """Snapshot normalizado de un ticker. Propaga `FetchError`."""
        key = ticker.strip().upper()
        if not key:
            raise ValueError("ticker vacío")

        if use_cache:
            cached = self._cache.get(key)
            if cached is not None:
                logger.debug("%s: cache hit", key)
                return cached

        adapter = self._byma if is_byma_ticker(key) else self._yfinance
        raw = adapter.fetch(key)
        snapshot = normalize(raw, target_currency=self._target_currency, fx=self._fx)
        self._cache.put(snapshot)
        return snapshot

    def get_many(self, tickers: Iterable[str], *, use_cache: bool = True) -> BatchResult:
        """Fetch de un batch. Un ticker roto no rompe los demás."""
        result = BatchResult()
        seen: set[str] = set()

        for raw_ticker in tickers:
            key = raw_ticker.strip().upper()
            if not key or key in seen:
                continue
            seen.add(key)

            if use_cache:
                cached = self._cache.get(key)
                if cached is not None:
                    result.snapshots.append(cached)
                    result.cache_hits += 1
                    continue

            try:
                result.snapshots.append(self.get(key, use_cache=False))
            except FetchError as exc:
                # Falla esperable de un proveedor: se loguea y sigue el batch.
                logger.warning("%s: %s", key, exc)
                result.failures.append((key, str(exc)))
            except Exception as exc:  # noqa: BLE001
                # Bug nuestro o algo inesperado del proveedor. Tampoco puede
                # tirar abajo un batch de 200 tickers, pero sí merece stacktrace.
                logger.exception("%s: error inesperado", key)
                result.failures.append((key, f"error inesperado: {exc}"))

        logger.info(
            "batch terminado: %d ok (%d de cache), %d con error",
            result.ok,
            result.cache_hits,
            len(result.failures),
        )
        return result


#: Proveedores de datos disponibles. yfinance scrapea Yahoo (anda local, se
#: bloquea en la nube); fmp es una API con key, la que sirve para desplegar.
PROVIDERS = ("yfinance", "fmp")
DEFAULT_PROVIDER = "yfinance"


class _FmpWithYfinanceFallback:
    """FMP como fuente primaria; si un ticker puntual devuelve HTTP 402 (plan
    pago requerido), reintenta con yfinance en vez de fallar el ticker entero.

    Confirmado contra producción: FMP exige un plan pago para los estados
    contables de varios CEDEARs/ADRs (GGAL, YPF, BMA), aunque coticen en
    NASDAQ/NYSE — no es un bug del pipeline, es una restricción del free
    tier. yfinance puede bloquear IPs de datacenter en la nube, así que este
    fallback no garantiza éxito ahí — pero nunca empeora el resultado (si
    también falla, el error final sigue siendo claro) y localmente resuelve
    el caso limpio.
    """

    def __init__(self, primary: SnapshotSource, fallback: Optional[SnapshotSource] = None):
        self._primary = primary
        self._fallback = fallback or YFinanceAdapter()

    def fetch(self, ticker: str, *, requested_as: Optional[str] = None) -> FundamentalSnapshot:
        try:
            return self._primary.fetch(ticker, requested_as=requested_as)
        except UpstreamError as exc:
            if exc.status_code != 402:
                raise
            logger.info("%s: FMP exige plan pago (402), reintento con yfinance", ticker)
            try:
                snapshot = self._fallback.fetch(ticker, requested_as=requested_as)
            except FetchError as fallback_exc:
                raise UpstreamError(
                    ticker,
                    "FMP exige un plan pago para este ticker (402) y el fallback a "
                    f"yfinance también falló: {fallback_exc}",
                ) from fallback_exc
            return snapshot.with_warning(
                "FMP exige un plan pago para este ticker (HTTP 402); se usó "
                "yfinance como alternativa para poder traer los datos."
            )


def _build_adapters(provider: str) -> tuple[SnapshotSource, SnapshotSource]:
    """Devuelve (adapter primario, adapter BYMA) según el proveedor.

    El adapter de BYMA resuelve el subyacente y delega en el primario, así que
    los dos comparten proveedor: un CEDEAR se pide a la misma fuente que un
    ticker US directo.
    """
    provider = provider.lower()
    if provider not in PROVIDERS:
        raise ValueError(f"proveedor desconocido: {provider!r} (opciones: {', '.join(PROVIDERS)})")

    if provider == "fmp":
        from .fmp import FmpAdapter, FmpClient

        client = FmpClient()
        primary = _FmpWithYfinanceFallback(FmpAdapter(client))
        return primary, BymaAdapter(underlying_adapter=primary)

    primary = YFinanceAdapter()
    return primary, BymaAdapter(underlying_adapter=primary)


def build_service(
    *,
    provider: str = DEFAULT_PROVIDER,
    cache_path: Optional[str] = None,
    ttl_hours: float = 24.0,
    use_cache: bool = True,
    target_currency: Optional[str] = None,
    fx: Optional[FxProvider] = None,
) -> tuple[FundamentalsService, SnapshotStore]:
    """Arma el servicio con las dependencias reales. Devuelve (servicio, cache).

    El cache se devuelve aparte porque hay que cerrarlo.
    """
    if not use_cache:
        store: SnapshotStore = NullCache()
    elif cache_path:
        store = SnapshotCache(cache_path, ttl_hours=ttl_hours)
    else:
        store = SnapshotCache(ttl_hours=ttl_hours)

    primary, byma = _build_adapters(provider)
    service = FundamentalsService(
        store, primary, byma, target_currency=target_currency, fx=fx
    )
    return service, store
