"""Tests de `bot/web/brief_api.py`: que las tablas por ejercicio expongan
exactamente los atributos de `AnnualPeriod` que dicen exponer. Es la clase de
bug que un typo en el nombre de la métrica produce en silencio (KeyError sólo
si el atributo no existe en absoluto; un nombre de atributo distinto pero
válido devolvería `None` sin avisar), así que se prueba con datos concretos.
"""

from __future__ import annotations

from datetime import date

import pytest

import bot.web.brief_api as brief_api
from bot.analysis.series import AnnualPeriod, FinancialHistory
from bot.models import NoDataError
from bot.web.brief_api import GROWTH_METRICS, PROFITABILITY_METRICS, _series_table, run_brief


def _history() -> FinancialHistory:
    return FinancialHistory((
        AnnualPeriod(
            period_end=date(2025, 12, 31),
            revenue=1000.0,
            net_income=150.0,
            total_assets=750.0,
            gross_profit=400.0,
            free_cash_flow=120.0,
            dividends_paid=-30.0,
            shares_outstanding=95.0,
        ),
    ))


class TestProfitabilityTable:
    def test_roa_y_rotacion_de_activos_llegan_a_la_tabla(self):
        table = _series_table(_history(), PROFITABILITY_METRICS)
        rows = {r["name"]: r["values"][0] for r in table["rows"]}
        assert rows["roa"] == 150.0 / 750.0
        assert rows["asset_turnover"] == 1000.0 / 750.0


class TestGrowthTable:
    def test_fcf_margin_acciones_y_payout_llegan_a_la_tabla(self):
        table = _series_table(_history(), GROWTH_METRICS)
        rows = {r["name"]: r["values"][0] for r in table["rows"]}
        assert rows["fcf_margin"] == 120.0 / 1000.0
        assert rows["shares_outstanding"] == 95.0
        assert rows["payout_ratio"] == 30.0 / 150.0


class TestBriefCache:
    """El brief no cacheaba nada: cada request retraía 5 ejercicios de balances
    y el histórico de precios, aunque fuera el mismo ticker de hace un minuto.
    Y la web lo pide sola al abrir, así que era cuota tirada en cada visita."""

    @pytest.fixture
    def contador(self, monkeypatch):
        """Cuenta cuántas veces se sale de verdad a construir el informe."""
        calls = []

        def fake_build(ticker, *, years, provider):
            calls.append((ticker, years, provider))
            return {"identity": {"ticker": ticker}, "warnings": []}

        monkeypatch.setattr(brief_api, "_build_brief", fake_build)
        return calls

    def test_el_segundo_pedido_sale_del_cache(self, contador, tmp_path):
        db = str(tmp_path / "cache.db")
        primero = run_brief("AAPL", cache_path=db)
        segundo = run_brief("AAPL", cache_path=db)

        assert len(contador) == 1  # sólo el primero fue a buscar datos
        assert primero["cached"] is False
        assert segundo["cached"] is True
        assert segundo["identity"] == primero["identity"]

    def test_cambiar_los_anios_es_otra_entrada(self, contador, tmp_path):
        db = str(tmp_path / "cache.db")
        run_brief("AAPL", years=5, cache_path=db)
        run_brief("AAPL", years=3, cache_path=db)
        assert len(contador) == 2

    def test_cambiar_el_proveedor_es_otra_entrada(self, contador, tmp_path):
        db = str(tmp_path / "cache.db")
        run_brief("AAPL", provider="yfinance", cache_path=db)
        run_brief("AAPL", provider="fmp", cache_path=db)
        assert len(contador) == 2

    def test_el_cedear_y_el_subyacente_no_comparten_entrada(self, contador, tmp_path):
        # GGAL.BA y GGAL comparten los datos pero no el informe: uno avisa que
        # es un CEDEAR y el otro no.
        db = str(tmp_path / "cache.db")
        run_brief("GGAL.BA", cache_path=db)
        run_brief("GGAL", cache_path=db)
        assert len(contador) == 2

    def test_use_cache_false_siempre_refetchea(self, contador, tmp_path):
        db = str(tmp_path / "cache.db")
        run_brief("AAPL", cache_path=db, use_cache=False)
        run_brief("AAPL", cache_path=db, use_cache=False)
        assert len(contador) == 2

    def test_un_error_no_queda_cacheado(self, monkeypatch, tmp_path):
        # Si el proveedor falla, el siguiente intento tiene que volver a probar
        # en vez de servir el error desde el cache.
        calls = []

        def falla(ticker, *, years, provider):
            calls.append(ticker)
            raise NoDataError(ticker, "el proveedor no lo tiene")

        monkeypatch.setattr(brief_api, "_build_brief", falla)
        db = str(tmp_path / "cache.db")
        for _ in range(2):
            with pytest.raises(NoDataError):
                run_brief("NADA", cache_path=db)
        assert len(calls) == 2
