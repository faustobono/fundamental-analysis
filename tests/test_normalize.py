from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot.models import FundamentalSnapshot
from bot.normalizer.fx import NullFxProvider, StaticFxProvider
from bot.normalizer.normalize import canonical_sector, normalize


def make(**kwargs) -> FundamentalSnapshot:
    base = dict(
        ticker="test",
        source_ticker="test",
        source="yfinance",
        as_of=datetime(2026, 7, 30, tzinfo=timezone.utc),
        sector="Technology",
        currency="USD",
    )
    base.update(kwargs)
    return FundamentalSnapshot(**base)


class TestCanonicalizacion:
    def test_normaliza_tickers_a_mayuscula(self):
        snapshot = normalize(make(ticker="ggal.ba", source_ticker="ggal"))
        assert snapshot.ticker == "GGAL.BA"
        assert snapshot.source_ticker == "GGAL"

    @pytest.mark.parametrize(
        "entrada,esperado",
        [
            ("Financial", "Financial Services"),
            ("financial services", "Financial Services"),
            ("Information Technology", "Technology"),
            ("Consumer Discretionary", "Consumer Cyclical"),
            ("Health Care", "Healthcare"),
        ],
    )
    def test_unifica_nomenclatura_de_sector(self, entrada, esperado):
        # Si dos fuentes nombran distinto al mismo sector, el scorer partiría el
        # grupo de peers en dos y compararía contra menos empresas.
        assert canonical_sector(entrada) == esperado

    def test_sector_faltante_es_unknown(self):
        assert canonical_sector(None) == "Unknown"
        assert canonical_sector("   ") == "Unknown"
        assert normalize(make(sector=None)).sector == "Unknown"

    def test_sector_desconocido_se_preserva(self):
        assert canonical_sector("Shipping") == "Shipping"

    def test_moneda_a_mayuscula(self):
        assert normalize(make(currency="usd")).currency == "USD"


class TestUnidades:
    def test_no_reinterpreta_unidades(self):
        # Convertir unidades es responsabilidad del adapter, que sabe qué
        # significa cada campo de su proveedor. Acá un 0.0235 es 2.35% y punto.
        snapshot = normalize(make(dividend_yield=0.0235))
        assert snapshot.dividend_yield == pytest.approx(0.0235)
        assert snapshot.warnings == ()

    def test_un_yield_imposible_se_descarta_en_vez_de_reescalarse(self):
        # 2.35 sería 235% anual. No se asume "quiso decir 2.35%": se descarta,
        # porque adivinar la unidad por magnitud produce datos falsos que se ven
        # razonables (0.32 es tan válido como 32% que como 0.32%).
        snapshot = normalize(make(dividend_yield=2.35))
        assert snapshot.dividend_yield is None
        assert any("dividend_yield" in w for w in snapshot.warnings)


class TestRangosPlausibles:
    @pytest.mark.parametrize(
        "campo,valor",
        [
            ("pe", 5000.0),
            ("pb", -3.0),
            ("roe", 50.0),
            ("debt_to_equity", -2.0),
            ("gross_margin", 3.0),
            ("revenue_growth_yoy", -2.0),
        ],
    )
    def test_valores_absurdos_pasan_a_none(self, campo, valor):
        snapshot = normalize(make(**{campo: valor}))
        assert getattr(snapshot, campo) is None
        assert any(campo in w for w in snapshot.warnings)

    def test_valores_extremos_pero_reales_sobreviven(self):
        # Una tech en hipercrecimiento con margen alto y sin deuda es rara, no
        # imposible: descartarla sesgaría el ranking.
        snapshot = normalize(
            make(pe=180.0, gross_margin=0.92, revenue_growth_yoy=2.5, debt_to_equity=0.0)
        )
        assert snapshot.pe == 180.0
        assert snapshot.gross_margin == 0.92
        assert snapshot.revenue_growth_yoy == 2.5
        assert snapshot.debt_to_equity == 0.0

    def test_descartar_una_metrica_no_afecta_al_resto(self):
        snapshot = normalize(make(pe=99_999.0, roic=0.25))
        assert snapshot.pe is None
        assert snapshot.roic == 0.25

    def test_none_sigue_siendo_none(self):
        assert normalize(make()).pe is None


