"""Cache SQLite de snapshots, con TTL.

Motivo: yfinance es un scrape de Yahoo y tiene rate limiting agresivo. Los
fundamentals cambian cuando sale un balance, o sea cuatro veces por año: pegarle
al proveedor en cada corrida es tirar cuota a la basura. Default 24hs de TTL.

El TTL se evalúa contra `fetched_at` (cuándo lo trajimos), no contra `as_of` del
snapshot, para que un snapshot restaurado de un export viejo no se dé por fresco.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from ..models import SCHEMA_VERSION, FundamentalSnapshot

logger = logging.getLogger(__name__)

DEFAULT_TTL_HOURS = 24.0


def default_cache_path() -> Path:
    """Devuelve una ubicación escribible para el cache según el runtime."""
    if os.environ.get("VERCEL"):
        # El filesystem de Vercel fuera de /tmp es de solo lectura. Este cache
        # es un acelerador, no una fuente de verdad, así que puede ser efímero.
        return Path("/tmp") / "fundamental-bot" / "snapshots.db"
    return Path.home() / ".cache" / "fundamental-bot" / "snapshots.db"


DEFAULT_CACHE_PATH = default_cache_path()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    ticker         TEXT PRIMARY KEY,
    payload        TEXT NOT NULL,
    fetched_at     REAL NOT NULL,
    schema_version INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_fetched_at ON snapshots (fetched_at);
"""

#: Tabla aparte de `snapshots`: ahí cada fila es un `FundamentalSnapshot` con
#: su schema; acá es un payload JSON ya armado, con una clave compuesta.
_PAYLOAD_SCHEMA = """
CREATE TABLE IF NOT EXISTS payloads (
    key            TEXT PRIMARY KEY,
    payload        TEXT NOT NULL,
    fetched_at     REAL NOT NULL,
    schema_version INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payloads_fetched_at ON payloads (fetched_at);
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


def brief_key(ticker: str, years: int, provider: str) -> str:
    """Clave de un brief. Los tres parámetros cambian el resultado.

    Se usa el ticker *pedido*, no el resuelto: `GGAL.BA` y `GGAL` producen
    informes distintos (uno avisa que es un CEDEAR), así que comparten datos
    pero no entrada de cache.
    """
    return f"brief:{ticker.strip().upper()}:{years}:{provider}"


class PayloadCache:
    """Cache de payloads JSON ya armados, con la misma expiración por tiempo.

    Existe para el `brief`, que hasta ahora no cacheaba nada: cada request
    retraía 5 ejercicios de balances y ~60 precios, aunque fuera el mismo
    ticker de hace un minuto. Se cachea el payload final y no el
    `CompanyProfile` porque el payload ya es JSON puro — serializar el objeto
    entero (historia, valuación, scores) sería trabajo y superficie de bugs
    para llegar al mismo lugar.
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
        self._conn.executescript(_PAYLOAD_SCHEMA)
        self._conn.commit()

    def get(self, key: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload, fetched_at, schema_version FROM payloads WHERE key = ?",
                (key,),
            ).fetchone()

        if row is None:
            return None
        payload, fetched_at, schema_version = row

        if schema_version != SCHEMA_VERSION:
            logger.debug("%s: payload de schema %s (actual %s), se descarta", key, schema_version, SCHEMA_VERSION)
            self.delete(key)
            return None

        age_hours = (self._clock().timestamp() - fetched_at) / 3600.0
        if age_hours >= self.ttl_hours:
            logger.debug("%s: payload vencido (%.1fh > %.1fh)", key, age_hours, self.ttl_hours)
            return None

        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            # Igual que en SnapshotCache: es cache, no fuente de verdad.
            logger.warning("%s: payload de cache ilegible (%s), se descarta", key, exc)
            self.delete(key)
            return None

    def put(self, key: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO payloads (key, payload, fetched_at, schema_version)"
                " VALUES (?, ?, ?, ?)",
                (
                    key,
                    json.dumps(payload, ensure_ascii=False, default=str),
                    self._clock().timestamp(),
                    SCHEMA_VERSION,
                ),
            )
            self._conn.commit()

    def delete(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM payloads WHERE key = ?", (key,))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "PayloadCache":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class NullPayloadCache:
    """`PayloadCache` que no cachea. Para `cache=0` y para tests."""

    ttl_hours = 0.0

    def get(self, key: str) -> Optional[dict[str, Any]]:
        return None

    def put(self, key: str, payload: dict[str, Any]) -> None:
        return None

    def delete(self, key: str) -> None:
        return None

    def close(self) -> None:
        return None

    def __enter__(self) -> "NullPayloadCache":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


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
