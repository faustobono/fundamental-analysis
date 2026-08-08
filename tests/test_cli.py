"""Tests de la CLI: parseo de argumentos y armado del universo. Sin red."""

from __future__ import annotations

import pytest

from bot.cli.main import EXIT_USAGE, build_parser, collect_tickers, parse_metrics, read_tickers_file
from bot.scorer.metrics import DEFAULT_METRICS, Method


def parse(argv):
    return build_parser().parse_args(argv)


class TestParser:
    def test_comando_del_readme(self):
        args = parse(["screener", "--tickers", "AAPL,GGAL.BA,MSFT"])
        assert args.command == "screener"
        assert args.tickers == "AAPL,GGAL.BA,MSFT"

    def test_defaults(self):
        args = parse(["screener", "--tickers", "AAPL"])
        assert args.top_n == 5
        assert args.method == Method.PERCENTILE.value
        assert args.cache_ttl_hours == 24.0
        assert not args.no_cache

    def test_modo_batch(self):
        args = parse(["screener", "--tickers-file", "universo.txt"])
        assert args.tickers_file == "universo.txt"

    def test_metodo_invalido_falla(self):
        with pytest.raises(SystemExit):
            parse(["screener", "--tickers", "AAPL", "--method", "magia"])

    def test_sin_subcomando_falla(self):
        with pytest.raises(SystemExit):
            parse([])


class TestUniverso:
    def test_separa_por_comas(self):
        assert collect_tickers(parse(["screener", "--tickers", "AAPL,MSFT"])) == ["AAPL", "MSFT"]

    def test_normaliza_a_mayuscula(self):
        assert collect_tickers(parse(["screener", "--tickers", "aapl,ggal.ba"])) == [
            "AAPL",
            "GGAL.BA",
        ]

    def test_deduplica_manteniendo_el_orden(self):
        args = parse(["screener", "--tickers", "AAPL, MSFT ,aapl"])
        assert collect_tickers(args) == ["AAPL", "MSFT"]

    def test_combina_flag_y_archivo(self, tmp_path):
        path = tmp_path / "universo.txt"
        path.write_text("MSFT\nNVDA\n", encoding="utf-8")
        args = parse(["screener", "--tickers", "AAPL", "--tickers-file", str(path)])
        assert collect_tickers(args) == ["AAPL", "MSFT", "NVDA"]

    def test_archivo_inexistente_es_error_claro(self):
        args = parse(["screener", "--tickers-file", "/no/existe.txt"])
        with pytest.raises(FileNotFoundError):
            collect_tickers(args)


class TestArchivoDeTickers:
    def test_uno_por_linea(self, tmp_path):
        path = tmp_path / "u.txt"
        path.write_text("AAPL\nMSFT\nGGAL.BA\n", encoding="utf-8")
        assert read_tickers_file(path) == ["AAPL", "MSFT", "GGAL.BA"]

    def test_ignora_vacios_y_comentarios(self, tmp_path):
        path = tmp_path / "u.txt"
        path.write_text("# tech\nAAPL\n\n  \nMSFT  # el de siempre\n", encoding="utf-8")
        assert read_tickers_file(path) == ["AAPL", "MSFT"]

    def test_acepta_lineas_con_comas(self, tmp_path):
        path = tmp_path / "u.txt"
        path.write_text("AAPL, MSFT\nNVDA\n", encoding="utf-8")
        assert read_tickers_file(path) == ["AAPL", "MSFT", "NVDA"]

    def test_archivo_vacio(self, tmp_path):
        path = tmp_path / "u.txt"
        path.write_text("", encoding="utf-8")
        assert read_tickers_file(path) == []


class TestMetricas:
    def test_sin_flag_usa_las_default(self):
        assert parse_metrics(None) == DEFAULT_METRICS

    def test_nombre_solo(self):
        metrics = parse_metrics("roic,fcf_yield")
        assert [m.name for m in metrics] == ["roic", "fcf_yield"]
        assert all(m.weight == 1.0 and m.higher_is_better for m in metrics)

    def test_peso(self):
        assert parse_metrics("roic:2.5")[0].weight == 2.5

    def test_direccion(self):
        assert not parse_metrics("debt_to_equity:1:lower")[0].higher_is_better
        assert parse_metrics("roic:1:higher")[0].higher_is_better

    def test_direccion_invalida_falla_con_mensaje_util(self):
        with pytest.raises(ValueError, match="lower"):
            parse_metrics("roic:1:arriba")

    def test_peso_invalido_falla(self):
        with pytest.raises(ValueError):
            parse_metrics("roic:mucho")

    def test_peso_cero_falla(self):
        with pytest.raises(ValueError):
            parse_metrics("roic:0")


class TestRun:
    def test_sin_tickers_es_error_de_uso(self, capsys):
        assert build_parser().parse_args(["screener"]).func(parse(["screener"])) == EXIT_USAGE
        assert "--tickers" in capsys.readouterr().err
