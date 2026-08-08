from __future__ import annotations

import json

import pytest

from bot.fetcher.byma_adapter import BymaAdapter, CedearMap, is_byma_ticker
from bot.fetcher.yfinance_adapter import YFinanceAdapter
from bot.models import UnmappedTickerError

from .conftest import (
    HEALTHY_BALANCE,
    HEALTHY_CASHFLOW,
    HEALTHY_FINANCIALS,
    HEALTHY_INFO,
    FakeTicker,
    factory_for,
    frame,
)


@pytest.fixture
def cedear_map() -> CedearMap:
    return CedearMap(
        mappings={
            "GGAL.BA": {"underlying": "GGAL", "exchange": "NASDAQ", "kind": "adr"},
            "AAPL.BA": {"underlying": "AAPL", "exchange": "NASDAQ", "kind": "cedear"},
        },
        no_underlying={"ALUA.BA": "Aluar no tiene ADR."},
    )


@pytest.fixture
def underlying_adapter() -> YFinanceAdapter:
    ggal = FakeTicker(
        info={**HEALTHY_INFO, "sector": "Financial Services", "longName": "Grupo Galicia"},
        financials=frame(HEALTHY_FINANCIALS),
        balance_sheet=frame(HEALTHY_BALANCE),
        cashflow=frame(HEALTHY_CASHFLOW),
    )
    return YFinanceAdapter(ticker_factory=factory_for({"GGAL": ggal}))


class TestDeteccion:
    @pytest.mark.parametrize("ticker", ["GGAL.BA", "ggal.ba", "YPFD.BA"])
    def test_reconoce_tickers_locales(self, ticker):
        assert is_byma_ticker(ticker)

    @pytest.mark.parametrize("ticker", ["AAPL", "BRK-B", "BA"])
    def test_ignora_tickers_extranjeros(self, ticker):
        # 'BA' es Boeing: termina en 'BA' pero no tiene el sufijo '.BA'.
        assert not is_byma_ticker(ticker)


class TestResolucion:
    def test_resuelve_adr(self, cedear_map):
        ref = cedear_map.resolve("GGAL.BA")
        assert ref.underlying == "GGAL"
        assert ref.kind == "adr"

    def test_es_case_insensitive(self, cedear_map):
        assert cedear_map.resolve("ggal.ba").underlying == "GGAL"

    def test_ticker_sin_subyacente_explica_por_que(self, cedear_map):
        with pytest.raises(UnmappedTickerError) as exc:
            cedear_map.resolve("ALUA.BA")
        assert "Aluar" in str(exc.value)

    def test_ticker_desconocido_sugiere_la_accion(self, cedear_map):
        with pytest.raises(UnmappedTickerError) as exc:
            cedear_map.resolve("PEPE.BA")
        assert "cedear_map.json" in str(exc.value)


class TestFetch:
    def test_pide_fundamentals_del_subyacente(self, cedear_map, underlying_adapter):
        snapshot = BymaAdapter(cedear_map, underlying_adapter).fetch("GGAL.BA")
        assert snapshot.ticker == "GGAL.BA"
        assert snapshot.source_ticker == "GGAL"
        assert snapshot.source == "byma"
        assert snapshot.is_cedear

    def test_los_ratios_son_los_del_subyacente(self, cedear_map, underlying_adapter):
        # El ratio de conversión del CEDEAR no interviene: los ratios son
        # adimensionales, así que el número del ADR es el correcto tal cual.
        snapshot = BymaAdapter(cedear_map, underlying_adapter).fetch("GGAL.BA")
        assert snapshot.roic == pytest.approx(24_000 / 90_000)
        assert snapshot.sector == "Financial Services"

    def test_deja_constancia_del_desvio(self, cedear_map, underlying_adapter):
        snapshot = BymaAdapter(cedear_map, underlying_adapter).fetch("GGAL.BA")
        assert any("GGAL" in w and "local" in w for w in snapshot.warnings)

    def test_ticker_sin_mapping_no_llega_a_la_red(self, cedear_map):
        # El adapter subyacente explota si lo llaman: prueba que ni se intenta.
        def exploding_factory(symbol: str):
            raise AssertionError("no debería haberse consultado al proveedor")

        adapter = BymaAdapter(cedear_map, YFinanceAdapter(ticker_factory=exploding_factory))
        with pytest.raises(UnmappedTickerError):
            adapter.fetch("PEPE.BA")


class TestMapDelRepo:
    """El JSON versionado tiene que estar bien formado y ser coherente."""

    def test_carga(self):
        assert len(CedearMap.load()) > 0

    def test_todas_las_claves_son_ba(self):
        data = json.loads(_map_path().read_text(encoding="utf-8"))
        for key in data["mappings"]:
            assert key.endswith(".BA"), key
        for key in data["no_underlying"]:
            assert key.endswith(".BA"), key

    def test_ningun_ticker_esta_en_los_dos_lados(self):
        data = json.loads(_map_path().read_text(encoding="utf-8"))
        assert not set(data["mappings"]) & set(data["no_underlying"])

    def test_ningun_subyacente_tiene_sufijo_local(self):
        data = json.loads(_map_path().read_text(encoding="utf-8"))
        for key, entry in data["mappings"].items():
            assert not entry["underlying"].endswith(".BA"), key

    def test_no_hay_subyacentes_duplicados(self):
        # Dos tickers locales apuntando al mismo ADR duplicarían la empresa en
        # el ranking sectorial.
        data = json.loads(_map_path().read_text(encoding="utf-8"))
        underlyings = [e["underlying"] for e in data["mappings"].values()]
        assert len(underlyings) == len(set(underlyings))


def _map_path():
    from bot.fetcher.byma_adapter import DEFAULT_MAP_PATH

    return DEFAULT_MAP_PATH
