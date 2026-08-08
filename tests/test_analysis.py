"""Tests de la Capa 1 extendida: series, salud financiera y valuación histórica."""

from __future__ import annotations

from datetime import date

import pytest

from bot.analysis.series import (
    AnnualPeriod,
    FinancialHistory,
    build_history,
    latest_with,
)
from bot.analysis.valuation import (
    REPORTING_LAG_DAYS,
    band,
    build_valuation,
    compute_multiples,
    multiples_history,
    period_public_at,
)
from bot.analysis.profile import build_profile

from .conftest import frame


def period(year: int, **kwargs) -> AnnualPeriod:
    base = dict(period_end=date(year, 12, 31), revenue=1000.0, net_income=100.0)
    base.update(kwargs)
    return AnnualPeriod(**base)


def history(*periods: AnnualPeriod) -> FinancialHistory:
    """Del más reciente al más viejo, como los devuelve yfinance."""
    return FinancialHistory(tuple(sorted(periods, key=lambda p: p.period_end, reverse=True)))


class TestRatiosDerivados:
    def test_margenes(self):
        p = period(2025, revenue=1000.0, gross_profit=400.0, operating_income=250.0, net_income=180.0)
        assert p.gross_margin == pytest.approx(0.40)
        assert p.operating_margin == pytest.approx(0.25)
        assert p.net_margin == pytest.approx(0.18)

    def test_margen_bruto_cero_no_es_un_margen(self):
        # Yahoo le pone 0 a los bancos: no tienen costo de ventas.
        assert period(2025, gross_profit=0.0).gross_margin is None

    def test_roic_usa_la_tasa_efectiva_cuando_esta(self):
        p = period(2025, ebit=300.0, pretax_income=280.0, tax_provision=56.0,
                   total_debt=400.0, total_equity=600.0)
        assert p.effective_tax_rate == pytest.approx(0.20)
        assert p.roic() == pytest.approx(300 * 0.8 / 1000)

    def test_roic_cae_al_default_si_la_tasa_es_absurda(self):
        p = period(2025, ebit=300.0, pretax_income=280.0, tax_provision=-900.0,
                   total_debt=400.0, total_equity=600.0)
        assert p.effective_tax_rate is None
        assert p.roic(0.21) == pytest.approx(300 * 0.79 / 1000)

    def test_deuda_neta_y_leverage(self):
        p = period(2025, total_debt=500.0, cash=200.0, ebitda=300.0)
        assert p.net_debt == pytest.approx(300.0)
        assert p.net_debt_to_ebitda == pytest.approx(1.0)

    def test_caja_neta_da_leverage_negativo(self):
        # Más caja que deuda es una posición sana, no un ratio inválido.
        p = period(2025, total_debt=100.0, cash=400.0, ebitda=300.0)
        assert p.net_debt == pytest.approx(-300.0)
        assert p.net_debt_to_ebitda == pytest.approx(-1.0)

    def test_cobertura_de_intereses_ignora_el_signo(self):
        # yfinance reporta el gasto financiero con signo inconsistente.
        assert period(2025, ebit=500.0, interest_expense=-50.0).interest_coverage == pytest.approx(10)
        assert period(2025, ebit=500.0, interest_expense=50.0).interest_coverage == pytest.approx(10)

    def test_dias_de_caja(self):
        p = period(2025, revenue=3650.0, operating_income=0.0, cash=100.0)
        assert p.cash_days == pytest.approx(10.0)

    def test_fcf_neto_de_sbc(self):
        # El SBC no sale de caja pero diluye igual: restarlo es la lectura conservadora.
        p = period(2025, free_cash_flow=1000.0, stock_based_comp=200.0)
        assert p.fcf_after_sbc == pytest.approx(800.0)
        assert p.sbc_to_revenue == pytest.approx(0.20)

    def test_ratios_faltantes_no_explotan(self):
        p = AnnualPeriod(period_end=date(2025, 12, 31))
        for name in ("gross_margin", "operating_margin", "net_margin", "net_debt",
                     "net_debt_to_ebitda", "interest_coverage", "current_ratio",
                     "cash_days", "roe", "fcf_after_sbc"):
            assert getattr(p, name) is None
        assert p.roic() is None


