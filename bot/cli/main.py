"""Entry point: `python -m bot screener --tickers AAPL,GGAL.BA,MSFT`.

La CLI sólo cablea capas y formatea salida. Ninguna regla de negocio vive acá.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional, Sequence

from ..fetcher.cache import DEFAULT_TTL_HOURS
from ..fetcher.service import BatchResult, build_service
from ..models import FetchError
from ..scorer.metrics import DEFAULT_METRICS, Method, MetricSpec
from ..scorer.sector_scorer import (
    DEFAULT_MIN_METRICS,
    DEFAULT_MIN_PEERS,
    ScoringResult,
    SectorScorer,
    describe_metric,
)

logger = logging.getLogger("bot")

EXIT_OK = 0
EXIT_NO_DATA = 1
EXIT_USAGE = 2


# --- entrada ---------------------------------------------------------------


def read_tickers_file(path: Path) -> list[str]:
    """Un ticker por línea. Ignora vacíos y comentarios con '#'."""
    tickers: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            tickers.extend(t.strip() for t in line.split(",") if t.strip())
    return tickers


def collect_tickers(args: argparse.Namespace) -> list[str]:
    tickers: list[str] = []
    if args.tickers:
        tickers.extend(t.strip() for t in args.tickers.split(",") if t.strip())
    if args.tickers_file:
        path = Path(args.tickers_file)
        if not path.exists():
            raise FileNotFoundError(f"no existe el archivo de tickers: {path}")
        tickers.extend(read_tickers_file(path))

    seen: set[str] = set()
    unique = []
    for ticker in tickers:
        key = ticker.upper()
        if key not in seen:
            seen.add(key)
            unique.append(key)
    return unique


def parse_metrics(spec: Optional[str]) -> tuple[MetricSpec, ...]:
    """`--metrics "roic:2,fcf_yield,debt_to_equity_trend:1:lower"` -> MetricSpecs.

    Formato por métrica: `nombre[:peso[:lower|higher]]`.
    """
    if not spec:
        return DEFAULT_METRICS

    metrics: list[MetricSpec] = []
    for chunk in spec.split(","):
        parts = [p.strip() for p in chunk.split(":") if p.strip()]
        if not parts:
            continue
        name = parts[0]
        weight = float(parts[1]) if len(parts) > 1 else 1.0
        higher = True
        if len(parts) > 2:
            direction = parts[2].lower()
            if direction not in ("lower", "higher"):
                raise ValueError(f"dirección inválida en '{chunk}': usá 'lower' o 'higher'")
            higher = direction == "higher"
        metrics.append(MetricSpec(name, weight=weight, higher_is_better=higher))

    if not metrics:
        raise ValueError("--metrics quedó vacío")
    return tuple(metrics)


# --- salida ----------------------------------------------------------------


def print_failures(result: BatchResult) -> None:
    if not result.failures:
        return
    print(f"\n⚠  {len(result.failures)} ticker(s) sin datos:")
    for ticker, reason in result.failures:
        print(f"   {ticker}: {reason}")


def print_rankings(scoring: ScoringResult, method: Method, top_n: int) -> None:
    for sector in sorted(scoring.sectors):
        ranking = scoring.sectors[sector]
        if not ranking.ranked and not ranking.unrankable:
            continue

        header = f"{sector}  ({ranking.peer_count} empresa(s))"
        if ranking.thin:
            header += "  ⚠ pocos peers: el percentil sectorial no es significativo"
        print(f"\n{'=' * 78}\n{header}\n{'=' * 78}")

        for score in ranking.top(top_n):
            name = score.snapshot.company_name or ""
            print(f"\n  #{score.rank}  {score.ticker:<10} {name[:44]}")
            print(f"      puntaje {score.composite:.3f}   cobertura {score.coverage:.0%}")
            for metric in score.metrics.values():
                print(f"      · {describe_metric(metric, method)}")
            if score.missing:
                print(f"      · sin dato: {', '.join(score.missing)}")
            for warning in score.snapshot.warnings:
                print(f"      ⚠ {warning}")

        for snapshot, reason in ranking.unrankable:
            print(f"\n  --  {snapshot.ticker:<10} sin rankear: {reason}")


def build_json_output(
    scoring: ScoringResult,
    result: BatchResult,
    top_n: int,
) -> dict[str, object]:
    return {
        "sectors": {
            sector: {
                "thin": ranking.thin,
                "peer_count": ranking.peer_count,
                "ranked": [
                    {
                        "rank": score.rank,
                        "ticker": score.ticker,
                        "composite": round(score.composite, 6),
                        "coverage": round(score.coverage, 4),
                        "missing": list(score.missing),
                        "metrics": {
                            name: {"raw": m.raw, "score": round(m.score, 6), "peers": m.peers}
                            for name, m in score.metrics.items()
                        },
                        "snapshot": score.snapshot.to_dict(),
                    }
                    for score in ranking.top(top_n)
                ],
                "unrankable": [
                    {"ticker": s.ticker, "reason": reason} for s, reason in ranking.unrankable
                ],
            }
            for sector, ranking in sorted(scoring.sectors.items())
        },
        "failures": [{"ticker": t, "reason": r} for t, r in result.failures],
    }


# --- comando ---------------------------------------------------------------


def run_screener(args: argparse.Namespace) -> int:
    try:
        tickers = collect_tickers(args)
        metrics = parse_metrics(args.metrics)
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if not tickers:
        print("error: hace falta --tickers o --tickers-file", file=sys.stderr)
        return EXIT_USAGE

    logger.info("universo: %d ticker(s)", len(tickers))

    service, cache = build_service(
        provider=args.provider,
        cache_path=args.cache_path,
        ttl_hours=args.cache_ttl_hours,
        use_cache=not args.no_cache,
    )
    try:
        result = service.get_many(tickers)
    finally:
        cache.close()

    if not result.snapshots:
        print_failures(result)
        print("\nno se pudo traer ningún fundamental; no hay nada que rankear.", file=sys.stderr)
        return EXIT_NO_DATA

    scorer = SectorScorer(
        metrics,
        method=Method(args.method),
        min_peers=args.min_peers,
        min_metrics=args.min_metrics,
    )
    scoring = scorer.score(result.snapshots)

    if args.json_out:
        payload = build_json_output(scoring, result, args.top_n)
        Path(args.json_out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        print(f"resultado escrito en {args.json_out}")
    else:
        print_rankings(scoring, Method(args.method), args.top_n)
        print_failures(result)

    return EXIT_OK


def run_brief(args: argparse.Namespace) -> int:
    """Arma el prompt de análisis con los datos de Capa 1 ya calculados."""
    # Import diferido: traer 5 años de balances y precios es mucho más caro que
    # el screener, y no tiene por qué cargarse cuando no se usa.
    from ..analysis.profile import company_profile
    from ..brief.prompt import build_prompt
    from ..brief.render import render_data_block
    from ..fetcher.byma_adapter import resolve_symbol

    try:
        symbol, requested_as = resolve_symbol(args.ticker)
    except FetchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NO_DATA
    if requested_as:
        logger.info("%s -> %s", requested_as, symbol)

    context = None
    if args.context_file:
        context = Path(args.context_file).read_text(encoding="utf-8")

    try:
        profile = company_profile(
            symbol, provider=args.provider, requested_as=requested_as, max_years=args.years
        )
    except FetchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NO_DATA

    output = (
        render_data_block(profile)
        if args.data_only
        else build_prompt(profile.ticker, render_data_block(profile), context)
    )

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"informe escrito en {args.out}")
        print("Pegalo en claude.ai para el análisis cualitativo (Capa 2).")
    else:
        print(output)
    return EXIT_OK


def run_precompute(args: argparse.Namespace) -> int:
    """Calcula el ranking del universo grande y lo deja versionado en el repo.

    Se corre a mano, local, con yfinance: FMP cobra 4 requests por ticker y el
    free tier son 250/día, así que precalcular ~100 tickers contra FMP gastaría
    la cuota entera. yfinance no tiene cuota y acá no hay IP de datacenter que
    lo bloquee.
    """
    from ..web.api import run_screen
    from ..web.precomputed import TOP_UNIVERSE, save, stamp

    tickers = TOP_UNIVERSE
    print(f"calculando {len(tickers)} tickers con {args.provider}… (tarda unos minutos)")

    payload = run_screen(
        tickers,
        metrics=DEFAULT_METRICS,
        method=Method.PERCENTILE,
        top_n=0,  # el front filtra el top-N en cliente
        use_cache=not args.no_cache,
        provider=args.provider,
    )
    stamp(payload, universe_size=len(tickers))

    meta = payload["meta"]
    if not meta["ok"]:
        print("\nno se pudo traer ningún fundamental; no se escribe nada.", file=sys.stderr)
        return EXIT_NO_DATA

    path = save(payload, Path(args.out) if args.out else None)
    print(f"\n{meta['ok']} ok · {meta['failed']} sin datos · {len(payload['sectors'])} sectores")
    for failure in payload["failures"]:
        print(f"   ⚠ {failure['ticker']}: {failure['reason']}")
    print(f"\nescrito en {path}")
    print("Commiteá el archivo: es lo que sirve la web sin gastar cuota del proveedor.")
    return EXIT_OK


def run_serve(args: argparse.Namespace) -> int:
    # Import diferido: `python -m bot screener` no tiene por qué cargar el
    # servidor, y el servidor importa de vuelta a este módulo (parse_metrics).
    from ..web.server import serve

    try:
        serve(
            host=args.host,
            port=args.port,
            open_browser=not args.no_browser,
            provider=args.provider,
        )
    except OSError as exc:
        print(f"no se pudo levantar en {args.host}:{args.port} — {exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


# --- parser ----------------------------------------------------------------


def _add_provider(parser: argparse.ArgumentParser) -> None:
    """`--provider` compartido. Default de `BOT_PROVIDER` o yfinance."""
    from ..config import default_provider
    from ..fetcher.service import PROVIDERS

    parser.add_argument(
        "--provider",
        choices=PROVIDERS,
        default=default_provider(),
        help="fuente de datos: yfinance (scrape, local) o fmp (API con key, para deploy). "
        "Default de BOT_PROVIDER.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bot",
        description="Screener de análisis fundamental con ranking por sector.",
    )
    parser.add_argument("--log-level", default="WARNING", help="DEBUG | INFO | WARNING | ERROR")

    sub = parser.add_subparsers(dest="command", required=True)
    screener = sub.add_parser("screener", help="rankea un universo de tickers por sector")

    universe = screener.add_argument_group("universo")
    universe.add_argument("--tickers", help="lista separada por comas: AAPL,GGAL.BA,MSFT")
    universe.add_argument(
        "--tickers-file", help="archivo con un ticker por línea (modo batch); '#' comenta"
    )

    cache = screener.add_argument_group("cache")
    cache.add_argument("--cache-path", help="ruta del SQLite (default: ~/.cache/fundamental-bot)")
    cache.add_argument(
        "--cache-ttl-hours", type=float, default=DEFAULT_TTL_HOURS, help="default: 24"
    )
    cache.add_argument("--no-cache", action="store_true", help="ignora el cache y refetchea todo")

    scoring = screener.add_argument_group("ranking")
    scoring.add_argument("--top-n", type=int, default=5, help="cuántos mostrar por sector")
    scoring.add_argument(
        "--method",
        default=Method.PERCENTILE.value,
        choices=[m.value for m in Method],
        help="percentile (robusto a outliers) o zscore (premia magnitud)",
    )
    scoring.add_argument(
        "--metrics",
        help="métricas a medida: 'roic:2,fcf_yield,debt_to_equity_trend:1:lower'",
    )
    scoring.add_argument("--min-peers", type=int, default=DEFAULT_MIN_PEERS)
    scoring.add_argument("--min-metrics", type=int, default=DEFAULT_MIN_METRICS)

    output = screener.add_argument_group("salida")
    output.add_argument("--json-out", help="escribe el resultado completo a un archivo JSON")

    _add_provider(screener)
    screener.set_defaults(func=run_screener)

    brief = sub.add_parser(
        "brief",
        help="informe de una empresa: prompt de análisis + datos ya calculados",
    )
    brief.add_argument("ticker", help="un solo ticker, ej. AAPL o GGAL.BA")
    brief.add_argument("--years", type=int, default=5, help="ejercicios a incluir (default: 5)")
    brief.add_argument("--out", help="escribe a un archivo en vez de stdout")
    brief.add_argument(
        "--data-only",
        action="store_true",
        help="sólo el bloque de datos, sin el prompt",
    )
    brief.add_argument(
        "--context-file",
        help="archivo con tu contexto de inversor, reemplaza el default",
    )
    _add_provider(brief)
    brief.set_defaults(func=run_brief)

    precompute = sub.add_parser(
        "precompute",
        help="calcula el ranking del universo grande y lo guarda para que la web lo sirva sin gastar cuota",
    )
    precompute.add_argument(
        "--out", help="destino del JSON (default: bot/web/data/top100.json)"
    )
    precompute.add_argument(
        "--no-cache", action="store_true", help="ignora el cache y refetchea todo"
    )
    _add_provider(precompute)
    precompute.set_defaults(func=run_precompute)

    serve = sub.add_parser("serve", help="levanta la web local del screener")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument(
        "--host",
        default="127.0.0.1",
        help="default: 127.0.0.1 (sólo esta máquina). Cambialo sabiendo lo que hacés.",
    )
    serve.add_argument("--no-browser", action="store_true", help="no abre el navegador solo")
    _add_provider(serve)
    serve.set_defaults(func=run_serve)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        parser = build_parser()
    except ValueError as exc:
        # p. ej. BOT_PROVIDER mal seteado: se lee al construir el parser. Mejor
        # un mensaje limpio que un traceback.
        print(f"error de configuración: {exc}", file=sys.stderr)
        return EXIT_USAGE
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.WARNING),
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrumpido", file=sys.stderr)
        return 130
