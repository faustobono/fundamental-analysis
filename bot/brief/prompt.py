"""Plantilla del prompt de la Capa 2.

Es el prompt de análisis, con una diferencia respecto de escribirlo a mano: como
la Capa 1 ya calculó los números, el prompt le prohíbe al modelo recalcularlos o
completarlos de memoria. Todo lo que puede afirmar tiene que estar en el bloque
de datos, y lo que falte tiene que declararlo faltante en vez de rellenarlo.
"""

from __future__ import annotations

from typing import Optional

DEFAULT_CONTEXT = """\
- Soy inversor de largo plazo (horizonte 20 años), perfil moderado.
- Mido todo en USD. Si es un CEDEAR argentino, aclarame el efecto FX.
- Priorizo calidad del negocio y valuación razonable sobre momentum."""

TEMPLATE = """\
Actuá como analista fundamental senior. Quiero que analices la empresa {ticker}
y me devuelvas un informe estructurado de análisis fundamental, no
recomendaciones de trading.

CONTEXTO:
{context}

REGLAS SOBRE LOS DATOS:
- Al final de este mensaje hay un bloque DATOS con métricas ya calculadas desde
  los estados contables. Usá esos números; no los recalcules ni los completes
  de memoria.
- Si una métrica figura como `null` o aparece en "LO QUE NO ESTÁ", decí
  explícitamente que falta y por qué importa. No la estimes salvo que te lo
  pida el punto correspondiente.
- Marcá con [ESTIMADO] todo lo que no salga del bloque DATOS: el WACC, el moat,
  los segmentos de negocio y cualquier contexto de industria.
- Los múltiplos están calculados sobre el último ejercicio anual, no sobre los
  últimos doce meses. No los compares contra un P/E "TTM" de otra fuente.

ANÁLISIS REQUERIDO (en este orden):

1. NEGOCIO
   - Qué hace la empresa y cómo gana plata (segmentos de ingreso)
   - Ventaja competitiva / moat (¿es defendible?)

2. RENTABILIDAD Y CALIDAD
   - ROIC actual y promedio de los años disponibles (tendencia)
   - Spread ROIC vs WACC (¿crea o destruye valor?)
     El bloque DATOS trae los componentes verificables del costo de capital
     (beta, costo de deuda, estructura, tasa efectiva) pero NO el WACC.
     Armalo vos declarando la tasa libre de riesgo y el equity risk premium que
     usás, marcá el resultado [ESTIMADO], y decime cuánto cambia la conclusión
     si el WACC se mueve ±2 puntos.
   - Márgenes: bruto, operativo, neto (tendencia)
   - ¿Los márgenes se expanden o comprimen? ¿Por qué?

3. CRECIMIENTO
   - Crecimiento de ingresos y EPS (histórico + proyectado)
   - ¿El crecimiento es rentable o quema caja?
   - Mirá el FCF neto de stock-based compensation, no sólo el FCF reportado.

4. SALUD FINANCIERA
   - Deuda neta / EBITDA, cobertura de intereses
   - Free cash flow (¿positivo y creciente?)
   - Días de caja / liquidez

5. VALUACIÓN
   - P/E, EV/EBITDA, EV/Revenue, FCF yield ACTUALES
   - vs su propia mediana histórica (está en el bloque DATOS, con percentil)
   - vs sus comparables del sector
   - ¿Está barata, cara o justa RESPECTO A SU CALIDAD?

6. RIESGOS
   - 3 riesgos concretos que podrían romper la tesis, anclados en los números
   - Si es CEDEAR/ADR argentino: riesgo país, FX y contable (ajuste por inflación)

7. VEREDICTO
   - Resumen en 3 líneas: calidad / valuación / principal riesgo
   - Rango de precio: barato / justo / caro
   - NO me digas "comprá" — dame los datos para que yo decida

FORMATO:
- Tablas para las métricas numéricas con tendencia temporal
- Números concretos, no generalidades
- Marcá con [ESTIMADO] cualquier dato que no puedas verificar
- Al final, listá las fuentes usadas y separá lo que salió del bloque DATOS de
  lo que aportaste vos

{data_block}
"""


def build_prompt(ticker: str, data_block: str, context: Optional[str] = None) -> str:
    return TEMPLATE.format(
        ticker=ticker,
        context=(context or DEFAULT_CONTEXT).strip(),
        data_block=data_block.strip(),
    )
