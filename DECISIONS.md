# Decisiones de diseño

## Proveedores intercambiables

El pipeline usa una interfaz común para que `yfinance` y FMP sean intercambiables.
`yfinance` es el default local; FMP es el proveedor previsto para hosting porque usa
una API con key y evita el bloqueo de IPs de datacenter.

## Ratios propios

Los ratios se calculan desde estados contables, precio y sector. No se usan ratios
precalculados del proveedor cuando se pueden derivar de líneas crudas. Esto mantiene
las unidades y las reglas de calidad bajo control del bot.

## Datos faltantes

Un dato faltante queda como `None`, se informa como warning o gap y no se imputa con
cero o mediana. En el ranking, los pesos se renormalizan sobre las métricas existentes.

## Ranking intra-sector

Las empresas se comparan contra peers del mismo sector. Una empresa puede aparecer
como no rankeable si no alcanza el mínimo de peers o métricas. Las advertencias de
pocos peers son intencionales.

## Monedas

Los ratios que mezclan mercado y balance no se calculan cuando las monedas difieren.
Los ratios de una sola punta pueden seguir siendo válidos. No se hardcodea un tipo de
cambio: la conversión requiere un `FxProvider` explícito.

## WACC

El bot no calcula WACC automáticamente. La tasa libre de riesgo y el equity risk
premium son supuestos de mercado y deben declararse fuera de la Capa 1, marcando el
resultado como estimado.

## Testing

Los tests no usan red. FMP recibe un cliente HTTP inyectable y yfinance recibe una
fábrica de tickers inyectable. Los smoke-tests contra la API real son manuales.

## Dependencias

FMP usa `urllib` de la stdlib para no agregar otra dependencia al proyecto. La UI usa
el servidor HTTP de la stdlib y no incorpora un framework web.

## Puntajes de riesgo y calidad (Altman Z-Score / Piotroski F-Score)

Se agregaron a `bot/analysis/scores.py` porque son modelos clásicos que un
trader espera ver junto al resto del análisis fundamental, y ambos son
verificables desde datos contables + precio (no opinión, no estimación
propia del bot). El Altman necesita el market cap *actual*, así que sólo se
calcula para el último ejercicio, no como serie histórica. El Piotroski
compara el último ejercicio contra el anterior; si a un criterio puntual le
falta el dato, ese criterio queda en `None` y se excluye del puntaje máximo
en vez de contar como no cumplido — mismo criterio de "no imputar" que el
resto del bot. Ninguno de los dos reemplaza un DCF ni el WACC: siguen sin
calcularse automáticamente, por el mismo motivo de siempre (supuestos de
mercado).

## Subagentes de Claude Code

`.claude/agents/` guarda definiciones de subagentes específicos para este
repo (por ejemplo `code-optimizer.md`, para pases de optimización/refactor
acotados que no deben tocar el comportamiento observable). Quedan
versionados junto con el resto del contexto compartido: cualquier agente que
retome la sesión los puede invocar o reutilizar su brief.

## Hosting

El destino de despliegue elegido es Vercel. La app conserva su handler de
`BaseHTTPRequestHandler` detrás de `api/index.py`; el runtime de Vercel lo admite
sin introducir un framework web. En producción debe usarse FMP, porque yfinance
puede bloquear las IPs de datacenter.

El cache SQLite se ubica en `/tmp` cuando detecta Vercel. Es un acelerador
efímero: no se asume persistencia entre invocaciones ni se usa como fuente de
verdad.

El proyecto de Vercel se renombró de `fundamental-analysis` a `fundscan`
(dominio público: `fundscan.vercel.app`) porque el nombre anterior generaba
un alias largo (`fundamental-analysis-eight.vercel.app`, con el sufijo que
Vercel agrega cuando el nombre base ya estaba tomado). `fundscan` evoca
"fundamental scan/screener", es corto y estaba libre.
