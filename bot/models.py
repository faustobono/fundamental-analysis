"""Modelo de datos común de la capa fundamental.

Todo lo que sale de un adapter (yfinance, BYMA, o el que venga) se expresa como
un `FundamentalSnapshot`. El resto del pipeline (normalizer, scorer) no conoce el
origen del dato.

Convención de unidades, respetada por todo el pipeline:
  - ratios y márgenes en fracción (0.15 == 15%), nunca en porcentaje;
  - montos absolutos en la moneda de `currency`, en unidades enteras (no miles
    ni millones);
  - `debt_to_equity` como veces (1.5 == 150% de deuda sobre patrimonio).
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, Optional

SCHEMA_VERSION = 1

#: Métricas que el scorer sabe rankear. Se definen acá porque son parte del
#: contrato del modelo, no del scorer.
RATIO_FIELDS = (
    "pe",
    "pb",
    "roe",
    "roic",
    "debt_to_equity",
    "fcf_yield",
    "revenue_growth_yoy",
    "gross_margin",
    "net_margin",
    "dividend_yield",
)

#: Campos monetarios que vienen de los estados contables: están en `currency`.
STATEMENT_MONETARY_FIELDS = (
    "revenue",
    "ebit",
    "free_cash_flow",
    "total_debt",
    "total_equity",
)

#: Campos monetarios que vienen del mercado: están en `quote_currency`.
#: No siempre coinciden con los anteriores. Un ADR argentino cotiza en USD pero
#: reporta el balance en ARS, y mezclar las dos puntas da ratios sin sentido.
QUOTE_MONETARY_FIELDS = (
    "market_cap",
    "price",
)

#: Todos los montos absolutos: los únicos afectados por conversión de moneda.
MONETARY_FIELDS = STATEMENT_MONETARY_FIELDS + QUOTE_MONETARY_FIELDS


class FetchError(Exception):
    """Error recuperable de un adapter: el batch loguea y sigue."""

    def __init__(self, ticker: str, message: str):
        self.ticker = ticker
        super().__init__(f"[{ticker}] {message}")


class NoDataError(FetchError):
    """El proveedor respondió pero no hay fundamentals utilizables."""


class UpstreamError(FetchError):
    """El proveedor falló (red, rate limit, respuesta corrupta)."""


class UnmappedTickerError(FetchError):
    """Ticker .BA sin subyacente conocido en el mapping de CEDEARs."""


def _clean(value: Any) -> Optional[float]:
    """Convierte a float o None. Descarta NaN/inf, que yfinance devuelve seguido."""
    if value is None:
        return None
    if isinstance(value, bool):  # bool es int en Python; nunca es un ratio válido
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


@dataclass(frozen=True)
class FundamentalSnapshot:
    """Foto de los fundamentals de una empresa en un momento dado.

    Todos los ratios son `Optional`: un proveedor que no reporta una métrica es
    el caso normal, no un error. El scorer decide qué hacer con los faltantes.
    """

    # --- identidad ---------------------------------------------------------
    ticker: str
    """Ticker tal como lo pidió el usuario (ej. 'GGAL.BA')."""

    source_ticker: str
    """Ticker efectivamente consultado (ej. 'GGAL'). Difiere en CEDEARs."""

    source: str
    """Adapter que produjo el snapshot: 'yfinance' | 'byma'."""

    as_of: datetime
    """Momento del fetch (UTC), no la fecha del balance."""

    # --- clasificación -----------------------------------------------------
    sector: Optional[str] = None
    industry: Optional[str] = None
    company_name: Optional[str] = None
    currency: Optional[str] = None
    """Moneda en la que la empresa reporta sus estados contables."""

    quote_currency: Optional[str] = None
    """Moneda en la que cotiza la acción. Puede diferir de `currency`."""

    # --- valuación ---------------------------------------------------------
    pe: Optional[float] = None
    pb: Optional[float] = None
    fcf_yield: Optional[float] = None
    dividend_yield: Optional[float] = None

    # --- rentabilidad ------------------------------------------------------
    roe: Optional[float] = None
    roic: Optional[float] = None
    gross_margin: Optional[float] = None
    net_margin: Optional[float] = None

    # --- solidez y crecimiento --------------------------------------------
    debt_to_equity: Optional[float] = None
    debt_to_equity_prev: Optional[float] = None
    """D/E del ejercicio anterior, para derivar tendencia de apalancamiento."""

    revenue_growth_yoy: Optional[float] = None

    # --- insumos crudos ----------------------------------------------------
    # Se guardan para auditar los ratios derivados y para que el LLM tenga
    # contexto de escala (un ROIC de 30% no significa lo mismo con revenue de
    # 10M que de 10B).
    market_cap: Optional[float] = None
    price: Optional[float] = None
    revenue: Optional[float] = None
    ebit: Optional[float] = None
    free_cash_flow: Optional[float] = None
    total_debt: Optional[float] = None
    total_equity: Optional[float] = None
    effective_tax_rate: Optional[float] = None

    # --- trazabilidad ------------------------------------------------------
    warnings: tuple[str, ...] = field(default_factory=tuple)
    """Notas de calidad de dato acumuladas por adapter y normalizer."""

    def __post_init__(self) -> None:
        # Los frozen dataclass permiten cualquier cosa en el constructor; acá
        # forzamos el contrato de tipos para que el resto del pipeline no tenga
        # que defenderse de strings o NaN venidos del proveedor.
        for f in fields(self):
            if f.name in ("ticker", "source_ticker", "source", "as_of", "warnings"):
                continue
            if f.name in ("sector", "industry", "company_name", "currency", "quote_currency"):
                value = getattr(self, f.name)
                object.__setattr__(self, f.name, value or None)
                continue
            object.__setattr__(self, f.name, _clean(getattr(self, f.name)))
        object.__setattr__(self, "warnings", tuple(self.warnings))

    # --- helpers -----------------------------------------------------------

    @property
    def debt_to_equity_trend(self) -> Optional[float]:
        """Variación de D/E vs. el ejercicio previo.

        Negativo == desapalancamiento. `None` si falta alguna de las dos puntas.
        """
        if self.debt_to_equity is None or self.debt_to_equity_prev is None:
            return None
        return self.debt_to_equity - self.debt_to_equity_prev

    @property
    def is_cedear(self) -> bool:
        return self.ticker != self.source_ticker

    @property
    def has_currency_mismatch(self) -> bool:
        """True si el balance y la cotización están en monedas distintas.

        Cuando pasa, cualquier ratio que cruce mercado con balance (FCF yield,
        market cap / patrimonio) está mal por un factor igual al tipo de cambio.
        """
        if not self.currency or not self.quote_currency:
            return False
        return self.currency != self.quote_currency

    def metric(self, name: str) -> Optional[float]:
        """Acceso uniforme a métricas, incluidas las derivadas por property."""
        if name == "debt_to_equity_trend":
            return self.debt_to_equity_trend
        if not hasattr(self, name):
            raise KeyError(f"métrica desconocida: {name}")
        return getattr(self, name)

    def with_warning(self, message: str) -> "FundamentalSnapshot":
        if message in self.warnings:
            return self
        return self.replace(warnings=self.warnings + (message,))

    def replace(self, **changes: Any) -> "FundamentalSnapshot":
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        data.update(changes)
        return FundamentalSnapshot(**data)

    # --- serialización (cache SQLite / export JSON) ------------------------

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["as_of"] = self.as_of.astimezone(timezone.utc).isoformat()
        data["warnings"] = list(self.warnings)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FundamentalSnapshot":
        data = dict(data)
        as_of = data.get("as_of")
        if isinstance(as_of, str):
            data["as_of"] = datetime.fromisoformat(as_of)
        known = {f.name for f in fields(cls)}
        # Ignoramos claves desconocidas: un cache viejo con campos de más no
        # debería romper el arranque.
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, payload: str) -> "FundamentalSnapshot":
        return cls.from_dict(json.loads(payload))
