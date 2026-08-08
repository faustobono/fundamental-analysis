from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot.models import FundamentalSnapshot
from bot.scorer.metrics import (
    Method,
    MetricSpec,
    percentile_ranks,
    quantile,
    winsorize,
    zscores,
)
from bot.scorer.sector_scorer import SectorScorer


def snap(ticker: str, sector: str = "Technology", **metrics) -> FundamentalSnapshot:
    return FundamentalSnapshot(
        ticker=ticker,
        source_ticker=ticker,
        source="yfinance",
        as_of=datetime(2026, 7, 30, tzinfo=timezone.utc),
        sector=sector,
        **metrics,
    )


def universe(sector="Technology", **per_ticker) -> list[FundamentalSnapshot]:
    """`universe(A=dict(roic=0.3), B=dict(roic=0.1))` -> lista de snapshots."""
    return [snap(t, sector, **m) for t, m in per_ticker.items()]


FULL = dict(fcf_yield=0.05, roic=0.2, revenue_growth_yoy=0.1, debt_to_equity=0.5, debt_to_equity_prev=0.5)


class TestEstadistica:
    def test_percentiles_ordenan(self):
        assert percentile_ranks([10.0, 20.0, 30.0]) == [0.0, 0.5, 1.0]

    def test_percentiles_empatan_los_iguales(self):
        # Dos empresas con el mismo ROIC no pueden recibir puntajes distintos.
        ranks = percentile_ranks([10.0, 10.0, 30.0])
        assert ranks[0] == ranks[1]
        assert ranks[2] == 1.0

    def test_percentil_de_un_solo_valor_es_neutro(self):
        # 1.0 diría "el mejor del sector"; con una sola empresa no hay tal cosa.
        assert percentile_ranks([42.0]) == [0.5]

    def test_percentiles_de_muestra_vacia(self):
        assert percentile_ranks([]) == []

    def test_zscore_basico(self):
        scores = zscores([10.0, 20.0, 30.0])
        assert scores[1] == pytest.approx(0.0)
        assert scores[0] == pytest.approx(-scores[2])

    def test_zscore_sin_dispersion_no_divide_por_cero(self):
        assert zscores([5.0, 5.0, 5.0]) == [0.0, 0.0, 0.0]

    def test_quantile_interpola(self):
        assert quantile([0.0, 10.0], 0.5) == pytest.approx(5.0)
        assert quantile([0.0, 5.0, 10.0], 0.5) == pytest.approx(5.0)

    def test_winsorize_recorta_el_outlier(self):
        recortado = winsorize([1.0, 2.0, 3.0, 4.0, 1000.0], 0.2)
        assert max(recortado) < 1000.0
        assert min(recortado) >= 1.0

    def test_winsorize_no_toca_muestras_chicas(self):
        assert winsorize([1.0, 1000.0], 0.05) == [1.0, 1000.0]


class TestSeparacionPorSector:
    def test_cada_sector_se_rankea_por_separado(self):
        snapshots = [
            snap("AAPL", "Technology", **FULL),
            snap("MSFT", "Technology", **FULL),
            snap("JPM", "Financial Services", **FULL),
        ]
        result = SectorScorer().score(snapshots)
        assert sorted(result.sectors) == ["Financial Services", "Technology"]
        assert result.sectors["Technology"].peer_count == 2

    def test_un_banco_apalancado_no_compite_contra_una_tech(self):
        # Mismo D/E creciente en los dos, pero cada uno se mide contra su sector:
        # el banco lidera su grupo aunque su ratio absoluto sea peor.
        snapshots = [
            snap("BANK1", "Financial Services", roic=0.10, fcf_yield=0.08,
                 debt_to_equity=8.0, debt_to_equity_prev=9.0, revenue_growth_yoy=0.05),
            snap("BANK2", "Financial Services", roic=0.05, fcf_yield=0.03,
                 debt_to_equity=9.0, debt_to_equity_prev=8.0, revenue_growth_yoy=0.01),
            snap("TECH1", "Technology", roic=0.30, fcf_yield=0.02,
                 debt_to_equity=0.2, debt_to_equity_prev=0.1, revenue_growth_yoy=0.25),
        ]
        result = SectorScorer().score(snapshots)
        assert result.sectors["Financial Services"].ranked[0].ticker == "BANK1"

    def test_sector_faltante_cae_en_unknown(self):
        result = SectorScorer().score([snap("X", None, **FULL), snap("Y", None, **FULL)])
        assert "Unknown" in result.sectors

    def test_sector_chico_queda_marcado(self):
        # Con dos empresas, una saca percentil 0 y la otra 100: no significa nada.
        result = SectorScorer(min_peers=3).score(universe(A=FULL, B=FULL))
        assert result.sectors["Technology"].thin

    def test_sector_grande_no_queda_marcado(self):
        result = SectorScorer(min_peers=3).score(universe(A=FULL, B=FULL, C=FULL))
        assert not result.sectors["Technology"].thin


