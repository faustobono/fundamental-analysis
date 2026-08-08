"""Tests de `bot/web/brief_api.py`: que las tablas por ejercicio expongan
exactamente los atributos de `AnnualPeriod` que dicen exponer. Es la clase de
bug que un typo en el nombre de la métrica produce en silencio (KeyError sólo
si el atributo no existe en absoluto; un nombre de atributo distinto pero
válido devolvería `None` sin avisar), así que se prueba con datos concretos.
"""

from __future__ import annotations

from datetime import date

from bot.analysis.series import AnnualPeriod, FinancialHistory
from bot.web.brief_api import GROWTH_METRICS, PROFITABILITY_METRICS, _series_table


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
