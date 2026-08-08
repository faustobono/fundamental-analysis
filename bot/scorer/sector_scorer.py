"""Ranking del universo, siempre dentro de cada sector.

Regla no negociable: no se compara cross-sector. Un banco apalancado 10x no es
"peor" que una software con caja neta; en banca el apalancamiento *es* el
negocio. Los mismos ratios significan cosas distintas según el sector, así que
cada empresa se puntúa contra sus peers y nunca contra el universo entero.

Faltantes: una métrica ausente no se rellena con 0 ni con la mediana. Se excluye
y los pesos se renormalizan sobre lo que sí hay, de modo que a una empresa nunca
la castiga (ni la premia) el silencio del proveedor. Si queda con muy poca
cobertura, no se rankea: se reporta aparte.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from ..models import FundamentalSnapshot
from .metrics import (
    DEFAULT_METRICS,
    Method,
    MetricSpec,
    percentile_ranks,
    winsorize,
    zscores,
)

logger = logging.getLogger(__name__)

#: Debajo de esto, un percentil sectorial es ruido: con dos empresas, una es
#: siempre el percentil 0 y la otra el 100.
DEFAULT_MIN_PEERS = 3

#: Mínimo de métricas con dato para que un puntaje signifique algo.
DEFAULT_MIN_METRICS = 2

#: Recorte de extremos antes del z-score (5% de cada cola).
DEFAULT_WINSORIZE = 0.05


@dataclass(frozen=True)
class MetricScore:
    """Puntaje de una empresa en una métrica, dentro de su sector."""

    name: str
    label: str
    raw: float
    score: float
    """Percentil en [0, 1], o z-score, según el método."""

    peers: int
    """Cuántas empresas del sector tenían dato en esta métrica."""

    higher_is_better: bool


@dataclass(frozen=True)
class TickerScore:
    snapshot: FundamentalSnapshot
    composite: float
    metrics: dict[str, MetricScore]
    missing: tuple[str, ...]
    rank: int = 0
    peers: int = 0

    @property
    def ticker(self) -> str:
        return self.snapshot.ticker

    @property
    def sector(self) -> str:
        return self.snapshot.sector or "Unknown"

    @property
    def coverage(self) -> float:
        """Fracción del peso total que efectivamente se pudo evaluar."""
        total = len(self.metrics) + len(self.missing)
        return len(self.metrics) / total if total else 0.0


@dataclass(frozen=True)
class SectorRanking:
    sector: str
    ranked: tuple[TickerScore, ...]
    unrankable: tuple[tuple[FundamentalSnapshot, str], ...] = ()
    thin: bool = False
    """True si el sector tiene menos peers que el mínimo: leer con pinzas."""

    @property
    def peer_count(self) -> int:
        return len(self.ranked)

    def top(self, n: int) -> tuple[TickerScore, ...]:
        return self.ranked[:n] if n > 0 else self.ranked


@dataclass
class ScoringResult:
    sectors: dict[str, SectorRanking] = field(default_factory=dict)

    def top_n(self, n: int) -> list[TickerScore]:
        """Top-N de cada sector, aplanado. Ordenado por sector y luego posición."""
        out: list[TickerScore] = []
        for sector in sorted(self.sectors):
            out.extend(self.sectors[sector].top(n))
        return out

    @property
    def all_scores(self) -> list[TickerScore]:
        return [s for sector in sorted(self.sectors) for s in self.sectors[sector].ranked]

    @property
    def unrankable(self) -> list[tuple[FundamentalSnapshot, str]]:
        return [item for sector in sorted(self.sectors) for item in self.sectors[sector].unrankable]


class SectorScorer:
    def __init__(
        self,
        metrics: Sequence[MetricSpec] = DEFAULT_METRICS,
        method: Method = Method.PERCENTILE,
        *,
        min_peers: int = DEFAULT_MIN_PEERS,
        min_metrics: int = DEFAULT_MIN_METRICS,
        winsorize_limit: float = DEFAULT_WINSORIZE,
    ):
        if not metrics:
            raise ValueError("hace falta al menos una métrica")
        self.metrics = tuple(metrics)
        self.method = Method(method)
        self.min_peers = min_peers
        self.min_metrics = min_metrics
        self.winsorize_limit = winsorize_limit

    # --- API ---------------------------------------------------------------

    def score(self, snapshots: Iterable[FundamentalSnapshot]) -> ScoringResult:
        by_sector: dict[str, list[FundamentalSnapshot]] = {}
        for snapshot in snapshots:
            by_sector.setdefault(snapshot.sector or "Unknown", []).append(snapshot)

        result = ScoringResult()
        for sector, members in by_sector.items():
            result.sectors[sector] = self._score_sector(sector, members)
        return result

    # --- interno -----------------------------------------------------------

    def _score_sector(self, sector: str, members: list[FundamentalSnapshot]) -> SectorRanking:
        # Orden estable de entrada: dos corridas con el mismo universo tienen que
        # producir exactamente el mismo ranking, incluso en los empates.
        members = sorted(members, key=lambda s: s.ticker)

        # Paso 1: puntuar cada métrica por separado, sólo entre quienes tienen dato.
        scores_by_metric: dict[str, dict[str, MetricScore]] = {}
        for spec in self.metrics:
            present = [(s.ticker, s.metric(spec.name)) for s in members]
            present = [(t, v) for t, v in present if v is not None]
            if not present:
                logger.debug("%s: ninguna empresa reporta %s", sector, spec.name)
                continue
            tickers = [t for t, _ in present]
            values = [v for _, v in present]
            scores_by_metric[spec.name] = {
                ticker: MetricScore(
                    name=spec.name,
                    label=spec.display,
                    raw=raw,
                    score=score,
                    peers=len(values),
                    higher_is_better=spec.higher_is_better,
                )
                for ticker, raw, score in zip(tickers, values, self._transform(values, spec))
            }

        # Paso 2: combinar en un compuesto, renormalizando por lo disponible.
        ranked: list[TickerScore] = []
        unrankable: list[tuple[FundamentalSnapshot, str]] = []

        for snapshot in members:
            available: dict[str, MetricScore] = {}
            missing: list[str] = []
            for spec in self.metrics:
                metric_score = scores_by_metric.get(spec.name, {}).get(snapshot.ticker)
                if metric_score is None:
                    missing.append(spec.name)
                else:
                    available[spec.name] = metric_score

            if len(available) < self.min_metrics:
                reason = (
                    f"sólo {len(available)} de {len(self.metrics)} métricas con dato "
                    f"(mínimo {self.min_metrics}): {', '.join(missing)}"
                )
                unrankable.append((snapshot, reason))
                continue

            weights = {spec.name: spec.weight for spec in self.metrics if spec.name in available}
            total_weight = sum(weights.values())
            composite = sum(available[name].score * w for name, w in weights.items()) / total_weight

            ranked.append(
                TickerScore(
                    snapshot=snapshot,
                    composite=composite,
                    metrics=available,
                    missing=tuple(missing),
                )
            )

        # Paso 3: ordenar. Ante empate, alfabético, para que el orden sea estable.
        ranked.sort(key=lambda s: (-s.composite, s.ticker))
        ranked = [
            TickerScore(
                snapshot=s.snapshot,
                composite=s.composite,
                metrics=s.metrics,
                missing=s.missing,
                rank=i + 1,
                peers=len(ranked),
            )
            for i, s in enumerate(ranked)
        ]

        thin = len(ranked) < self.min_peers
        if thin and ranked:
            logger.warning(
                "sector %s: sólo %d empresa(s) rankeable(s); el percentil sectorial no es significativo",
                sector,
                len(ranked),
            )

        return SectorRanking(
            sector=sector,
            ranked=tuple(ranked),
            unrankable=tuple(unrankable),
            thin=thin,
        )

    def _transform(self, values: Sequence[float], spec: MetricSpec) -> list[float]:
        """Valores crudos -> puntajes comparables, ya orientados a 'más es mejor'."""
        oriented = list(values) if spec.higher_is_better else [-v for v in values]
        if self.method is Method.PERCENTILE:
            # El percentil ya es robusto a outliers; winsorizar no cambiaría el
            # orden y sólo agregaría empates artificiales en las colas.
            return percentile_ranks(oriented)
        return zscores(winsorize(oriented, self.winsorize_limit))


def describe_metric(score: MetricScore, method: Method) -> str:
    """Texto corto de un puntaje, para CLI y para el prompt del LLM."""
    direction = "↑" if score.higher_is_better else "↓"
    if method is Method.PERCENTILE:
        return f"{score.label} {direction}: {score.raw:.4g} (percentil {score.score:.0%} de {score.peers})"
    return f"{score.label} {direction}: {score.raw:.4g} (z={score.score:+.2f} sobre {score.peers})"
