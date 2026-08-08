"""Múltiplos de valuación actuales y contra la propia historia de la empresa.

"¿Está cara o barata respecto de cómo cotizó los últimos 5 años?" es la
pregunta más útil del informe y la que ningún proveedor gratuito responde.
Se arma cruzando precios mensuales con los fundamentals del ejercicio que
**ya estaba publicado** en cada fecha.

Ese detalle es el que hace que el número signifique algo: un balance con cierre
en diciembre no es público hasta febrero o marzo. Usarlo para valuar enero es
sesgo de anticipación —look-ahead bias— y produce una historia de múltiplos que
nunca existió.

Todos los múltiplos se calculan sobre el **último ejercicio anual**, no sobre
los últimos doce meses. El P/E de acá y el `trailingPE` de yfinance no son la
misma cifra; la ventaja es que el actual y el histórico se calculan igual y por
lo tanto se pueden comparar entre sí.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Optional, Sequence

from .series import AnnualPeriod, FinancialHistory

#: Demora típica entre cierre de ejercicio y publicación del balance.
#: Sin esto se valúa con información que todavía no era pública.
REPORTING_LAG_DAYS = 90

#: Múltiplos que se siguen a lo largo del tiempo.
MULTIPLES = ("pe", "ev_ebitda", "ev_revenue", "fcf_yield", "pb", "ps")

#: En estos, más alto es más caro. `fcf_yield` va al revés.
HIGHER_IS_EXPENSIVE = {"pe": True, "ev_ebitda": True, "ev_revenue": True,
                       "pb": True, "ps": True, "fcf_yield": False}


@dataclass(frozen=True)
class Multiples:
    """Múltiplos en un momento dado, todos sobre el mismo ejercicio."""

    as_of: Optional[date] = None
    price: Optional[float] = None
    market_cap: Optional[float] = None
    enterprise_value: Optional[float] = None
    pe: Optional[float] = None
    ev_ebitda: Optional[float] = None
    ev_revenue: Optional[float] = None
    fcf_yield: Optional[float] = None
    pb: Optional[float] = None
    ps: Optional[float] = None

    def get(self, name: str) -> Optional[float]:
        return getattr(self, name, None)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["as_of"] = self.as_of.isoformat() if self.as_of else None
        return data


@dataclass(frozen=True)
class ValuationBand:
    """Dónde está el múltiplo de hoy dentro de su propia historia."""

    metric: str
    current: Optional[float]
    median: Optional[float]
    p25: Optional[float]
    p75: Optional[float]
    minimum: Optional[float]
    maximum: Optional[float]
    percentile: Optional[float]
    """En qué percentil de su historia cotiza hoy, en [0, 1]."""

    observations: int

    @property
    def verdict(self) -> Optional[str]:
        """'barato' / 'justo' / 'caro' respecto de su propia historia.

        Es una lectura relativa a la empresa, no al sector ni al mercado: una
        acción cara puede estar 'barata' contra su propia historia de burbuja.
        """
        if self.percentile is None or self.observations < 12:
            return None
        cheap_high = not HIGHER_IS_EXPENSIVE.get(self.metric, True)
        p = 1 - self.percentile if cheap_high else self.percentile
        if p <= 0.25:
            return "barato"
        if p >= 0.75:
            return "caro"
        return "justo"

    @property
    def vs_median(self) -> Optional[float]:
        """Desvío contra la mediana, en fracción. +0.30 = 30% por encima."""
        if self.current is None or not self.median:
            return None
        return (self.current - self.median) / abs(self.median)


def _quantile(sorted_values: Sequence[float], q: float) -> Optional[float]:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    low = int(pos)
    high = min(low + 1, len(sorted_values) - 1)
    weight = pos - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


def _percentile_of(values: Sequence[float], target: float) -> Optional[float]:
    if not values:
        return None
    below = sum(1 for v in values if v < target)
    equal = sum(1 for v in values if v == target)
    return (below + equal / 2) / len(values)


def compute_multiples(
    period: AnnualPeriod,
    price: float,
    shares: Optional[float],
    as_of: Optional[date] = None,
) -> Multiples:
    """Múltiplos de un ejercicio a un precio dado.

    `shares` se toma del ejercicio para que la historia refleje la dilución (o
    la recompra) real de cada momento, en vez de proyectar el conteo de hoy
    hacia atrás.
    """
    if not shares or shares <= 0 or price <= 0:
        return Multiples(as_of=as_of, price=price)

    market_cap = price * shares
    net_debt = period.net_debt
    enterprise_value = market_cap + net_debt if net_debt is not None else None

    def over_ev(value: Optional[float]) -> Optional[float]:
        if enterprise_value is None or not value or value <= 0:
            return None
        return enterprise_value / value

    def over_mcap(value: Optional[float]) -> Optional[float]:
        if not value or value <= 0:
            return None
        return market_cap / value

    return Multiples(
        as_of=as_of,
        price=price,
        market_cap=market_cap,
        enterprise_value=enterprise_value,
        # Un P/E con resultado negativo no es "barato": no es un múltiplo.
        pe=over_mcap(period.net_income),
        ev_ebitda=over_ev(period.ebitda),
        ev_revenue=over_ev(period.revenue),
        fcf_yield=(period.free_cash_flow / market_cap) if period.free_cash_flow is not None else None,
        pb=over_mcap(period.total_equity),
        ps=over_mcap(period.revenue),
    )


def period_public_at(
    history: FinancialHistory,
    at: date,
    lag_days: int = REPORTING_LAG_DAYS,
) -> Optional[AnnualPeriod]:
    """El ejercicio más reciente que ya era público en `at`."""
    candidates = [
        p
        for p in history.periods
        if p.period_end is not None and p.period_end + timedelta(days=lag_days) <= at
    ]
    return max(candidates, key=lambda p: p.period_end) if candidates else None


def multiples_history(
    history: FinancialHistory,
    prices: Sequence[tuple[date, float]],
    lag_days: int = REPORTING_LAG_DAYS,
) -> list[Multiples]:
    """Serie de múltiplos, un punto por precio, usando datos ya publicados."""
    out: list[Multiples] = []
    for at, price in prices:
        period = period_public_at(history, at, lag_days)
        if period is None:
            continue
        out.append(compute_multiples(period, price, period.shares_outstanding, as_of=at))
    return out


def band(metric: str, series: Sequence[Multiples], current: Optional[float]) -> ValuationBand:
    """Distribución histórica de un múltiplo y dónde cae el valor de hoy."""
    values = sorted(v for v in (m.get(metric) for m in series) if v is not None)
    return ValuationBand(
        metric=metric,
        current=current,
        median=_quantile(values, 0.5),
        p25=_quantile(values, 0.25),
        p75=_quantile(values, 0.75),
        minimum=values[0] if values else None,
        maximum=values[-1] if values else None,
        percentile=_percentile_of(values, current) if current is not None else None,
        observations=len(values),
    )


@dataclass(frozen=True)
class ValuationProfile:
    current: Multiples
    bands: dict[str, ValuationBand]
    observations: int
    lag_days: int = REPORTING_LAG_DAYS

    def to_dict(self) -> dict:
        return {
            "actual": self.current.to_dict(),
            "vs_historia": {
                name: {
                    "actual": b.current,
                    "mediana_5a": b.median,
                    "p25": b.p25,
                    "p75": b.p75,
                    "min": b.minimum,
                    "max": b.maximum,
                    "percentil_en_su_historia": b.percentile,
                    "vs_mediana": b.vs_median,
                    "veredicto": b.verdict,
                    "observaciones": b.observations,
                }
                for name, b in self.bands.items()
            },
            "observaciones_de_precio": self.observations,
            "dias_de_rezago_de_publicacion": self.lag_days,
        }


def build_valuation(
    history: FinancialHistory,
    prices: Sequence[tuple[date, float]],
    current_price: Optional[float],
    current_shares: Optional[float],
    lag_days: int = REPORTING_LAG_DAYS,
) -> Optional[ValuationProfile]:
    """Múltiplos de hoy y su lugar en la distribución histórica."""
    latest = history.latest
    if latest is None or not current_price:
        return None

    shares = current_shares or latest.shares_outstanding
    current = compute_multiples(latest, current_price, shares, as_of=date.today())
    series = multiples_history(history, prices, lag_days)

    return ValuationProfile(
        current=current,
        bands={name: band(name, series, current.get(name)) for name in MULTIPLES},
        observations=len(series),
        lag_days=lag_days,
    )
