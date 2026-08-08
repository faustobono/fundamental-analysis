from __future__ import annotations

import pandas as pd
import pytest

from bot.fetcher.yfinance_adapter import DEFAULT_TAX_RATE, YFinanceAdapter
from bot.models import NoDataError, UpstreamError

from .conftest import (
    HEALTHY_BALANCE,
    HEALTHY_CASHFLOW,
    HEALTHY_FINANCIALS,
    HEALTHY_INFO,
    FakeTicker,
    factory_for,
    frame,
)


def adapter_for(ticker: FakeTicker, symbol: str = "TEST") -> YFinanceAdapter:
    return YFinanceAdapter(ticker_factory=factory_for({symbol: ticker}))


def build(info=None, financials=None, balance=None, cashflow=None) -> FakeTicker:
    """Ticker sano, con los overrides que pida el test."""
    return FakeTicker(
        info={**HEALTHY_INFO, **(info or {})},
        financials=frame({**HEALTHY_FINANCIALS, **(financials or {})}),
        balance_sheet=frame({**HEALTHY_BALANCE, **(balance or {})}),
        cashflow=frame({**HEALTHY_CASHFLOW, **(cashflow or {})}),
    )


class TestCasoFeliz:
    @pytest.fixture
    def snapshot(self, healthy_factory):
        return YFinanceAdapter(ticker_factory=healthy_factory).fetch("TEST")

    def test_identidad(self, snapshot):
        assert snapshot.ticker == "TEST"
        assert snapshot.source_ticker == "TEST"
        assert snapshot.source == "yfinance"
        assert snapshot.sector == "Technology"
        assert snapshot.currency == "USD"
        assert snapshot.as_of.tzinfo is not None

    def test_ratios_de_info(self, snapshot):
        assert snapshot.pe == pytest.approx(22.0)
        assert snapshot.pb == pytest.approx(8.3)

    def test_margenes_calculados_del_income_statement(self, snapshot):
        assert snapshot.gross_margin == pytest.approx(0.45)
        assert snapshot.net_margin == pytest.approx(0.224)

    def test_roe(self, snapshot):
        assert snapshot.roe == pytest.approx(22_400 / 60_000)

    def test_roic_usa_tasa_efectiva_derivada(self, snapshot):
        # tax = 5600/28000 = 20%; NOPAT = 30000*0.8 = 24000
        # capital invertido = 30000 deuda + 60000 equity = 90000
        assert snapshot.effective_tax_rate == pytest.approx(0.20)
        assert snapshot.roic == pytest.approx(24_000 / 90_000)

    def test_debt_to_equity_actual_y_previo(self, snapshot):
        assert snapshot.debt_to_equity == pytest.approx(0.5)
        assert snapshot.debt_to_equity_prev == pytest.approx(0.6)
        assert snapshot.debt_to_equity_trend == pytest.approx(-0.1)

    def test_fcf_yield(self, snapshot):
        assert snapshot.fcf_yield == pytest.approx(25_000 / 500_000)

    def test_revenue_growth(self, snapshot):
        assert snapshot.revenue_growth_yoy == pytest.approx(10_000 / 90_000)

    def test_snapshot_sano_no_deja_warnings(self, snapshot):
        assert snapshot.warnings == ()


