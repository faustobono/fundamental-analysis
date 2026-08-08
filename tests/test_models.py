from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from bot.models import FundamentalSnapshot


def make(**kwargs) -> FundamentalSnapshot:
    base = dict(
        ticker="TEST",
        source_ticker="TEST",
        source="yfinance",
        as_of=datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
    )
    base.update(kwargs)
    return FundamentalSnapshot(**base)


class TestCoercion:
    def test_nan_e_inf_se_vuelven_none(self):
        snapshot = make(pe=float("nan"), roe=float("inf"), pb=float("-inf"))
        assert snapshot.pe is None
        assert snapshot.roe is None
        assert snapshot.pb is None

    def test_strings_numericos_se_convierten(self):
        assert make(pe="18.5").pe == 18.5

    def test_strings_no_numericos_se_descartan(self):
        assert make(pe="N/A").pe is None

    def test_bool_no_es_un_ratio(self):
        # yfinance devuelve booleanos en algunos campos de .info; que True se
        # cuele como 1.0 sería un dato falso silencioso.
        assert make(roe=True).roe is None

    def test_strings_vacios_de_clasificacion_se_vuelven_none(self):
        assert make(sector="").sector is None


class TestDerivados:
    def test_trend_de_deuda(self):
        snapshot = make(debt_to_equity=0.5, debt_to_equity_prev=0.6)
        assert snapshot.debt_to_equity_trend == pytest.approx(-0.1)

    def test_trend_none_si_falta_una_punta(self):
        assert make(debt_to_equity=0.5).debt_to_equity_trend is None
        assert make(debt_to_equity_prev=0.5).debt_to_equity_trend is None

    def test_is_cedear(self):
        assert make(ticker="GGAL.BA", source_ticker="GGAL").is_cedear
        assert not make().is_cedear

    def test_metric_incluye_derivadas(self):
        snapshot = make(debt_to_equity=0.5, debt_to_equity_prev=0.6)
        assert snapshot.metric("debt_to_equity_trend") == pytest.approx(-0.1)
        assert snapshot.metric("pe") is None

    def test_metric_desconocida_falla_fuerte(self):
        # Un typo en la config del scorer tiene que explotar, no rankear con None.
        with pytest.raises(KeyError):
            make().metric("magic_ratio")


class TestInmutabilidad:
    def test_replace_no_muta_el_original(self):
        original = make(pe=20.0)
        modificado = original.replace(pe=25.0)
        assert original.pe == 20.0
        assert modificado.pe == 25.0

    def test_with_warning_no_duplica(self):
        snapshot = make().with_warning("ojo").with_warning("ojo")
        assert snapshot.warnings == ("ojo",)


class TestSerializacion:
    def test_roundtrip_json(self):
        original = make(pe=20.0, sector="Technology", warnings=("nota",))
        restaurado = FundamentalSnapshot.from_json(original.to_json())
        assert restaurado == original

    def test_roundtrip_preserva_timezone(self):
        original = make()
        assert FundamentalSnapshot.from_json(original.to_json()).as_of == original.as_of

    def test_from_dict_ignora_campos_desconocidos(self):
        # Un cache escrito por una versión más nueva no debe romper el arranque.
        data = make().to_dict()
        data["metrica_del_futuro"] = 1.23
        assert FundamentalSnapshot.from_dict(data).ticker == "TEST"

    def test_json_es_estable(self):
        # sort_keys=True: dos snapshots iguales serializan igual, así el cache
        # no ensucia el diff ni invalida entradas por orden de claves.
        assert make(pe=1.0).to_json() == make(pe=1.0).to_json()

    def test_nan_no_llega_al_json(self):
        payload = make(pe=float("nan")).to_json()
        assert "NaN" not in payload
        assert not math.isnan(float(FundamentalSnapshot.from_json(payload).pe or 0))