class TestSeries:
    @pytest.fixture
    def h(self):
        return history(
            period(2023, revenue=1000.0, net_income=100.0),
            period(2024, revenue=1200.0, net_income=150.0),
            period(2025, revenue=1500.0, net_income=200.0),
        )

    def test_la_serie_va_del_mas_viejo_al_mas_nuevo(self, h):
        # Una tendencia se lee hacia adelante en el tiempo.
        assert [v for _, v in h.series("revenue")] == [1000.0, 1200.0, 1500.0]

    def test_promedio_y_tendencia(self, h):
        assert h.average("net_margin") == pytest.approx((0.1 + 0.125 + 0.1333) / 3, abs=1e-3)
        assert h.trend("net_margin") == pytest.approx(0.0333, abs=1e-3)

    def test_cagr(self, h):
        assert h.cagr("revenue") == pytest.approx((1500 / 1000) ** 0.5 - 1)

    def test_cagr_none_si_la_base_no_es_positiva(self):
        h = history(period(2024, revenue=-100.0), period(2025, revenue=500.0))
        assert h.cagr("revenue") is None

    def test_crecimiento_yoy(self, h):
        growth = [g for _, g in h.yoy_growth("revenue")]
        assert growth == [pytest.approx(0.2), pytest.approx(0.25)]

    def test_una_sola_observacion_no_tiene_tendencia(self):
        assert history(period(2025)).trend("net_margin") is None

    def test_metrica_desconocida_falla_fuerte(self, h):
        with pytest.raises(KeyError):
            h.series("magic_ratio")

    def test_latest_with_busca_hacia_atras(self):
        # Apple dejó de reportar Interest Expense en 2024 pero lo tenía en 2023.
        h = history(
            period(2023, interest_expense=50.0, total_debt=1000.0),
            period(2024, interest_expense=None, total_debt=900.0),
            period(2025, interest_expense=None, total_debt=800.0),
        )
        found = latest_with(h, "interest_expense", "total_debt")
        assert found.period_end.year == 2023

    def test_latest_with_devuelve_none_si_nadie_lo_tiene(self):
        assert latest_with(history(period(2025)), "interest_expense") is None


class TestConstruccionDesdeDataFrames:
    def test_arma_la_serie(self):
        periods = [date(2025, 12, 31), date(2024, 12, 31)]
        h = build_history(
            frame({"Total Revenue": [1500.0, 1200.0], "Net Income": [200.0, 150.0]}, periods),
            frame({"Stockholders Equity": [800.0, 700.0]}, periods),
            frame({"Free Cash Flow": [180.0, 140.0]}, periods),
        )
        assert len(h) == 2
        assert h.latest.revenue == 1500.0
        assert h.latest.period_end == date(2025, 12, 31)

    def test_descarta_las_columnas_fantasma(self):
        # yfinance devuelve un ejercicio extra casi vacío (para AAPL, 6 filas de
        # 39). Contarlo infla el período declarado y ensucia todas las tablas.
        periods = [date(2025, 12, 31), date(2024, 12, 31), date(2023, 12, 31)]
        h = build_history(
            frame({"Total Revenue": [1500.0, 1200.0, None], "Net Income": [200.0, 150.0, None]}, periods),
            frame({"Stockholders Equity": [800.0, 700.0, 600.0]}, periods),
            frame({"Free Cash Flow": [180.0, 140.0, 120.0]}, periods),
        )
        assert len(h) == 2
        assert [p.period_end.year for p in h.periods] == [2025, 2024]

    def test_respeta_el_tope_de_anios(self):
        periods = [date(2025 - i, 12, 31) for i in range(5)]
        h = build_history(
            frame({"Total Revenue": [100.0] * 5}, periods),
            frame({"Stockholders Equity": [50.0] * 5}, periods),
            frame({"Free Cash Flow": [10.0] * 5}, periods),
            max_years=3,
        )
        assert len(h) == 3

    def test_estados_vacios(self):
        import pandas as pd

        assert len(build_history(pd.DataFrame(), pd.DataFrame(), pd.DataFrame())) == 0