class TestRanking:
    def test_mejor_en_todo_queda_primero(self):
        snapshots = universe(
            BUENA=dict(fcf_yield=0.10, roic=0.30, revenue_growth_yoy=0.20,
                       debt_to_equity=0.4, debt_to_equity_prev=0.6),
            MEDIA=dict(fcf_yield=0.05, roic=0.15, revenue_growth_yoy=0.10,
                       debt_to_equity=0.5, debt_to_equity_prev=0.5),
            MALA=dict(fcf_yield=0.01, roic=0.05, revenue_growth_yoy=-0.05,
                      debt_to_equity=0.9, debt_to_equity_prev=0.5),
        )
        ranked = SectorScorer().score(snapshots).sectors["Technology"].ranked
        assert [s.ticker for s in ranked] == ["BUENA", "MEDIA", "MALA"]
        assert [s.rank for s in ranked] == [1, 2, 3]

    def test_el_desapalancamiento_suma(self):
        # Igual en todo salvo la tendencia de deuda: baja deuda -> mejor.
        snapshots = universe(
            BAJANDO=dict(fcf_yield=0.05, roic=0.2, revenue_growth_yoy=0.1,
                         debt_to_equity=0.4, debt_to_equity_prev=0.8),
            SUBIENDO=dict(fcf_yield=0.05, roic=0.2, revenue_growth_yoy=0.1,
                          debt_to_equity=0.8, debt_to_equity_prev=0.4),
            QUIETA=dict(fcf_yield=0.05, roic=0.2, revenue_growth_yoy=0.1,
                        debt_to_equity=0.5, debt_to_equity_prev=0.5),
        )
        ranked = SectorScorer().score(snapshots).sectors["Technology"].ranked
        assert [s.ticker for s in ranked] == ["BAJANDO", "QUIETA", "SUBIENDO"]

    def test_empate_se_desempata_alfabeticamente(self):
        ranked = SectorScorer().score(universe(ZZZ=FULL, AAA=FULL, MMM=FULL)).sectors["Technology"].ranked
        assert [s.ticker for s in ranked] == ["AAA", "MMM", "ZZZ"]

    def test_top_n_por_sector(self):
        snapshots = [
            *universe("Technology", A=dict(roic=0.3, fcf_yield=0.09, revenue_growth_yoy=0.3),
                      B=dict(roic=0.2, fcf_yield=0.06, revenue_growth_yoy=0.2),
                      C=dict(roic=0.1, fcf_yield=0.03, revenue_growth_yoy=0.1)),
            *universe("Energy", X=dict(roic=0.3, fcf_yield=0.09, revenue_growth_yoy=0.3),
                      Y=dict(roic=0.1, fcf_yield=0.03, revenue_growth_yoy=0.1)),
        ]
        top = SectorScorer(min_peers=2).score(snapshots).top_n(1)
        assert sorted(s.ticker for s in top) == ["A", "X"]

    def test_top_n_mas_grande_que_el_sector_no_falla(self):
        result = SectorScorer().score(universe(A=FULL, B=FULL))
        assert len(result.top_n(50)) == 2


