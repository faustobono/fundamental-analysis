"""Tests de la capa web: parseo del universo y serialización. Sin red."""

from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from bot.models import FundamentalSnapshot
from bot.scorer.metrics import Method
from bot.scorer.sector_scorer import SectorScorer
from bot.web import precomputed
from bot.web.api import _bar, _score_payload, _snapshot_payload, parse_tickers
from bot.web.server import MAX_TICKERS, STATIC_FILES, STATIC_DIR, ScreenerHandler, provider_for
from api.index import public_path


@contextmanager
def running_server():
    """Levanta el handler real en un puerto libre. Ninguna ruta usada acá sale
    a la red: `/api/top` sólo lee un archivo del disco."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), ScreenerHandler)
    httpd.provider = "yfinance"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


def get(port: int, path: str):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


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


class TestUniversoPrecalculado:
    def test_no_hay_tickers_repetidos(self):
        universo = precomputed.TOP_UNIVERSE
        assert len(set(universo)) == len(universo)

    def test_estan_normalizados(self):
        # El servicio compara en mayúscula; un ticker en minúscula sería un
        # miss de cache silencioso contra el mismo ticker pedido a mano.
        assert all(t == t.strip().upper() for t in precomputed.TOP_UNIVERSE)

    def test_entra_en_el_tope_del_servidor(self):
        assert len(precomputed.TOP_UNIVERSE) <= MAX_TICKERS

    def test_stamp_declara_que_es_precalculado_y_cuando(self):
        payload = precomputed.stamp({"meta": {"ok": 3}}, universe_size=100)
        assert payload["meta"]["precomputed"] is True
        assert payload["meta"]["universe_size"] == 100
        # Tiene que ser parseable por `new Date()` en el front.
        assert datetime.fromisoformat(payload["meta"]["generated_at"]).tzinfo is not None

    def test_save_y_load_son_simetricos(self, tmp_path):
        path = tmp_path / "sub" / "top.json"  # el subdirectorio no existe todavía
        original = {"meta": {"ok": 2}, "sectors": [], "failures": []}
        precomputed.save(original, path)
        assert precomputed.load(path) == original

    def test_load_devuelve_none_si_no_se_genero(self, tmp_path):
        # No es un error: es "corré `python -m bot precompute`".
        assert precomputed.load(tmp_path / "no-existe.json") is None

    def test_load_devuelve_none_si_esta_corrupto(self, tmp_path):
        path = tmp_path / "roto.json"
        path.write_text("{no soy json", encoding="utf-8")
        assert precomputed.load(path) is None


class TestEndpointTop:
    """`/api/top` end-to-end contra el handler real. Sin red: sólo lee disco."""

    def test_sirve_el_ranking_precalculado(self, tmp_path, monkeypatch):
        payload = {"meta": {"ok": 100, "precomputed": True}, "sectors": [], "failures": []}
        path = tmp_path / "top.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setattr(precomputed, "DEFAULT_PATH", path)

        with running_server() as port:
            status, headers, body = get(port, "/api/top")

        assert status == 200
        assert json.loads(body)["meta"]["ok"] == 100
        # Se puede reusar del navegador: sólo cambia cuando alguien regenera.
        assert "max-age" in headers["Cache-Control"]

    def test_sin_archivo_explica_como_generarlo(self, tmp_path, monkeypatch):
        monkeypatch.setattr(precomputed, "DEFAULT_PATH", tmp_path / "no-existe.json")

        with running_server() as port:
            status, headers, body = get(port, "/api/top")

        assert status == 404
        assert "precompute" in json.loads(body)["error"]
        # Un error nunca se cachea: si se genera en un minuto, hay que verlo.
        assert headers["Cache-Control"] == "no-store"

    def test_los_estaticos_siguen_sin_cachearse(self):
        # Deliberado: sin esto hay que hard-refreshear cada cambio de CSS.
        with running_server() as port:
            status, headers, _ = get(port, "/styles.css")
        assert status == 200
        assert headers["Cache-Control"] == "no-store"
