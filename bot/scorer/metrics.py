"""Definición de métricas y estadística de apoyo para el ranking sectorial.

Estadística en Python puro (sin numpy): los universos son de decenas de tickers,
no de millones, y a cambio el cálculo queda explícito y fácil de testear.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence


class Method(str, Enum):
    """Cómo se convierte un valor crudo en puntaje comparable."""

    PERCENTILE = "percentile"
    """Posición relativa dentro del sector, en [0, 1]. Robusto a outliers."""

    ZSCORE = "zscore"
    """Desvíos respecto de la media del sector. Premia magnitud, no sólo orden."""


@dataclass(frozen=True)
class MetricSpec:
    """Una métrica del ranking y cómo interpretarla."""

    name: str
    """Nombre del campo en `FundamentalSnapshot` (incluye derivados)."""

    weight: float = 1.0
    higher_is_better: bool = True
    label: Optional[str] = None

    def __post_init__(self) -> None:
        if self.weight <= 0:
            raise ValueError(f"{self.name}: el peso debe ser positivo")

    @property
    def display(self) -> str:
        return self.label or self.name


#: Criterio por defecto: barato en relación a la caja que genera, que rinda
#: sobre el capital que emplea, que no se esté apalancando, y que crezca.
DEFAULT_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("fcf_yield", weight=1.0, higher_is_better=True, label="FCF yield"),
    MetricSpec("roic", weight=1.0, higher_is_better=True, label="ROIC"),
    MetricSpec(
        "debt_to_equity_trend",
        weight=0.75,
        higher_is_better=False,
        label="Tendencia deuda/patrimonio",
    ),
    MetricSpec(
        "revenue_growth_yoy",
        weight=0.75,
        higher_is_better=True,
        label="Crecimiento de ingresos YoY",
    ),
)


# --- estadística ------------------------------------------------------------


def quantile(sorted_values: Sequence[float], q: float) -> float:
    """Cuantil por interpolación lineal. `sorted_values` viene ordenado."""
    if not sorted_values:
        raise ValueError("secuencia vacía")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = q * (len(sorted_values) - 1)
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    weight = position - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


def winsorize(values: Sequence[float], limit: float) -> list[float]:
    """Recorta los extremos al percentil `limit` / `1 - limit`.

    Sin esto, una sola empresa con ROIC de 300% (típicamente un artefacto
    contable, no una ventaja competitiva) aplasta el z-score de todo el sector y
    el ranking pasa a decir "ésta y todas las demás".
    """
    if limit <= 0 or len(values) < 3:
        return list(values)
    ordered = sorted(values)
    low = quantile(ordered, limit)
    high = quantile(ordered, 1 - limit)
    return [min(max(v, low), high) for v in values]


def percentile_ranks(values: Sequence[float]) -> list[float]:
    """Percentil de cada valor dentro de la muestra, en [0, 1].

    Los empates comparten el promedio de sus posiciones, así que dos empresas
    con el mismo ROIC reciben el mismo puntaje. Con un solo elemento devuelve
    0.5: no hay información relativa, y un 1.0 diría "el mejor del sector"
    cuando en realidad es el único.
    """
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [0.5]

    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        average_rank = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = average_rank / (n - 1)
        i = j + 1
    return ranks


def zscores(values: Sequence[float]) -> list[float]:
    """Z-scores de la muestra. Si no hay dispersión, todos empatan en 0."""
    n = len(values)
    if n == 0:
        return []
    mean = sum(values) / n
    if n == 1:
        return [0.0]
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    if variance <= 0:
        return [0.0] * n
    std = variance**0.5
    return [(v - mean) / std for v in values]
