"""Tests del orquestador: ruteo, cache y tolerancia a fallas en batch.

Ningún test toca yfinance: los adapters se reemplazan por dobles.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot.fetcher.cache import NullCache
from bot.fetcher.service import FundamentalsService
from bot.models import FundamentalSnapshot, NoDataError, UnmappedTickerError, UpstreamError


def snapshot(ticker="TEST", sector="Technology", **kwargs) -> FundamentalSnapshot:
    base = dict(
        ticker=ticker,
        source_ticker=ticker,
        source="yfinance",
        as_of=datetime(2026, 7, 30, tzinfo=timezone.utc),
        sector=sector,
        pe=20.0,
    )
    base.update(kwargs)
    return FundamentalSnapshot(**base)


class FakeAdapter:
    """Adapter programable: devuelve snapshots o lanza, y cuenta llamadas."""

    def __init__(self, responses: dict[str, object], source="yfinance"):
        self._responses = responses
        self.source = source
        self.calls: list[str] = []

    def fetch(self, ticker: str) -> FundamentalSnapshot:
        self.calls.append(ticker)
        response = self._responses.get(ticker)
        if response is None:
            raise NoDataError(ticker, "sin datos en el doble")
        if isinstance(response, Exception):
            raise response
        return response


class DictCache:
    """Cache en memoria, sin TTL: acá se testea el ruteo, no la expiración."""

    def __init__(self):
        self.data: dict[str, FundamentalSnapshot] = {}
        self.writes = 0

    def get(self, ticker):
        return self.data.get(ticker.upper())

    def put(self, snapshot):
        self.writes += 1
        self.data[snapshot.ticker.upper()] = snapshot


class TestRuteo:
    def test_ticker_extranjero_va_a_yfinance(self):
        yf = FakeAdapter({"AAPL": snapshot("AAPL")})
        byma = FakeAdapter({})
        service = FundamentalsService(NullCache(), yf, byma)

        assert service.get("AAPL").ticker == "AAPL"
        assert yf.calls == ["AAPL"]
        assert byma.calls == []

    def test_ticker_local_va_a_byma(self):
        yf = FakeAdapter({})
        byma = FakeAdapter({"GGAL.BA": snapshot("GGAL.BA", source_ticker="GGAL")}, source="byma")
        service = FundamentalsService(NullCache(), yf, byma)

        assert service.get("GGAL.BA").source_ticker == "GGAL"
        assert byma.calls == ["GGAL.BA"]
        assert yf.calls == []

    def test_normaliza_el_ticker_antes_de_rutear(self):
        byma = FakeAdapter({"GGAL.BA": snapshot("GGAL.BA")}, source="byma")
        service = FundamentalsService(NullCache(), FakeAdapter({}), byma)
        service.get("  ggal.ba  ")
        assert byma.calls == ["GGAL.BA"]

    def test_ticker_vacio_es_error_de_programacion(self):
        service = FundamentalsService(NullCache(), FakeAdapter({}), FakeAdapter({}))
        with pytest.raises(ValueError):
            service.get("   ")


class TestNormalizacion:
    def test_el_servicio_normaliza_lo_que_devuelve_el_adapter(self):
        crudo = snapshot("AAPL", sector="Information Technology", pe=99_999.0)
        service = FundamentalsService(NullCache(), FakeAdapter({"AAPL": crudo}), FakeAdapter({}))
        resultado = service.get("AAPL")
        assert resultado.sector == "Technology"
        assert resultado.pe is None

    def test_lo_que_se_cachea_ya_esta_normalizado(self):
        cache = DictCache()
        crudo = snapshot("AAPL", sector="Information Technology")
        service = FundamentalsService(cache, FakeAdapter({"AAPL": crudo}), FakeAdapter({}))
        service.get("AAPL")
        assert cache.data["AAPL"].sector == "Technology"


class TestCache:
    def test_hit_evita_la_llamada_al_proveedor(self):
        cache = DictCache()
        cache.data["AAPL"] = snapshot("AAPL")
        yf = FakeAdapter({"AAPL": snapshot("AAPL")})
        FundamentalsService(cache, yf, FakeAdapter({})).get("AAPL")
        assert yf.calls == []

    def test_miss_consulta_y_guarda(self):
        cache = DictCache()
        yf = FakeAdapter({"AAPL": snapshot("AAPL")})
        FundamentalsService(cache, yf, FakeAdapter({})).get("AAPL")
        assert yf.calls == ["AAPL"]
        assert cache.writes == 1

    def test_use_cache_false_fuerza_el_refetch(self):
        cache = DictCache()
        cache.data["AAPL"] = snapshot("AAPL", pe=10.0)
        yf = FakeAdapter({"AAPL": snapshot("AAPL", pe=30.0)})
        resultado = FundamentalsService(cache, yf, FakeAdapter({})).get("AAPL", use_cache=False)
        assert resultado.pe == 30.0
        assert yf.calls == ["AAPL"]

    def test_un_fallo_no_deja_basura_en_el_cache(self):
        cache = DictCache()
        yf = FakeAdapter({"AAPL": UpstreamError("AAPL", "timeout")})
        service = FundamentalsService(cache, yf, FakeAdapter({}))
        service.get_many(["AAPL"])
        assert cache.data == {}


class TestBatchResiliente:
    def test_un_ticker_roto_no_corta_el_batch(self):
        yf = FakeAdapter(
            {
                "AAPL": snapshot("AAPL"),
                "ROTO": UpstreamError("ROTO", "500 del proveedor"),
                "MSFT": snapshot("MSFT"),
            }
        )
        result = FundamentalsService(NullCache(), yf, FakeAdapter({})).get_many(["AAPL", "ROTO", "MSFT"])

        assert [s.ticker for s in result.snapshots] == ["AAPL", "MSFT"]
        assert len(result.failures) == 1
        assert result.failures[0][0] == "ROTO"
        assert "500 del proveedor" in result.failures[0][1]

    def test_cedear_sin_mapping_se_reporta_como_falla_no_como_crash(self):
        byma = FakeAdapter({"PEPE.BA": UnmappedTickerError("PEPE.BA", "no está en el mapping")}, source="byma")
        result = FundamentalsService(NullCache(), FakeAdapter({"AAPL": snapshot("AAPL")}), byma).get_many(
            ["AAPL", "PEPE.BA"]
        )
        assert result.ok == 1
        assert result.failures[0][0] == "PEPE.BA"

    def test_un_bug_inesperado_tampoco_tira_el_batch(self):
        # Un TypeError nuestro no debería costar 200 fetches ya pagados.
        yf = FakeAdapter({"BUG": TypeError("None + int"), "AAPL": snapshot("AAPL")})
        result = FundamentalsService(NullCache(), yf, FakeAdapter({})).get_many(["BUG", "AAPL"])
        assert result.ok == 1
        assert "inesperado" in result.failures[0][1]

    def test_deduplica_manteniendo_el_orden(self):
        yf = FakeAdapter({"AAPL": snapshot("AAPL"), "MSFT": snapshot("MSFT")})
        result = FundamentalsService(NullCache(), yf, FakeAdapter({})).get_many(
            ["AAPL", "aapl", "MSFT", "AAPL"]
        )
        assert [s.ticker for s in result.snapshots] == ["AAPL", "MSFT"]
        assert yf.calls == ["AAPL", "MSFT"]

    def test_ignora_lineas_vacias(self):
        # Archivos de tickers con líneas en blanco al final son la norma.
        yf = FakeAdapter({"AAPL": snapshot("AAPL")})
        result = FundamentalsService(NullCache(), yf, FakeAdapter({})).get_many(["AAPL", "", "   "])
        assert result.ok == 1
        assert result.failures == []

    def test_cuenta_los_hits_de_cache(self):
        cache = DictCache()
        cache.data["AAPL"] = snapshot("AAPL")
        yf = FakeAdapter({"MSFT": snapshot("MSFT")})
        result = FundamentalsService(cache, yf, FakeAdapter({})).get_many(["AAPL", "MSFT"])
        assert result.cache_hits == 1
        assert result.ok == 2

    def test_batch_entero_fallido_devuelve_resultado_vacio(self):
        yf = FakeAdapter({"A": NoDataError("A", "nada"), "B": NoDataError("B", "nada")})
        result = FundamentalsService(NullCache(), yf, FakeAdapter({})).get_many(["A", "B"])
        assert result.snapshots == []
        assert len(result.failures) == 2


class TestAgrupamiento:
    def test_agrupa_por_sector(self):
        yf = FakeAdapter(
            {
                "AAPL": snapshot("AAPL", sector="Technology"),
                "MSFT": snapshot("MSFT", sector="Technology"),
                "JPM": snapshot("JPM", sector="Financial Services"),
            }
        )
        result = FundamentalsService(NullCache(), yf, FakeAdapter({})).get_many(["AAPL", "MSFT", "JPM"])
        grupos = result.by_sector()
        assert sorted(grupos) == ["Financial Services", "Technology"]
        assert len(grupos["Technology"]) == 2

    def test_los_sin_sector_caen_en_unknown(self):
        yf = FakeAdapter({"RARO": snapshot("RARO", sector=None)})
        result = FundamentalsService(NullCache(), yf, FakeAdapter({})).get_many(["RARO"])
        assert "Unknown" in result.by_sector()
