"""Ranking sectorial: percentil o z-score sobre peers del mismo sector."""

from .metrics import DEFAULT_METRICS, Method, MetricSpec
from .sector_scorer import (
    MetricScore,
    ScoringResult,
    SectorRanking,
    SectorScorer,
    TickerScore,
    describe_metric,
)

__all__ = [
    "SectorScorer",
    "ScoringResult",
    "SectorRanking",
    "TickerScore",
    "MetricScore",
    "MetricSpec",
    "Method",
    "DEFAULT_METRICS",
    "describe_metric",
]