class TestMoneda:
    def test_por_defecto_no_convierte(self):
        snapshot = normalize(make(currency="ARS", market_cap=1_000_000.0))
        assert snapshot.currency == "ARS"
        assert snapshot.market_cap == 1_000_000.0

    def test_convierte_montos_absolutos(self):
        fx = StaticFxProvider({"ARS": 0.001})  # 1 ARS = 0.001 USD
        snapshot = normalize(
            make(currency="ARS", market_cap=1_000_000.0, revenue=500_000.0),
            target_currency="USD",
            fx=fx,
        )
        assert snapshot.currency == "USD"
        assert snapshot.market_cap == pytest.approx(1_000.0)
        assert snapshot.revenue == pytest.approx(500.0)

    def test_no_toca_los_ratios(self):
        # El punto entero de rankear por ratios: son invariantes a la moneda.
        fx = StaticFxProvider({"ARS": 0.001})
        original = make(currency="ARS", market_cap=1_000_000.0, pe=12.0, roic=0.18, fcf_yield=0.07)
        convertido = normalize(original, target_currency="USD", fx=fx)
        assert convertido.pe == 12.0
        assert convertido.roic == 0.18
        assert convertido.fcf_yield == 0.07

    def test_misma_moneda_es_no_op(self):
        snapshot = normalize(make(currency="USD", market_cap=100.0), target_currency="USD", fx=NullFxProvider())
        assert snapshot.market_cap == 100.0
        assert snapshot.warnings == ()

    def test_sin_cotizacion_deja_el_monto_intacto_y_avisa(self):
        # Preferimos un monto honesto en su moneda que uno convertido a un tipo
        # de cambio inventado.
        snapshot = normalize(
            make(currency="ARS", market_cap=1_000_000.0), target_currency="USD", fx=NullFxProvider()
        )
        assert snapshot.market_cap == 1_000_000.0
        assert snapshot.currency == "ARS"
        assert any("sin cotización" in w for w in snapshot.warnings)

    def test_sin_moneda_declarada_no_convierte(self):
        snapshot = normalize(make(currency=None, market_cap=100.0), target_currency="USD")
        assert any("no declara" in w for w in snapshot.warnings)

    def test_balance_y_cotizacion_se_convierten_con_su_propia_moneda(self):
        # ADR argentino: reporta en ARS, cotiza en USD. Aplicarle el tipo de
        # cambio del balance al market cap lo dividiría por mil.
        fx = StaticFxProvider({"ARS": 0.001})
        snapshot = normalize(
            make(currency="ARS", quote_currency="USD", revenue=1_000_000.0, market_cap=8_000.0),
            target_currency="USD",
            fx=fx,
        )
        assert snapshot.revenue == pytest.approx(1_000.0)
        assert snapshot.market_cap == pytest.approx(8_000.0)  # ya estaba en USD
        assert snapshot.currency == "USD"
        assert snapshot.quote_currency == "USD"

    def test_sin_quote_currency_se_asume_la_del_balance(self):
        fx = StaticFxProvider({"ARS": 0.001})
        snapshot = normalize(
            make(currency="ARS", quote_currency=None, market_cap=1_000_000.0),
            target_currency="USD",
            fx=fx,
        )
        assert snapshot.market_cap == pytest.approx(1_000.0)


class TestMonedasMixtas:
    def test_detecta_el_desfasaje(self):
        assert make(currency="ARS", quote_currency="USD").has_currency_mismatch
        assert not make(currency="USD", quote_currency="USD").has_currency_mismatch

    def test_sin_dato_no_se_asume_desfasaje(self):
        assert not make(currency="USD", quote_currency=None).has_currency_mismatch


class TestPureza:
    def test_no_muta_la_entrada(self):
        original = make(dividend_yield=2.35, sector="Financial")
        normalize(original)
        assert original.dividend_yield == 2.35
        assert original.sector == "Financial"

    def test_es_idempotente(self):
        una_vez = normalize(make(dividend_yield=2.35, sector="Financial", pe=99_999.0))
        dos_veces = normalize(una_vez)
        assert dos_veces.replace(warnings=()) == una_vez.replace(warnings=())

    def test_preserva_warnings_previos_del_adapter(self):
        snapshot = normalize(make(warnings=("nota del adapter",), dividend_yield=2.35))
        assert "nota del adapter" in snapshot.warnings
        assert len(snapshot.warnings) == 2


class TestFx:
    def test_conversion_cruzada(self):
        fx = StaticFxProvider({"ARS": 0.001, "EUR": 1.10})
        assert fx.rate("EUR", "USD") == pytest.approx(1.10)
        assert fx.rate("USD", "ARS") == pytest.approx(1000.0)
        assert fx.rate("EUR", "ARS") == pytest.approx(1100.0)

    def test_moneda_desconocida_no_cotiza(self):
        assert StaticFxProvider({}).rate("ARS", "USD") is None

    def test_null_provider_solo_identidad(self):
        assert NullFxProvider().rate("USD", "USD") == 1.0
        assert NullFxProvider().rate("ARS", "USD") is None
