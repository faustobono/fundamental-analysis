"""Puntajes de riesgo y calidad financiera: Altman Z-Score y Piotroski F-Score.

Dos modelos clásicos que un trader que hace fundamental analysis espera
encontrar y que este bot no calculaba: uno mide riesgo de quiebra (Altman), el
otro calidad/fortaleza fundamental año contra año (Piotroski). Ambos se arman
acá y no en `series.py` porque el Altman necesita el market cap *actual* —algo
que no vive en `AnnualPeriod`, que es sólo líneas contables— y el Piotroski
compara dos ejercicios, no describe uno solo.

Mismo criterio que el resto de la Capa 1: si falta un insumo, el resultado (o
el criterio puntual) queda en `None`, no se estima ni se imputa.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .series import AnnualPeriod


def _div(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or not denominator:
        return None
    return numerator / denominator

# --- Altman Z-Score -----------------------------------------------------------
#
# Modelo original (empresas públicas, 1968): combina liquidez, rentabilidad
# acumulada, rentabilidad operativa, apalancamiento a valor de mercado y
# eficiencia de activos en un solo número. No sustituye un análisis de
# crédito, es una señal de alerta temprana.

ALTMAN_SAFE = 2.99
ALTMAN_DISTRESS = 1.81


@dataclass(frozen=True)
class AltmanResult:
    z_score: float
    zone: str
    """'segura', 'gris' o 'riesgo'."""


def altman_z_score(period: AnnualPeriod, market_cap: Optional[float]) -> Optional[AltmanResult]:
    """Z = 1.2·X1 + 1.4·X2 + 3.3·X3 + 0.6·X4 + 1.0·X5.

    X1 capital de trabajo / activos, X2 resultados acumulados / activos,
    X3 EBIT / activos, X4 market cap / pasivo total, X5 ingresos / activos.
    Con cualquier insumo faltante (o activos/pasivo total ≤ 0, donde el ratio
    no tiene lectura) se devuelve `None` en vez de un número a medias.
    """
    assets = period.total_assets
    if not assets or assets <= 0:
        return None
    if (
        period.current_assets is None
        or period.current_liabilities is None
        or period.retained_earnings is None
        or period.ebit is None
        or period.revenue is None
        or period.total_equity is None
        or market_cap is None
    ):
        return None

    total_liabilities = assets - period.total_equity
    if total_liabilities <= 0:
        return None

    working_capital = period.current_assets - period.current_liabilities
    x1 = working_capital / assets
    x2 = period.retained_earnings / assets
    x3 = period.ebit / assets
    x4 = market_cap / total_liabilities
    x5 = period.revenue / assets

    z = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5
    if z > ALTMAN_SAFE:
        zone = "segura"
    elif z < ALTMAN_DISTRESS:
        zone = "riesgo"
    else:
        zone = "gris"
    return AltmanResult(z_score=z, zone=zone)


# --- Piotroski F-Score ---------------------------------------------------------
#
# 9 criterios binarios (Piotroski, 2000) que comparan el ejercicio actual
# contra el anterior: rentabilidad, calidad de la ganancia, apalancamiento,
# liquidez, dilución y eficiencia. A diferencia del Altman no necesita precio
# de mercado — es puramente contable.

_CRITERIA_LABELS: dict[str, str] = {
    "roa_positivo": "ROA positivo",
    "cfo_positivo": "Flujo de caja operativo positivo",
    "roa_mejora": "ROA mejora vs. el ejercicio anterior",
    "cfo_mayor_a_utilidad": "CFO mayor a la ganancia neta (calidad del resultado)",
    "apalancamiento_baja": "Apalancamiento (deuda/activos) baja",
    "liquidez_mejora": "Liquidez corriente mejora",
    "sin_nuevas_acciones": "No se emitieron acciones nuevas",
    "margen_bruto_mejora": "Margen bruto mejora",
    "rotacion_activos_mejora": "Rotación de activos mejora",
}


@dataclass(frozen=True)
class PiotroskiCriterion:
    name: str
    label: str
    passed: Optional[bool]
    """`None` cuando falta el dato para evaluar este criterio puntual —no se
    fuerza a `False`, porque eso penalizaría a la empresa por un hueco del
    proveedor, no por su fundamental."""


@dataclass(frozen=True)
class PiotroskiResult:
    score: int
    """Criterios cumplidos."""
    max_score: int
    """Criterios que se pudieron evaluar (≤ 9). Con datos completos es 9."""
    criteria: tuple[PiotroskiCriterion, ...]


def _leverage(period: AnnualPeriod) -> Optional[float]:
    return _div(period.total_debt, period.total_assets)


def piotroski_f_score(current: AnnualPeriod, prior: Optional[AnnualPeriod]) -> Optional[PiotroskiResult]:
    """Compara `current` contra `prior` (el ejercicio inmediatamente anterior).

    `None` si no hay ejercicio previo contra el cual comparar: el modelo es
    inherentemente año-contra-año, no tiene lectura para un solo período.
    """
    if prior is None:
        return None

    roa_now, roa_prev = current.roa, prior.roa
    lev_now, lev_prev = _leverage(current), _leverage(prior)
    cr_now, cr_prev = current.current_ratio, prior.current_ratio
    gm_now, gm_prev = current.gross_margin, prior.gross_margin
    at_now, at_prev = current.asset_turnover, prior.asset_turnover

    # Un `None` puntual significa "no se pudo evaluar este criterio" (falta el
    # dato), no "no cumple" — ver `PiotroskiCriterion.passed`.
    passed: dict[str, Optional[bool]] = {
        "roa_positivo": None if roa_now is None else roa_now > 0,
        "cfo_positivo": None if current.operating_cash_flow is None else current.operating_cash_flow > 0,
        "roa_mejora": None if roa_now is None or roa_prev is None else roa_now > roa_prev,
        "cfo_mayor_a_utilidad": (
            None
            if current.operating_cash_flow is None or current.net_income is None
            else current.operating_cash_flow > current.net_income
        ),
        "apalancamiento_baja": None if lev_now is None or lev_prev is None else lev_now < lev_prev,
        "liquidez_mejora": None if cr_now is None or cr_prev is None else cr_now > cr_prev,
        "sin_nuevas_acciones": (
            None
            if current.shares_outstanding is None or prior.shares_outstanding is None
            else current.shares_outstanding <= prior.shares_outstanding
        ),
        "margen_bruto_mejora": None if gm_now is None or gm_prev is None else gm_now > gm_prev,
        "rotacion_activos_mejora": None if at_now is None or at_prev is None else at_now > at_prev,
    }

    criteria = tuple(
        PiotroskiCriterion(name=name, label=label, passed=passed[name])
        for name, label in _CRITERIA_LABELS.items()
    )
    evaluated = [c for c in criteria if c.passed is not None]
    if not evaluated:
        return None
    return PiotroskiResult(
        score=sum(1 for c in evaluated if c.passed),
        max_score=len(evaluated),
        criteria=criteria,
    )