class TestFaltantes:
    def test_una_metrica_faltante_no_hunde_a_la_empresa(self):
        # SIN_FCF gana en todo lo que reporta; que le falte FCF yield no puede
        # costarle el primer puesto contra una peor en todo.
        snapshots = universe(
            SIN_FCF=dict(roic=0.40, revenue_growth_yoy=0.30, debt_to_equity=0.2, debt_to_equity_prev=0.5),
            COMPLETA=dict(fcf_yield=0.02, roic=0.05, revenue_growth_yoy=0.01,
                          debt_to_equity=0.9, debt_to_equity_prev=0.4),
            OTRA=dict(fcf_yield=0.03, roic=0.10, revenue_growth_yoy=0.05,
                      debt_to_equity=0.5, debt_to_equity_prev=0.5),
        )
        ranked = SectorScorer().score(snapshots).sectors["Technology"].ranked
        assert ranked[0].ticker == "SIN_FCF"
        assert "fcf_yield" in ranked[0].missing

    def test_los_faltantes_no_cuentan_como_cero(self):
        # Si el faltante se imputara como 0, SIN_DATO quedaría en el fondo.
        snapshots = universe(
            SIN_DATO=dict(roic=0.20, revenue_growth_yoy=0.10),
            PEOR=dict(fcf_yield=-0.50, roic=0.01, revenue_growth_yoy=-0.20),
            MEDIA=dict(fcf_yield=0.02, roic=0.10, revenue_growth_yoy=0.05),
        )
        ranked = SectorScorer().score(snapshots).sectors["Technology"].ranked
        assert ranked[-1].ticker == "PEOR"

    def test_los_pesos_se_renormalizan(self):
        # Con sólo dos métricas presentes, el compuesto sigue estando en [0,1]:
        # no queda diluido por el peso de las que faltan.
        snapshots = universe(
            A=dict(roic=0.3, revenue_growth_yoy=0.3),
            B=dict(roic=0.1, revenue_growth_yoy=0.1),
        )
        ranked = SectorScorer(min_peers=2).score(snapshots).sectors["Technology"].ranked
        assert ranked[0].composite == pytest.approx(1.0)
        assert ranked[1].composite == pytest.approx(0.0)

    def test_cobertura_insuficiente_no_se_rankea(self):
        snapshots = universe(SOLA=dict(roic=0.2), A=FULL, B=FULL)
        ranking = SectorScorer(min_metrics=2).score(snapshots).sectors["Technology"]
        assert [s.ticker for s in ranking.ranked] == ["A", "B"]
        assert ranking.unrankable[0][0].ticker == "SOLA"
        assert "mínimo 2" in ranking.unrankable[0][1]

    def test_los_no_rankeables_se_reportan_no_se_pierden(self):
        result = SectorScorer(min_metrics=3).score(universe(SOLA=dict(roic=0.2), A=FULL, B=FULL))
        assert [s.ticker for s, _ in result.unrankable] == ["SOLA"]

    def test_metrica_ausente_en_todo_el_sector_se_ignora(self):
        # Los bancos no reportan FCF ni ROIC: el sector se rankea igual con lo
        # que queda, en vez de quedar entero sin puntaje.
        snapshots = universe(
            "Financial Services",
            BANK1=dict(revenue_growth_yoy=0.20, debt_to_equity=8.0, debt_to_equity_prev=9.0),
            BANK2=dict(revenue_growth_yoy=0.05, debt_to_equity=9.0, debt_to_equity_prev=8.0),
            BANK3=dict(revenue_growth_yoy=0.10, debt_to_equity=8.5, debt_to_equity_prev=8.5),
        )
        ranking = SectorScorer().score(snapshots).sectors["Financial Services"]
        assert [s.ticker for s in ranking.ranked] == ["BANK1", "BANK3", "BANK2"]
        assert ranking.unrankable == ()

    def test_cobertura_reportada(self):
        score = SectorScorer().score(universe(A=dict(roic=0.2, fcf_yield=0.05), B=FULL)).sectors[
            "Technology"
        ]
        a = next(s for s in score.ranked if s.ticker == "A")
        assert a.coverage == pytest.approx(0.5)


class TestMetodos:
    def test_zscore_premia_la_magnitud_no_solo_el_orden(self):
        # Por percentil, B y C están igual de separados de A. Por z-score, la
        # distancia real de A al resto se nota.
        snapshots = universe(
            A=dict(roic=1.0, fcf_yield=0.5, revenue_growth_yoy=1.0),
            B=dict(roic=0.11, fcf_yield=0.051, revenue_growth_yoy=0.11),
            C=dict(roic=0.10, fcf_yield=0.050, revenue_growth_yoy=0.10),
        )
        z = SectorScorer(method=Method.ZSCORE, winsorize_limit=0.0).score(snapshots)
        ranked = z.sectors["Technology"].ranked
        assert ranked[0].ticker == "A"
        assert ranked[0].composite - ranked[1].composite > ranked[1].composite - ranked[2].composite

    def test_percentil_es_robusto_al_outlier(self):
        snapshots = universe(
            LOCA=dict(roic=50.0, fcf_yield=0.01, revenue_growth_yoy=0.01),
            A=dict(roic=0.30, fcf_yield=0.09, revenue_growth_yoy=0.20),
            B=dict(roic=0.20, fcf_yield=0.08, revenue_growth_yoy=0.15),
        )
        ranked = SectorScorer(method=Method.PERCENTILE).score(snapshots).sectors["Technology"].ranked
        # Un ROIC de 5000% no le compra el primer puesto si pierde en todo lo demás.
        assert ranked[0].ticker == "A"

    def test_winsorize_limita_al_outlier_en_zscore(self):
        snapshots = universe(
            LOCA=dict(roic=50.0, fcf_yield=0.01, revenue_growth_yoy=0.01),
            A=dict(roic=0.30, fcf_yield=0.09, revenue_growth_yoy=0.20),
            B=dict(roic=0.20, fcf_yield=0.08, revenue_growth_yoy=0.15),
            C=dict(roic=0.10, fcf_yield=0.07, revenue_growth_yoy=0.10),
            D=dict(roic=0.05, fcf_yield=0.06, revenue_growth_yoy=0.05),
        )
        sin_recorte = SectorScorer(method=Method.ZSCORE, winsorize_limit=0.0).score(snapshots)
        con_recorte = SectorScorer(method=Method.ZSCORE, winsorize_limit=0.25).score(snapshots)
        z_sin = next(s for s in sin_recorte.sectors["Technology"].ranked if s.ticker == "LOCA")
        z_con = next(s for s in con_recorte.sectors["Technology"].ranked if s.ticker == "LOCA")
        assert z_con.metrics["roic"].score < z_sin.metrics["roic"].score

    def test_los_percentiles_estan_en_rango(self):
        result = SectorScorer().score(universe(A=FULL, B=FULL, C=FULL))
        for score in result.all_scores:
            assert 0.0 <= score.composite <= 1.0


