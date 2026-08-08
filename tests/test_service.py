"""Tests del orquestador: ruteo, cache y tolerancia a fallas en batch.

Ningún test toca yfinance: los adapters se reemplazan por dobles.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot.fetcher.cache import NullCache
from bot.fetcher.service import FundamentalsService, _FmpWithYfinanceFallback
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


class FakeSource:
    """Como `FakeAdapter`, pero acepta `requested_as` — hace falta para poder
    usarse como primario/fallback de `_FmpWithYfinanceFallback`, que reenvía
    ese kwarg tal cual llega (lo necesita `BymaAdapter` para resolver CEDEARs)."""

    def __init__(self, responses: dict[str, object]):
        self._responses = responses
        self.calls: list[tuple[str, object]] = []

    def fetch(self, ticker: str, *, requested_as=None) -> FundamentalSnapshot:
        self.calls.append((ticker, requested_as))
        response = self._responses.get(ticker)
        if response is None:
            raise NoDataError(ticker, "sin datos en el doble")
        if isinstance(response, Exception):
            raise response
        return response


class TestFmpFallback:
    """`_FmpWithYfinanceFallback`: FMP con un 402 (plan pago requerido) no
    debería tirar abajo el ticker entero si yfinance puede traerlo igual —
    hallazgo real contra producción con CEDEARs (GGAL, YPF, BMA)."""

    def test_402_cae_a_yfinance(self):
        primary = FakeSource({"GGAL": UpstreamError("GGAL", "FMP: plan pago", status_code=402)})
        fallback = FakeSource({"GGAL": snapshot("GGAL")})
        result = _FmpWithYfinanceFallback(primary, fallback).fetch("GGAL")
        assert result.ticker == "GGAL"
        assert any("plan pago" in w for w in result.warnings)

    def test_error_sin_402_no_cae_al_fallback(self):
        # 429 (rate limit) o cualquier otro código no es "hace falta plan
        # pago" — no tiene sentido reintentar con otra fuente, y esconder el
        # error real detrás de un fallback silencioso sería peor.
        primary = FakeSource({"AAPL": UpstreamError("AAPL", "límite alcanzado", status_code=429)})
        fallback = FakeSource({"AAPL": snapshot("AAPL")})
        with pytest.raises(UpstreamError, match="límite alcanzado"):
            _FmpWithYfinanceFallback(primary, fallback).fetch("AAPL")
        assert fallback.calls == []

    def test_si_el_fallback_tambien_falla_el_error_explica_las_dos_fuentes(self):
        primary = FakeSource({"GGAL": UpstreamError("GGAL", "FMP: plan pago", status_code=402)})
        fallback = FakeSource({"GGAL": UpstreamError("GGAL", "bloqueado (datacenter IP)")})
        with pytest.raises(UpstreamError, match="plan pago.*yfinance también falló"):
            _FmpWithYfinanceFallback(primary, fallback).fetch("GGAL")

    def test_no_cae_al_fallback_si_el_primario_funciona(self):
        primary = FakeSource({"AAPL": snapshot("AAPL")})
        fallback = FakeSource({})
        result = _FmpWithYfinanceFallback(primary, fallback).fetch("AAPL")
        assert result.ticker == "AAPL"
        assert fallback.calls == []

    def test_reenvia_requested_as_a_las_dos_fuentes(self):
        # Así es como lo llama `BymaAdapter`: ticker = subyacente, requested_as
        # = ticker local del CEDEAR.
        primary = FakeSource({"GGAL": UpstreamError("GGAL", "plan pago", status_code=402)})
        fallback = FakeSource({"GGAL": snapshot("GGAL")})
        _FmpWithYfinanceFallback(primary, fallback).fetch("GGAL", requested_as="GGAL.BA")
        assert primary.calls == [("GGAL", "GGAL.BA")]
        assert fallback.calls == [("GGAL", "GGAL.BA")]


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
