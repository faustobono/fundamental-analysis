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

## CEDEARs/ADRs: el `.BA` nunca cambia un número

`resolve_symbol()` resuelve el ticker local (`.BA`) al subyacente en EE.UU.
*antes* de traer cualquier dato — precio incluido, no sólo los estados
contables. Por eso el análisis de un CEDEAR mapeado (ej. `PAMP.BA`) y el de
su subyacente (`PAM`) son idénticos número por número: la única diferencia es
la etiqueta y el warning "es un CEDEAR". El bot nunca usa el precio ni el
ratio de conversión de la cotización local en BYMA — el objetivo es siempre
el fundamental de la empresa, no el instrumento local. El `.BA` importa por
otro motivo: activa la búsqueda en `cedear_map.json`, que hace falta cuando
el ticker local no coincide con el del subyacente (la mayoría de los casos,
ej. `PAMP` → `PAM`); sin él, esos tickers no se encuentran.

Los CEDEARs exigían plan pago en FMP (`HTTP 402` para sus estados contables
en el free tier, aunque coticen en NASDAQ/NYSE) — confirmado que no era un
problema del pipeline, local con yfinance funcionaban bien. Se agregó un
fallback automático a yfinance cuando FMP devuelve 402 (`bot/fetcher/
service.py::_FmpWithYfinanceFallback` para el screener, la misma lógica en
`bot/analysis/profile.py::company_profile()` para el brief), declarado como
warning en vez de silencioso. Otros errores de FMP (429, 401, etc.) NO caen
al fallback — sólo 402, que es específicamente "hace falta plan pago", no
un problema transitorio. Verificado contra producción con GGAL.BA, YPFD.BA,
BMA.BA, SUPV.BA y BBAR.BA. **Este fallback no es una garantía**: depende de
que yfinance no esté bloqueando la IP de Vercel en ese momento (ver la
decisión de Hosting más abajo) — es "mejor que fallar siempre", no una
solución permanente. El universo default de auto-carga de la web (ver más
abajo) sigue siendo sólo EE.UU. por ahora, para no depender de ese
fallback en la primera impresión de cualquier visitante.

## Auto-carga de la web

Las dos pestañas de la web se auto-analizan al abrir, sin que el usuario
tenga que escribir nada ni apretar "Analizar" — pedido explícito del usuario
("quiero que automáticamente tenga ya el análisis de lo más importante y con
mayor volumen"). El screener arranca con un preset de mega-caps de EE.UU.
(`AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA, JPM`, la lista más líquida e
importante del mercado) y el análisis profundo con AAPL; si el usuario ya
usó la web antes, se auto-corre su última búsqueda (`localStorage`) en vez
del default. El universo default es sólo EE.UU. porque los CEDEARs
argentinos hoy fallan en producción (ver el punto anterior) — meterlos acá
mostraría "sin datos" en la primera carga de cualquier visitante.

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
