from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bot.fetcher.cache import (
    NullCache,
    NullPayloadCache,
    PayloadCache,
    SnapshotCache,
    brief_key,
    default_cache_path,
)
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
    def test_el_cache_de_vercel_usa_tmp(self, monkeypatch):
        monkeypatch.setenv("VERCEL", "1")
        assert default_cache_path().as_posix() == "/tmp/fundamental-bot/snapshots.db"

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


class TestPayloadCache:
    """Cache del brief: un informe son 5 ejercicios de balances más el
    histórico de precios, y hasta ahora se retraía entero en cada request."""

    @pytest.fixture
    def cache(self, tmp_path, clock):
        with PayloadCache(tmp_path / "payloads.db", ttl_hours=24.0, clock=clock) as c:
            yield c

    def test_guarda_y_devuelve(self, cache):
        cache.put("brief:AAPL:5:fmp", {"identity": {"ticker": "AAPL"}})
        assert cache.get("brief:AAPL:5:fmp")["identity"]["ticker"] == "AAPL"

    def test_una_clave_que_no_esta(self, cache):
        assert cache.get("brief:NADA:5:fmp") is None

    def test_vence_con_el_ttl(self, cache, clock):
        cache.put("k", {"v": 1})
        clock.advance(hours=23)
        assert cache.get("k") is not None
        clock.advance(hours=2)
        assert cache.get("k") is None

    def test_convive_con_los_snapshots_en_el_mismo_archivo(self, tmp_path, clock):
        # Son dos tablas del mismo SQLite: abrir una no puede pisar la otra.
        path = tmp_path / "compartido.db"
        with SnapshotCache(path, clock=clock) as snapshots, PayloadCache(path, clock=clock) as payloads:
            snapshots.put(snapshot("AAPL"))
            payloads.put("brief:AAPL:5:fmp", {"v": 1})
            assert snapshots.get("AAPL") is not None
            assert payloads.get("brief:AAPL:5:fmp") == {"v": 1}

    def test_una_fila_corrupta_se_descarta_en_vez_de_romper(self, cache):
        cache._conn.execute(
            "INSERT INTO payloads (key, payload, fetched_at, schema_version) VALUES (?, ?, ?, ?)",
            ("roto", "{no soy json", cache._clock().timestamp(), SCHEMA_VERSION),
        )
        cache._conn.commit()
        assert cache.get("roto") is None

    def test_ignora_el_cache_de_un_schema_viejo(self, cache):
        cache._conn.execute(
            "INSERT INTO payloads (key, payload, fetched_at, schema_version) VALUES (?, ?, ?, ?)",
            ("viejo", '{"v": 1}', cache._clock().timestamp(), SCHEMA_VERSION - 1),
        )
        cache._conn.commit()
        assert cache.get("viejo") is None


class TestBriefKey:
    def test_los_tres_parametros_cambian_la_clave(self):
        base = brief_key("AAPL", 5, "fmp")
        assert base != brief_key("MSFT", 5, "fmp")
        assert base != brief_key("AAPL", 3, "fmp")
        assert base != brief_key("AAPL", 5, "yfinance")

    def test_normaliza_el_ticker(self):
        assert brief_key(" aapl ", 5, "fmp") == brief_key("AAPL", 5, "fmp")


class TestNullPayloadCache:
    def test_nunca_devuelve_nada(self):
        cache = NullPayloadCache()
        cache.put("k", {"v": 1})
        assert cache.get("k") is None
