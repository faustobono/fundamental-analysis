"""Tests del proveedor FMP contra JSON enlatado. Ningún test toca la red.

Se prueban dos niveles:
  - el `FmpClient` real con un `http_get` inyectado (arma la URL, mete la key,
    parsea, maneja errores);
  - el `FmpAdapter` y `build_profile_fmp` con un cliente falso (la lógica de
    ratios y el armado de series).
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from bot.fetcher.fmp.adapter import FmpAdapter
from bot.fetcher.fmp.client import FmpClient
from bot.fetcher.fmp.deep import build_profile_fmp
from bot.brief.render import render_data_block
from bot.models import NoDataError, UpstreamError

# --- una empresa coherente a través de todos los endpoints ------------------

PROFILE = {
    "symbol": "TEST",
    "companyName": "Testco Inc.",
    "sector": "Technology",
    "industry": "Software",
    "currency": "USD",
    "price": 50.0,
    "marketCap": 500_000.0,
    "beta": 1.1,
    "lastDividend": 0.25,
}

INCOME = [
    {
        "date": "2025-09-30", "reportedCurrency": "USD",
        "revenue": 100_000, "costOfRevenue": 55_000, "grossProfit": 45_000,
        "operatingIncome": 30_000, "ebit": 30_000, "ebitda": 35_000,
        "netIncome": 22_400, "epsDiluted": 5.0, "interestExpense": 500,
        "incomeBeforeTax": 28_000, "incomeTaxExpense": 5_600,
        "weightedAverageShsOutDil": 4_480,
    },
    {
        "date": "2024-09-30", "reportedCurrency": "USD",
        "revenue": 90_000, "costOfRevenue": 50_000, "grossProfit": 40_000,
        "operatingIncome": 26_000, "ebit": 26_000, "ebitda": 30_000,
        "netIncome": 19_200, "epsDiluted": 4.3, "interestExpense": 480,
        "incomeBeforeTax": 24_000, "incomeTaxExpense": 4_800,
        "weightedAverageShsOutDil": 4_460,
    },
]

BALANCE = [
    {
        "date": "2025-09-30", "cashAndShortTermInvestments": 20_000,
        "totalDebt": 30_000, "totalStockholdersEquity": 60_000,
        "totalCurrentAssets": 50_000, "totalCurrentLiabilities": 40_000,
        "totalAssets": 150_000, "retainedEarnings": 70_000,
    },
    {
        "date": "2024-09-30", "cashAndShortTermInvestments": 18_000,
        "totalDebt": 33_000, "totalStockholdersEquity": 55_000,
        "totalCurrentAssets": 48_000, "totalCurrentLiabilities": 41_000,
        "totalAssets": 140_000, "retainedEarnings": 60_000,
    },
]

CASHFLOW = [
    {
        "date": "2025-09-30", "operatingCashFlow": 30_000,
        "capitalExpenditure": -5_000, "freeCashFlow": 25_000,
        "stockBasedCompensation": 2_000, "commonStockRepurchased": -8_000,
        "dividendsPaid": -4_000,
    },
    {
        "date": "2024-09-30", "operatingCashFlow": 27_000,
        "capitalExpenditure": -4_000, "freeCashFlow": 23_000,
        "stockBasedCompensation": 1_800, "commonStockRepurchased": -7_000,
        "dividendsPaid": -3_500,
    },
]


def _prices() -> list[dict]:
    """Serie diaria falsa: dos cierres por mes durante 24 meses."""
    rows = []
    for year in (2024, 2025):
        for month in range(1, 13):
            rows.append({"symbol": "TEST", "date": f"{year}-{month:02d}-05", "close": 40.0})
            rows.append({"symbol": "TEST", "date": f"{year}-{month:02d}-28", "close": 45.0})
    return rows


class FakeFmpClient:
    """Cliente que sirve fixtures por nombre de endpoint. Cuenta llamadas."""

    def __init__(self, overrides: dict | None = None):
        self._data = {
            "profile": [PROFILE],
            "income-statement": INCOME,
            "balance-sheet-statement": BALANCE,
            "cash-flow-statement": CASHFLOW,
            "historical-price-eod/full": _prices(),
            "analyst-estimates": [],
        }
        if overrides:
            self._data.update(overrides)
        self.calls: list[str] = []

    def get(self, endpoint, *, ticker=None, **params):
        self.calls.append(endpoint)
        if endpoint not in self._data:
            raise NoDataError(ticker or endpoint, f"endpoint sin fixture: {endpoint}")
        return self._data[endpoint]

    def get_one(self, endpoint, *, ticker=None, **params):
        data = self.get(endpoint, ticker=ticker, **params)
        if isinstance(data, list):
            return data[0] if data else None
        return data

    def get_list(self, endpoint, *, ticker=None, **params):
        data = self.get(endpoint, ticker=ticker, **params)
        return data if isinstance(data, list) else [data]


# --- FmpClient (transporte) -------------------------------------------------


class TestClient:
    def test_arma_la_url_con_key_y_parametros(self):
        seen = {}

        def http_get(url):
            seen["url"] = url
            return json.dumps([PROFILE])

        client = FmpClient(api_key="SECRET", http_get=http_get)
        client.get_one("profile", symbol="AAPL")
        assert "financialmodelingprep.com/stable/profile" in seen["url"]
        assert "symbol=AAPL" in seen["url"]
        assert "apikey=SECRET" in seen["url"]

    def test_sin_key_es_upstream_error(self):
        client = FmpClient(api_key=None, http_get=lambda url: "[]")
        with pytest.raises(UpstreamError, match="API key"):
            client.get("profile", ticker="AAPL")

    def test_el_error_de_negocio_viene_en_el_cuerpo(self):
        # FMP manda 200 con {"Error Message": ...} cuando la key es mala.
        body = json.dumps({"Error Message": "Invalid API KEY."})
        client = FmpClient(api_key="X", http_get=lambda url: body)
        with pytest.raises(UpstreamError, match="Invalid API KEY"):
            client.get("profile", ticker="AAPL")

    def test_detecta_el_limite_diario(self):
        body = json.dumps({"Error Message": "Limit Reach . Upgrade your plan."})
        client = FmpClient(api_key="X", http_get=lambda url: body)
        with pytest.raises(UpstreamError, match="límite"):
            client.get("profile", ticker="AAPL")

    def test_http_429_es_limite(self):
        import urllib.error

        def http_get(url):
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)

        client = FmpClient(api_key="X", http_get=http_get)
        with pytest.raises(UpstreamError, match="límite"):
            client.get("profile", ticker="AAPL")

    def test_json_corrupto_es_upstream_error(self):
        client = FmpClient(api_key="X", http_get=lambda url: "no soy json{")
        with pytest.raises(UpstreamError, match="no es JSON"):
            client.get("profile", ticker="AAPL")

    def test_get_one_devuelve_none_con_lista_vacia(self):
        client = FmpClient(api_key="X", http_get=lambda url: "[]")
        assert client.get_one("profile", ticker="NADA") is None


# --- FmpAdapter (screener) --------------------------------------------------


class TestAdapter:
    @pytest.fixture
    def snapshot(self):
        return FmpAdapter(FakeFmpClient()).fetch("TEST")

    def test_identidad(self, snapshot):
        assert snapshot.ticker == "TEST"
        assert snapshot.source == "fmp"
        assert snapshot.sector == "Technology"
        assert snapshot.currency == "USD"
        assert snapshot.company_name == "Testco Inc."

    def test_ratios_calculados_de_las_lineas(self, snapshot):
        assert snapshot.pe == pytest.approx(50 / 5)
        assert snapshot.pb == pytest.approx(500_000 / 60_000)
        assert snapshot.roe == pytest.approx(22_400 / 60_000)
        assert snapshot.roic == pytest.approx(30_000 * 0.8 / 90_000)
        assert snapshot.effective_tax_rate == pytest.approx(0.20)
        assert snapshot.debt_to_equity == pytest.approx(0.5)
        assert snapshot.debt_to_equity_prev == pytest.approx(0.6)
        assert snapshot.fcf_yield == pytest.approx(25_000 / 500_000)
        assert snapshot.revenue_growth_yoy == pytest.approx(10_000 / 90_000)
        assert snapshot.gross_margin == pytest.approx(0.45)
        assert snapshot.net_margin == pytest.approx(0.224)

    def test_dividend_yield_desde_el_dividendo_por_accion(self, snapshot):
        # Unidad inequívoca: 0.25 sobre precio 50 = 0.5%.
        assert snapshot.dividend_yield == pytest.approx(0.005)

    def test_snapshot_sano_no_deja_warnings(self, snapshot):
        assert snapshot.warnings == ()

    def test_pe_negativo_se_descarta(self):
        income = [dict(INCOME[0], netIncome=-5_000, epsDiluted=-1.0), INCOME[1]]
        adapter = FmpAdapter(FakeFmpClient({"income-statement": income}))
        snapshot = adapter.fetch("TEST")
        assert snapshot.pe is None

    def test_margen_bruto_cero_no_es_margen(self):
        # Como los bancos: 0 no es 0%, es un campo que no aplica.
        income = [dict(INCOME[0], grossProfit=0), INCOME[1]]
        snapshot = FmpAdapter(FakeFmpClient({"income-statement": income})).fetch("TEST")
        assert snapshot.gross_margin is None
        assert any("no aplica" in w for w in snapshot.warnings)

    def test_monedas_mixtas_omiten_los_multiplos_de_mercado(self):
        # ADR: balance en ARS, cotización en USD.
        income = [dict(INCOME[0], reportedCurrency="ARS"), dict(INCOME[1], reportedCurrency="ARS")]
        snapshot = FmpAdapter(FakeFmpClient({"income-statement": income})).fetch("TEST")
        assert snapshot.currency == "ARS"
        assert snapshot.quote_currency == "USD"
        assert snapshot.has_currency_mismatch
        assert snapshot.fcf_yield is None  # cruza mercado con balance
        assert snapshot.pb is None
        assert snapshot.roic is not None  # una sola punta: sigue valiendo

    def test_perfil_ausente_es_no_data(self):
        with pytest.raises(NoDataError):
            FmpAdapter(FakeFmpClient({"profile": []})).fetch("NADA")

    def test_requested_as_etiqueta_el_cedear(self):
        snapshot = FmpAdapter(FakeFmpClient()).fetch("GGAL", requested_as="GGAL.BA")
        assert snapshot.ticker == "GGAL.BA"
        assert snapshot.source_ticker == "GGAL"
        assert snapshot.is_cedear

    def test_absorbe_el_rename_v3(self):
        # v3 usaba mktCap, epsdiluted, totalStockholdersEquity con otra grafía.
        profile = {k: v for k, v in PROFILE.items() if k != "marketCap"}
        profile["mktCap"] = 500_000.0
        income = [{**INCOME[0]}, {**INCOME[1]}]
        for row in income:
            row["epsdiluted"] = row.pop("epsDiluted")
        snapshot = FmpAdapter(
            FakeFmpClient({"profile": [profile], "income-statement": income})
        ).fetch("TEST")
        assert snapshot.pe == pytest.approx(10.0)
        assert snapshot.pb == pytest.approx(500_000 / 60_000)


# --- build_profile_fmp (brief) ----------------------------------------------


class TestDeep:
    @pytest.fixture
    def profile(self):
        return build_profile_fmp("TEST", FakeFmpClient())

    def test_arma_la_serie(self, profile):
        assert profile.years == 2
        assert profile.history.latest.revenue == 100_000
        assert profile.history.latest.period_end == date(2025, 9, 30)

    def test_series_derivadas(self, profile):
        h = profile.history
        assert h.average("roic") is not None
        assert h.cagr("revenue") == pytest.approx((100_000 / 90_000) - 1)
        assert h.latest.net_debt == pytest.approx(30_000 - 20_000)

    def test_valuacion_vs_historia(self, profile):
        assert profile.valuation is not None
        # Hay 24 meses de precios (2024-2025) pero sólo 12 producen observación:
        # el filtro de look-ahead descarta los meses anteriores a que el balance
        # de 2024 fuera público (cierre sep-2024 + 90 días de rezago = fin de
        # 2024), así que sólo los cierres de 2025 valúan contra un balance ya
        # publicado. Eso es el feature, no un bug.
        assert profile.valuation.observations == 12

    def test_componentes_del_costo_de_capital(self, profile):
        c = profile.cost_of_capital
        assert c.beta == pytest.approx(1.1)
        assert c.market_cap == pytest.approx(500_000)
        # costo de deuda = intereses / deuda del ejercicio con dato
        assert c.cost_of_debt == pytest.approx(500 / 30_000)

    def test_une_los_estados_por_ano_no_por_indice(self):
        # Si al balance le falta el ejercicio 2024, ese año queda con equity None
        # en vez de correr el índice y tomar el 2025 dos veces.
        balance = [BALANCE[0]]  # sólo 2025
        profile = build_profile_fmp("TEST", FakeFmpClient({"balance-sheet-statement": balance}))
        periods = {p.period_end.year: p for p in profile.history.periods}
        assert periods[2025].total_equity == 60_000
        assert periods[2024].total_equity is None

    def test_monedas_mixtas_no_calculan_valuacion(self):
        income = [dict(INCOME[0], reportedCurrency="ARS"), dict(INCOME[1], reportedCurrency="ARS")]
        profile = build_profile_fmp("TEST", FakeFmpClient({"income-statement": income}))
        assert profile.valuation is None
        assert any("monedas distintas" in g for g in profile.gaps())

    def test_sin_precios_no_hay_valuacion(self):
        profile = build_profile_fmp("TEST", FakeFmpClient({"historical-price-eod/full": []}))
        assert profile.valuation is None

    def test_el_informe_declara_fmp_como_fuente(self, profile):
        output = render_data_block(profile)
        assert "FMP (Financial Modeling Prep)" in output
        assert "yfinance (Yahoo Finance)" not in output

    def test_provider_queda_registrado_en_el_profile(self, profile):
        # Bug real detectado contra producción: con --provider fmp, un ticker
        # directo (sin CEDEAR de por medio) mostraba "Segmentos de ingreso:
        # yfinance no los publica." — el mensaje estaba hardcodeado y acusaba
        # al proveedor equivocado sin importar cuál se usó realmente.
        assert profile.provider == "fmp"

    def test_los_gaps_acusan_al_proveedor_correcto(self, profile):
        gaps = profile.gaps()
        assert any("fmp" in g.lower() and "segmentos" in g.lower() for g in gaps)
        assert not any("yfinance" in g.lower() for g in gaps)

    def test_total_assets_y_retained_earnings_llegan_al_periodo(self, profile):
        latest = profile.history.latest
        assert latest.total_assets == 150_000
        assert latest.retained_earnings == 70_000
        assert latest.dividends_paid == -4_000
        assert latest.roa == pytest.approx(22_400 / 150_000)
        assert latest.payout_ratio == pytest.approx(4_000 / 22_400)

    def test_financial_strength_completo_con_datos_de_balance(self, profile):
        # A diferencia del fixture "healthy" de yfinance (sin Total Assets), acá
        # BALANCE trae todo lo que pide el Altman, así que también se calcula.
        fs = profile.financial_strength
        assert fs is not None
        assert fs.altman is not None
        assert fs.altman.zone == "segura"
        assert fs.piotroski is not None
        assert fs.piotroski.max_score == 9
        assert fs.piotroski.score == 8
        emision = next(c for c in fs.piotroski.criteria if c.name == "sin_nuevas_acciones")
        assert emision.passed is False  # las acciones en circulación subieron
