"""Tests de la capa web: parseo del universo y serialización. Sin red."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bot.models import FundamentalSnapshot
from bot.scorer.metrics import Method
from bot.scorer.sector_scorer import SectorScorer
from bot.web.api import _bar, _score_payload, _snapshot_payload, parse_tickers
from bot.web.server import MAX_TICKERS, STATIC_FILES, STATIC_DIR, provider_for
from api.index import public_path


def snap(ticker, sector="Technology", **metrics) -> FundamentalSnapshot:
    base = dict(
        ticker=ticker,
        source_ticker=ticker,
        source="yfinance",
        as_of=datetime(2026, 7, 30, tzinfo=timezone.utc),
        sector=sector,
        company_name=f"{ticker} Inc.",
        currency="USD",
        quote_currency="USD",
    )
    base.update(metrics)
    return FundamentalSnapshot(**base)


@pytest.fixture
def ranking():
    universe = [
        snap("AAA", roic=0.30, fcf_yield=0.09, revenue_growth_yoy=0.25,
             debt_to_equity=0.3, debt_to_equity_prev=0.5, pe=22.0, market_cap=1e12),
        snap("BBB", roic=0.18, fcf_yield=0.05, revenue_growth_yoy=0.10,
             debt_to_equity=0.6, debt_to_equity_prev=0.6, pe=30.0),
        snap("CCC", roic=0.06, fcf_yield=0.02, revenue_growth_yoy=-0.05,
             debt_to_equity=1.4, debt_to_equity_prev=0.9, pe=45.0),
    ]
    return SectorScorer().score(universe).sectors["Technology"]


class TestParseTickers:
    @pytest.mark.parametrize(
        "raw",
        ["AAPL,MSFT", "AAPL MSFT", "AAPL\nMSFT", "AAPL, MSFT", "AAPL;MSFT", "  AAPL\n\n MSFT "],
    )
    def test_acepta_cualquier_separador(self, raw):
        # El usuario pega desde donde sea; no vamos a pedirle un formato exacto.
        assert parse_tickers(raw) == ["AAPL", "MSFT"]

    def test_normaliza_a_mayuscula(self):
        assert parse_tickers("aapl, ggal.ba") == ["AAPL", "GGAL.BA"]

    def test_deduplica_manteniendo_el_orden(self):
        assert parse_tickers("MSFT, AAPL, msft") == ["MSFT", "AAPL"]

    def test_vacio(self):
        assert parse_tickers("") == []
        assert parse_tickers(None) == []
        assert parse_tickers("   \n , ; ") == []


class TestBarras:
    def test_el_percentil_ya_esta_en_rango(self):
        assert _bar(0.0, Method.PERCENTILE) == 0.0
        assert _bar(0.5, Method.PERCENTILE) == 0.5
        assert _bar(1.0, Method.PERCENTILE) == 1.0

    def test_el_zscore_se_centra_en_la_mitad(self):
        assert _bar(0.0, Method.ZSCORE) == pytest.approx(0.5)
        assert _bar(2.5, Method.ZSCORE) == pytest.approx(1.0)
        assert _bar(-2.5, Method.ZSCORE) == pytest.approx(0.0)

    def test_un_zscore_extremo_no_desborda_la_barra(self):
        # Sin el clamp, un z de 8 pintaría una barra de 800% de ancho.
        assert _bar(8.0, Method.ZSCORE) == 1.0
        assert _bar(-8.0, Method.ZSCORE) == 0.0


class TestPayload:
    def test_la_tarjeta_trae_todo_lo_que_pinta_el_front(self, ranking):
        payload = _score_payload(ranking.ranked[0], Method.PERCENTILE)
        assert payload["rank"] == 1
        assert payload["ticker"] == "AAA"
        assert payload["company_name"] == "AAA Inc."
        assert 0.0 <= payload["composite"] <= 1.0

    def test_cada_metrica_trae_etiqueta_formato_y_barra(self, ranking):
        # El front no tiene reglas de negocio: todo viene resuelto del server.
        for metric in _score_payload(ranking.ranked[0], Method.PERCENTILE)["metrics"]:
            assert metric["label"]
            assert metric["format"] in ("pct", "x", "delta_x", "money", "num")
            assert 0.0 <= metric["bar"] <= 1.0
            assert isinstance(metric["higher_is_better"], bool)

    def test_los_faltantes_llegan_con_etiqueta_legible(self):
        universe = [
            snap("SIN", roic=0.2, revenue_growth_yoy=0.1),
            snap("CON", roic=0.1, fcf_yield=0.05, revenue_growth_yoy=0.05),
        ]
        r = SectorScorer(min_peers=2).score(universe).sectors["Technology"]
        sin = next(s for s in r.ranked if s.ticker == "SIN")
        missing = _score_payload(sin, Method.PERCENTILE)["missing"]
        assert {"name": "fcf_yield", "label": "FCF yield"} in missing

    def test_los_porcentajes_se_marcan_como_pct(self, ranking):
        ratios = {r["name"]: r for r in _snapshot_payload(ranking.ranked[0].snapshot)["ratios"]}
        assert ratios["roic"]["format"] == "pct"
        assert ratios["pe"]["format"] == "x"
        assert ratios["debt_to_equity_trend"]["format"] == "delta_x"

    def test_expone_el_desfasaje_de_monedas(self):
        s = snap("GGAL.BA", currency="ARS", quote_currency="USD")
        payload = _snapshot_payload(s.replace(source_ticker="GGAL"))
        assert payload["currency_mismatch"]
        assert payload["is_cedear"]
        assert payload["source_ticker"] == "GGAL"

    def test_propaga_los_warnings(self, ranking):
        s = ranking.ranked[0].snapshot.with_warning("ojo con esto")
        assert "ojo con esto" in _snapshot_payload(s)["warnings"]

    def test_el_payload_es_json_serializable(self, ranking):
        # Si no serializa, el endpoint devuelve 500 en vez de datos.
        json.dumps(_score_payload(ranking.ranked[0], Method.PERCENTILE), default=str)

    def test_los_none_sobreviven_como_null(self, ranking):
        payload = _snapshot_payload(ranking.ranked[1].snapshot)
        market_cap = payload["scale"]["market_cap"]
        assert market_cap is None
        assert "null" in json.dumps({"v": market_cap})


class TestServidor:
    def test_restaura_la_ruta_publica_reescrita_por_vercel(self):
        assert public_path("/api/index?__path=/api/health") == "/api/health"

    def test_restaura_la_ruta_y_preserva_sus_parametros(self):
        assert public_path("/api/index?__path=/api/screen&tickers=AAPL%2CMSFT") == (
            "/api/screen?tickers=AAPL%2CMSFT"
        )

    def test_usa_el_provider_inyectado_por_el_servidor_local(self):
        assert provider_for(SimpleNamespace(provider="fmp")) == "fmp"

    def test_usa_el_provider_del_entorno_si_el_runtime_no_lo_inyecta(self, monkeypatch):
        monkeypatch.setenv("BOT_PROVIDER", "fmp")
        assert provider_for(SimpleNamespace()) == "fmp"

    def test_los_estaticos_existen(self):
        for filename in set(STATIC_FILES.values()):
            assert (STATIC_DIR / filename).is_file(), filename

    def test_la_allowlist_no_permite_traversal(self):
        # Se sirve un dict fijo, no una ruta del request: no hay '..' posible.
        assert all(".." not in name and "/" not in name for name in STATIC_FILES.values())

    def test_hay_tope_de_tickers(self):
        # Un pegado accidental no puede disparar mil fetches a yfinance.
        assert 0 < MAX_TICKERS <= 500
