"""Cache SQLite de snapshots, con TTL.

Motivo: yfinance es un scrape de Yahoo y tiene rate limiting agresivo. Los
fundamentals cambian cuando sale un balance, o sea cuatro veces por año: pegarle
al proveedor en cada corrida es tirar cuota a la basura. Default 24hs de TTL.

El TTL se evalúa contra `fetched_at` (cuándo lo trajimos), no contra `as_of` del
snapshot, para que un snapshot restaurado de un export viejo no se dé por fresco.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

from ..models import SCHEMA_VERSION, FundamentalSnapshot

logger = logging.getLogger(__name__)

DEFAULT_TTL_HOURS = 24.0
DEFAULT_CACHE_PATH = Path.home() / ".cache" / "fundamental-bot" / "snapshots.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    ticker         TEXT PRIMARY KEY,
    payload        TEXT NOT NULL,
    fetched_at     REAL NOT NULL,
    schema_version INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_fetched_at ON snapshots (fetched_at);
"""

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SnapshotCache:
    """Almacén clave-valor de snapshots con expiración por tiempo.

    Se usa como context manager o llamando `close()`. `path=":memory:"` sirve
    para tests.
    """

    def __init__(
        self,
        path: Path | str = DEFAULT_CACHE_PATH,
        ttl_hours: float = DEFAULT_TTL_HOURS,
        clock: Clock = _utcnow,
    ):
        self.path = str(path)
        self.ttl_hours = ttl_hours
        self._clock = clock
        self._lock = threading.Lock()

        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # --- API ---------------------------------------------------------------

    def get(self, ticker: str) -> Optional[FundamentalSnapshot]:
        """Snapshot vigente para `ticker`, o None si no está o venció."""
        key = ticker.upper()
        with self._lock:
            row = self._conn.execute(
                "SELECT payload, fetched_at, schema_version FROM snapshots WHERE ticker = ?",
                (key,),
            ).fetchone()

        if row is None:
            return None
        payload, fetched_at, schema_version = row

        if schema_version != SCHEMA_VERSION:
            logger.debug("%s: cache de schema %s (actual %s), se descarta", key, schema_version, SCHEMA_VERSION)
            self.delete(key)
            return None

        age_hours = (self._clock().timestamp() - fetched_at) / 3600.0
        if age_hours >= self.ttl_hours:
            logger.debug("%s: cache vencido (%.1fh > %.1fh)", key, age_hours, self.ttl_hours)
            return None

        try:
            return FundamentalSnapshot.from_json(payload)
        except (ValueError, TypeError) as exc:
            # Fila corrupta: es cache, no una fuente de verdad. Se borra y se
            # refetchea en vez de romper la corrida.
            logger.warning("%s: entrada de cache ilegible (%s), se descarta", key, exc)
            self.delete(key)
            return None

    def put(self, snapshot: FundamentalSnapshot) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO snapshots (ticker, payload, fetched_at, schema_version)"
                " VALUES (?, ?, ?, ?)",
                (
                    snapshot.ticker.upper(),
                    snapshot.to_json(),
                    self._clock().timestamp(),
                    SCHEMA_VERSION,
                ),
            )
            self._conn.commit()

    def delete(self, ticker: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM snapshots WHERE ticker = ?", (ticker.upper(),))
            self._conn.commit()

    def purge_expired(self) -> int:
        """Borra entradas vencidas. Devuelve cuántas eliminó."""
        cutoff = self._clock().timestamp() - self.ttl_hours * 3600.0
        with self._lock:
            cursor = self._conn.execute("DELETE FROM snapshots WHERE fetched_at < ?", (cutoff,))
            self._conn.commit()
            return cursor.rowcount

    def tickers(self) -> Iterable[str]:
        with self._lock:
            return [r[0] for r in self._conn.execute("SELECT ticker FROM snapshots").fetchall()]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "SnapshotCache":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class NullCache:
    """Cache que no cachea. Para `--no-cache` y para tests del fetcher."""

    ttl_hours = 0.0

    def get(self, ticker: str) -> Optional[FundamentalSnapshot]:
        return None

    def put(self, snapshot: FundamentalSnapshot) -> None:
        return None

    def delete(self, ticker: str) -> None:
        return None

    def purge_expired(self) -> int:
        return 0

    def tickers(self) -> Iterable[str]:
        return []

    def close(self) -> None:
        return None

    def __enter__(self) -> "NullCache":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None