class TestConfiguracion:
    def test_metricas_a_medida(self):
        # Un value investor podría rankear sólo por lo barato que está.
        metrics = (MetricSpec("pe", higher_is_better=False), MetricSpec("pb", higher_is_better=False))
        snapshots = universe(
            BARATA=dict(pe=8.0, pb=1.0),
            CARA=dict(pe=40.0, pb=12.0),
            MEDIA=dict(pe=18.0, pb=3.0),
        )
        ranked = SectorScorer(metrics, min_metrics=1).score(snapshots).sectors["Technology"].ranked
        assert [s.ticker for s in ranked] == ["BARATA", "MEDIA", "CARA"]

    def test_los_pesos_cambian_el_resultado(self):
        snapshots = universe(
            CRECE=dict(roic=0.10, revenue_growth_yoy=0.50),
            RINDE=dict(roic=0.40, revenue_growth_yoy=0.05),
        )
        pro_roic = (MetricSpec("roic", weight=10.0), MetricSpec("revenue_growth_yoy", weight=1.0))
        pro_growth = (MetricSpec("roic", weight=1.0), MetricSpec("revenue_growth_yoy", weight=10.0))
        assert SectorScorer(pro_roic, min_peers=2).score(snapshots).sectors["Technology"].ranked[0].ticker == "RINDE"
        assert SectorScorer(pro_growth, min_peers=2).score(snapshots).sectors["Technology"].ranked[0].ticker == "CRECE"

    def test_peso_invalido_falla_temprano(self):
        with pytest.raises(ValueError):
            MetricSpec("roic", weight=0.0)

    def test_metrica_inexistente_falla_al_puntuar(self):
        # Un typo en la config no puede degradarse en "todos sin dato".
        with pytest.raises(KeyError):
            SectorScorer((MetricSpec("roci"),)).score(universe(A=FULL, B=FULL))

    def test_sin_metricas_no_arranca(self):
        with pytest.raises(ValueError):
            SectorScorer(metrics=())


class TestDeterminismo:
    def test_el_orden_de_entrada_no_cambia_el_ranking(self):
        snapshots = universe(
            A=dict(roic=0.3, fcf_yield=0.05, revenue_growth_yoy=0.2),
            B=dict(roic=0.2, fcf_yield=0.07, revenue_growth_yoy=0.1),
            C=dict(roic=0.1, fcf_yield=0.09, revenue_growth_yoy=0.3),
        )
        directo = SectorScorer().score(snapshots).sectors["Technology"]
        invertido = SectorScorer().score(list(reversed(snapshots))).sectors["Technology"]
        assert [s.ticker for s in directo.ranked] == [s.ticker for s in invertido.ranked]
        assert [s.composite for s in directo.ranked] == [s.composite for s in invertido.ranked]

    def test_dos_corridas_dan_lo_mismo(self):
        snapshots = universe(A=FULL, B=dict(roic=0.1), C=dict(fcf_yield=0.02, roic=0.3))
        primera = SectorScorer().score(snapshots)
        segunda = SectorScorer().score(snapshots)
        assert [s.composite for s in primera.all_scores] == [s.composite for s in segunda.all_scores]

    def test_universo_vacio(self):
        assert SectorScorer().score([]).sectors == {}