class TestValuacion:
    @pytest.fixture
    def h(self):
        return history(
            period(2024, revenue=1000.0, net_income=100.0, ebitda=200.0,
                   total_debt=300.0, cash=100.0, total_equity=500.0,
                   free_cash_flow=80.0, shares_outstanding=100.0),
            period(2025, revenue=1200.0, net_income=120.0, ebitda=240.0,
                   total_debt=300.0, cash=200.0, total_equity=600.0,
                   free_cash_flow=100.0, shares_outstanding=100.0),
        )

    def test_multiplos_basicos(self, h):
        m = compute_multiples(h.latest, price=24.0, shares=100.0)
        assert m.market_cap == pytest.approx(2400.0)
        assert m.enterprise_value == pytest.approx(2400 + 100)  # deuda neta = 300-200
        assert m.pe == pytest.approx(20.0)
        assert m.ev_ebitda == pytest.approx(2500 / 240)
        assert m.fcf_yield == pytest.approx(100 / 2400)

    def test_resultado_negativo_no_produce_pe(self, h):
        # Un P/E negativo no es un múltiplo barato: no es un múltiplo.
        p = period(2025, net_income=-50.0, shares_outstanding=100.0)
        assert compute_multiples(p, price=10.0, shares=100.0).pe is None

    def test_sin_acciones_no_hay_multiplos(self, h):
        assert compute_multiples(h.latest, price=24.0, shares=None).market_cap is None

    def test_usa_el_ejercicio_ya_publicado(self, h):
        # Con rezago de 90 días, en marzo de 2025 el balance de dic-2024 recién
        # se publicó y el de dic-2025 no existe.
        assert period_public_at(h, date(2025, 4, 15)).period_end.year == 2024

    def test_no_usa_un_balance_que_todavia_no_salio(self, h):
        # 15 de enero de 2025: el cierre de dic-2024 no es público todavía.
        found = period_public_at(h, date(2025, 1, 15))
        assert found is None or found.period_end.year < 2024

    def test_el_rezago_es_configurable(self, h):
        assert period_public_at(h, date(2025, 1, 15), lag_days=0).period_end.year == 2024

    def test_la_serie_saltea_fechas_sin_balance_publicado(self, h):
        prices = [(date(2023, 6, 30), 20.0), (date(2025, 6, 30), 24.0)]
        serie = multiples_history(h, prices)
        # La primera fecha es anterior a cualquier ejercicio disponible.
        assert len(serie) == 1
        assert serie[0].as_of == date(2025, 6, 30)


class TestBandas:
    def _series(self, values):
        from bot.analysis.valuation import Multiples

        return [Multiples(pe=v) for v in values]

    def test_mediana_y_percentil(self):
        b = band("pe", self._series([10, 20, 30, 40, 50]), current=30.0)
        assert b.median == pytest.approx(30.0)
        assert b.percentile == pytest.approx(0.5)
        assert b.vs_median == pytest.approx(0.0)

    def test_veredicto_caro(self):
        b = band("pe", self._series(list(range(10, 30))), current=29.0)
        assert b.verdict == "caro"

    def test_veredicto_barato(self):
        b = band("pe", self._series(list(range(10, 30))), current=10.0)
        assert b.verdict == "barato"

    def test_el_fcf_yield_se_lee_al_reves(self):
        # Un yield alto es barato, no caro: la dirección se invierte.
        alto = band("fcf_yield", [__import__("bot.analysis.valuation", fromlist=["Multiples"]).Multiples(fcf_yield=v / 100) for v in range(1, 21)], current=0.20)
        assert alto.verdict == "barato"

    def test_sin_suficientes_observaciones_no_hay_veredicto(self):
        # Con pocos puntos el percentil es ruido; mejor no opinar.
        assert band("pe", self._series([10, 20, 30]), current=30.0).verdict is None

    def test_vs_mediana(self):
        b = band("pe", self._series([10, 20, 30, 40, 50]), current=45.0)
        assert b.vs_median == pytest.approx(0.5)

    def test_serie_vacia(self):
        b = band("pe", [], current=20.0)
        assert b.median is None
        assert b.verdict is None
        assert b.observations == 0


class TestPerfilCompleto:
    def test_sin_precios_no_hay_valuacion(self):
        h = history(period(2025, shares_outstanding=100.0))
        assert build_valuation(h, [], current_price=None, current_shares=100.0) is None

    def test_construye_las_bandas_de_todos_los_multiplos(self):
        h = history(
            period(2024, ebitda=200.0, total_debt=100.0, cash=50.0,
                   total_equity=500.0, free_cash_flow=80.0, shares_outstanding=100.0),
            period(2025, ebitda=240.0, total_debt=100.0, cash=80.0,
                   total_equity=600.0, free_cash_flow=100.0, shares_outstanding=100.0),
        )
        prices = [(date(2025, m, 1), 20.0 + m) for m in range(1, 13)]
        profile = build_valuation(h, prices, current_price=30.0, current_shares=100.0)
        assert profile is not None
        assert set(profile.bands) == {"pe", "ev_ebitda", "ev_revenue", "fcf_yield", "pb", "ps"}
        assert profile.lag_days == REPORTING_LAG_DAYS


class TestCompanyProfileProvider:
    """Simétrico al de FMP en test_fmp.py: el default de yfinance no debe
    acusar a FMP por lo que no trae, igual que FMP no debe acusar a yfinance.
    `CompanyProfile.provider` es el campo que hace esa distinción posible.
    """

    def test_build_profile_registra_yfinance_como_proveedor(self, healthy_factory):
        profile = build_profile("TEST", healthy_factory)
        assert profile.provider == "yfinance"

    def test_los_gaps_acusan_al_proveedor_correcto(self, healthy_factory):
        gaps = build_profile("TEST", healthy_factory).gaps()
        assert any("yfinance" in g.lower() and "segmentos" in g.lower() for g in gaps)
        assert not any("fmp" in g.lower() for g in gaps)