class TestDividendYield:
    """`.info` mezcla unidades entre campos, así que el orden de preferencia importa."""

    def test_se_reconstruye_del_dividendo_por_accion(self):
        # Unidad inequívoca: 1.08 de dividendo sobre precio 50 = 2.16%.
        snapshot = adapter_for(build(info={"dividendRate": 1.08, "currentPrice": 50.0})).fetch("TEST")
        assert snapshot.dividend_yield == pytest.approx(0.0216)

    def test_ignora_dividend_yield_cuando_puede_calcularlo(self):
        # dividendYield=0.32 significa 0.32%, no 32%: no se puede usar tal cual.
        snapshot = adapter_for(
            build(info={"dividendRate": 1.08, "currentPrice": 50.0, "dividendYield": 0.32})
        ).fetch("TEST")
        assert snapshot.dividend_yield == pytest.approx(0.0216)

    def test_fallback_a_trailing_yield_que_viene_en_fraccion(self):
        info = {k: v for k, v in HEALTHY_INFO.items() if k != "dividendYield"}
        info["trailingAnnualDividendYield"] = 0.0031
        ticker = FakeTicker(info=info, financials=frame(HEALTHY_FINANCIALS))
        assert adapter_for(ticker).fetch("TEST").dividend_yield == pytest.approx(0.0031)

    def test_ultimo_recurso_dividend_yield_se_lee_como_porcentaje(self):
        info = {k: v for k, v in HEALTHY_INFO.items() if k != "currentPrice"}
        info["dividendYield"] = 2.38
        ticker = FakeTicker(info=info, financials=frame(HEALTHY_FINANCIALS))
        assert adapter_for(ticker).fetch("TEST").dividend_yield == pytest.approx(0.0238)

    def test_empresa_sin_dividendos(self):
        info = {k: v for k, v in HEALTHY_INFO.items() if k != "dividendYield"}
        ticker = FakeTicker(info=info, financials=frame(HEALTHY_FINANCIALS))
        assert adapter_for(ticker).fetch("TEST").dividend_yield is None


class TestMonedasMixtas:
    """Un ADR argentino reporta el balance en ARS y cotiza en USD."""

    @pytest.fixture
    def snapshot(self):
        return adapter_for(build(info={"currency": "USD", "financialCurrency": "ARS"})).fetch("TEST")

    def test_registra_ambas_monedas(self, snapshot):
        assert snapshot.currency == "ARS"
        assert snapshot.quote_currency == "USD"
        assert snapshot.has_currency_mismatch

    def test_no_calcula_fcf_yield(self, snapshot):
        # FCF en ARS sobre market cap en USD daría un yield inflado ~1000x.
        assert snapshot.fcf_yield is None
        assert any("mezclan mercado con balance" in w for w in snapshot.warnings)

    def test_los_ratios_de_un_solo_estado_contable_siguen_valiendo(self, snapshot):
        # Numerador y denominador vienen del mismo balance: la moneda se cancela.
        assert snapshot.roic == pytest.approx(24_000 / 90_000)
        assert snapshot.net_margin == pytest.approx(0.224)
        assert snapshot.debt_to_equity == pytest.approx(0.5)

    def test_no_inventa_pb_cruzando_monedas(self):
        info = {k: v for k, v in HEALTHY_INFO.items() if k != "priceToBook"}
        info.update({"currency": "USD", "financialCurrency": "ARS"})
        ticker = FakeTicker(
            info=info,
            financials=frame(HEALTHY_FINANCIALS),
            balance_sheet=frame(HEALTHY_BALANCE),
            cashflow=frame(HEALTHY_CASHFLOW),
        )
        assert adapter_for(ticker).fetch("TEST").pb is None

    def test_misma_moneda_no_deja_warning(self, healthy_factory):
        snapshot = YFinanceAdapter(ticker_factory=healthy_factory).fetch("TEST")
        assert not snapshot.has_currency_mismatch
        assert snapshot.fcf_yield is not None


class TestPrecedencia:
    def test_el_balance_le_gana_a_info(self):
        # .info dice 200% de D/E; el balance dice 50%. Gana el balance.
        snapshot = adapter_for(build(info={"debtToEquity": 200.0})).fetch("TEST")
        assert snapshot.debt_to_equity == pytest.approx(0.5)

    def test_info_como_fallback_convierte_porcentaje(self):
        ticker = FakeTicker(
            info={**HEALTHY_INFO, "debtToEquity": 165.0},
            financials=frame(HEALTHY_FINANCIALS),
            balance_sheet=pd.DataFrame(),  # sin balance no hay cálculo propio
            cashflow=frame(HEALTHY_CASHFLOW),
        )
        snapshot = adapter_for(ticker).fetch("TEST")
        assert snapshot.debt_to_equity == pytest.approx(1.65)
        assert any("info" in w for w in snapshot.warnings)

    def test_pe_derivado_de_eps_si_no_viene_trailing_pe(self):
        info = {k: v for k, v in HEALTHY_INFO.items() if k != "trailingPE"}
        info["trailingEps"] = 2.5
        ticker = FakeTicker(
            info=info,
            financials=frame(HEALTHY_FINANCIALS),
            balance_sheet=frame(HEALTHY_BALANCE),
            cashflow=frame(HEALTHY_CASHFLOW),
        )
        # currentPrice=50, eps=2.5 -> 20x
        assert adapter_for(ticker).fetch("TEST").pe == pytest.approx(20.0)


