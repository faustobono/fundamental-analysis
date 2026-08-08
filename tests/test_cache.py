from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bot.fetcher.cache import NullCache, SnapshotCache
from bot.models import SCHEMA_VERSION, FundamentalSnapshot


class FakeClock:
    """Reloj controlable: el TTL se testea moviendo el tiempo, no durmiendo."""

    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc))


@pytest.fixture
def cache(tmp_path, clock):
    with SnapshotCache(tmp_path / "test.db", ttl_hours=24.0, clock=clock) as c:
        yield c


def snapshot(ticker="TEST", **kwargs) -> FundamentalSnapshot:
    base = dict(
        ticker=ticker,
        source_ticker=ticker,
        source="yfinance",
        as_of=datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
        pe=20.0,
        sector="Technology",
    )
    base.update(kwargs)
    return FundamentalSnapshot(**base)


class TestBasico:
    def test_miss_devuelve_none(self, cache):
        assert cache.get("NADA") is None

    def test_roundtrip(self, cache):
        original = snapshot()
        cache.put(original)
        assert cache.get("TEST") == original

    def test_es_case_insensitive(self, cache):
        cache.put(snapshot("test"))
        assert cache.get("TEST") is not None
        assert cache.get("test") is not None

    def test_put_pisa_la_entrada_previa(self, cache):
        cache.put(snapshot(pe=20.0))
        cache.put(snapshot(pe=25.0))
        assert cache.get("TEST").pe == 25.0

    def test_delete(self, cache):
        cache.put(snapshot())
        cache.delete("TEST")
        assert cache.get("TEST") is None

    def test_persiste_entre_conexiones(self, tmp_path, clock):
        path = tmp_path / "persist.db"
        with SnapshotCache(path, clock=clock) as first:
            first.put(snapshot())
        with SnapshotCache(path, clock=clock) as second:
            assert second.get("TEST") is not None


class TestTTL:
    def test_dentro_del_ttl_es_hit(self, cache, clock):
        cache.put(snapshot())
        clock.advance(hours=23, minutes=59)
        assert cache.get("TEST") is not None

    def test_pasado_el_ttl_es_miss(self, cache, clock):
        cache.put(snapshot())
        clock.advance(hours=24, minutes=1)
        assert cache.get("TEST") is None

    def test_el_borde_exacto_es_miss(self, cache, clock):
        cache.put(snapshot())
        clock.advance(hours=24)
        assert cache.get("TEST") is None

    def test_ttl_configurable(self, tmp_path, clock):
        with SnapshotCache(tmp_path / "corto.db", ttl_hours=1.0, clock=clock) as cache:
            cache.put(snapshot())
            clock.advance(hours=2)
            assert cache.get("TEST") is None

    def test_el_ttl_corre_desde_el_fetch_no_desde_as_of(self, cache, clock):
        # as_of viejo (snapshot importado de un export) pero traído recién:
        # se considera fresco porque lo que vence es nuestra copia.
        viejo = snapshot(as_of=datetime(2020, 1, 1, tzinfo=timezone.utc))
        cache.put(viejo)
        assert cache.get("TEST") is not None

    def test_purge_expired_borra_solo_lo_vencido(self, cache, clock):
        cache.put(snapshot("VIEJO"))
        clock.advance(hours=25)
        cache.put(snapshot("NUEVO"))
        assert cache.purge_expired() == 1
        assert sorted(cache.tickers()) == ["NUEVO"]


class TestRobustez:
    def test_entrada_corrupta_se_descarta_sin_romper(self, cache):
        cache.put(snapshot())
        cache._conn.execute("UPDATE snapshots SET payload = ? WHERE ticker = ?", ("{no es json", "TEST"))
        cache._conn.commit()
        assert cache.get("TEST") is None
        assert list(cache.tickers()) == []

    def test_schema_viejo_se_invalida(self, cache):
        cache.put(snapshot())
        cache._conn.execute(
            "UPDATE snapshots SET schema_version = ? WHERE ticker = ?",
            (SCHEMA_VERSION - 1, "TEST"),
        )
        cache._conn.commit()
        # Si cambia el modelo, el cache viejo no debe reinterpretarse mal.
        assert cache.get("TEST") is None

    def test_directorio_inexistente_se_crea(self, tmp_path, clock):
        path = tmp_path / "a" / "b" / "c.db"
        with SnapshotCache(path, clock=clock) as cache:
            cache.put(snapshot())
            assert cache.get("TEST") is not None
        assert path.exists()


class TestNullCache:
    def test_nunca_devuelve_nada(self):
        cache = NullCache()
        cache.put(snapshot())
        assert cache.get("TEST") is None
        assert list(cache.tickers()) == []