class TestDatosIncompletos:
    def test_sin_gross_profit_lo_reconstruye_de_cost_of_revenue(self):
        financials = {k: v for k, v in HEALTHY_FINANCIALS.items() if k != "Gross Profit"}
        financials["Cost Of Revenue"] = [55_000.0, 50_000.0]
        ticker = FakeTicker(
            info=dict(HEALTHY_INFO),
            financials=frame(financials),
            balance_sheet=frame(HEALTHY_BALANCE),
            cashflow=frame(HEALTHY_CASHFLOW),
        )
        assert adapter_for(ticker).fetch("TEST").gross_margin == pytest.approx(0.45)

    def test_sin_free_cash_flow_lo_calcula_de_cfo_menos_capex(self):
        cashflow = {k: v for k, v in HEALTHY_CASHFLOW.items() if k != "Free Cash Flow"}
        ticker = FakeTicker(
            info=dict(HEALTHY_INFO),
            financials=frame(HEALTHY_FINANCIALS),
            balance_sheet=frame(HEALTHY_BALANCE),
            cashflow=frame(cashflow),
        )
        # 30000 CFO - 5000 capex = 25000
        assert adapter_for(ticker).fetch("TEST").free_cash_flow == pytest.approx(25_000)

    def test_sin_total_debt_lo_suma_de_las_partes(self):
        balance = {k: v for k, v in HEALTHY_BALANCE.items() if k != "Total Debt"}
        balance["Long Term Debt"] = [25_000.0, 28_000.0]
        balance["Current Debt"] = [5_000.0, 5_000.0]
        ticker = FakeTicker(
            info=dict(HEALTHY_INFO),
            financials=frame(HEALTHY_FINANCIALS),
            balance_sheet=frame(balance),
            cashflow=frame(HEALTHY_CASHFLOW),
        )
        assert adapter_for(ticker).fetch("TEST").total_debt == pytest.approx(30_000)

    def test_sin_ejercicio_previo_no_hay_growth_ni_trend(self):
        ticker = FakeTicker(
            info={k: v for k, v in HEALTHY_INFO.items()},
            financials=frame({k: v[:1] for k, v in HEALTHY_FINANCIALS.items()}),
            balance_sheet=frame({k: v[:1] for k, v in HEALTHY_BALANCE.items()}),
            cashflow=frame({k: v[:1] for k, v in HEALTHY_CASHFLOW.items()}),
        )
        snapshot = adapter_for(ticker).fetch("TEST")
        assert snapshot.revenue_growth_yoy is None
        assert snapshot.debt_to_equity_prev is None
        assert snapshot.debt_to_equity_trend is None
        assert snapshot.roic is not None  # el resto sigue calculándose

    def test_solo_info_sin_estados_contables(self):
        # Caso típico de un ticker exótico: Yahoo tiene la ficha pero no los
        # balances. Debe rendir lo que se pueda, no fallar.
        ticker = FakeTicker(info=dict(HEALTHY_INFO))
        snapshot = adapter_for(ticker).fetch("TEST")
        assert snapshot.pe == pytest.approx(22.0)
        assert snapshot.roic is None
        assert snapshot.gross_margin is None

    def test_nan_en_una_fila_no_contamina(self):
        ticker = build(financials={"Gross Profit": [float("nan"), 40_000.0]})
        assert adapter_for(ticker).fetch("TEST").gross_margin is None


class TestReglasEconomicas:
    def test_pe_negativo_se_descarta(self):
        # Una empresa que pierde plata no tiene P/E "barato" de -5x.
        snapshot = adapter_for(build(info={"trailingPE": -5.0})).fetch("TEST")
        assert snapshot.pe is None
        assert any("P/E negativo" in w for w in snapshot.warnings)

    def test_tax_rate_default_si_no_es_derivable(self):
        financials = {k: v for k, v in HEALTHY_FINANCIALS.items() if k != "Tax Provision"}
        ticker = FakeTicker(
            info=dict(HEALTHY_INFO),
            financials=frame(financials),
            balance_sheet=frame(HEALTHY_BALANCE),
            cashflow=frame(HEALTHY_CASHFLOW),
        )
        snapshot = adapter_for(ticker).fetch("TEST")
        assert snapshot.effective_tax_rate == pytest.approx(DEFAULT_TAX_RATE)
        assert any("tax rate" in w for w in snapshot.warnings)

    def test_tax_rate_absurdo_cae_al_default(self):
        # Crédito fiscal: tax negativo sobre pretax positivo -> tasa negativa.
        snapshot = adapter_for(build(financials={"Tax Provision": [-9_000.0, 4_800.0]})).fetch("TEST")
        assert snapshot.effective_tax_rate == pytest.approx(DEFAULT_TAX_RATE)
        assert any("fuera de rango" in w for w in snapshot.warnings)

    def test_gross_profit_en_cero_no_es_margen_cero(self):
        # Yahoo le pone Gross Profit = 0 a los bancos, que no tienen costo de
        # ventas. Un 0% de margen bruto los haría ver como el peor del sector
        # cuando en realidad el ratio no aplica al negocio.
        snapshot = adapter_for(
            build(financials={"Gross Profit": [0.0, 0.0]}, info={"grossMargins": 0.0})
        ).fetch("TEST")
        assert snapshot.gross_margin is None
        assert any("no aplica" in w for w in snapshot.warnings)

    def test_revenue_previo_negativo_no_genera_growth(self):
        snapshot = adapter_for(build(financials={"Total Revenue": [100_000.0, -5_000.0]})).fetch("TEST")
        assert snapshot.revenue_growth_yoy is None

    def test_patrimonio_negativo_no_genera_roic(self):
        # Equity negativo hace que el capital invertido no tenga sentido.
        snapshot = adapter_for(build(balance={"Stockholders Equity": [-100_000.0, 55_000.0]})).fetch("TEST")
        assert snapshot.roic is None
        assert any("capital invertido" in w for w in snapshot.warnings)


class TestErrores:
    def test_proveedor_caido_es_upstream_error(self):
        ticker = FakeTicker(raises=ConnectionError("boom"))
        with pytest.raises(UpstreamError) as exc:
            adapter_for(ticker).fetch("TEST")
        assert "TEST" in str(exc.value)

    def test_ticker_vacio_es_no_data(self):
        with pytest.raises(NoDataError):
            adapter_for(FakeTicker()).fetch("TEST")

    def test_info_sin_ninguna_metrica_util_es_no_data(self):
        ticker = FakeTicker(info={"sector": "Technology", "longName": "Fantasma SA"})
        with pytest.raises(NoDataError):
            adapter_for(ticker).fetch("TEST")

    def test_el_error_lleva_la_etiqueta_pedida_no_el_simbolo_resuelto(self):
        adapter = YFinanceAdapter(ticker_factory=factory_for({"GGAL": FakeTicker()}))
        with pytest.raises(NoDataError) as exc:
            adapter.fetch("GGAL", requested_as="GGAL.BA")
        assert "GGAL.BA" in str(exc.value)


class TestDeterminismo:
    def test_dos_fetches_dan_el_mismo_resultado(self, healthy_factory):
        adapter = YFinanceAdapter(ticker_factory=healthy_factory)
        a = adapter.fetch("TEST")
        b = adapter.fetch("TEST")
        assert a.replace(as_of=b.as_of) == b
