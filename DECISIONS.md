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

## Ranking precalculado en vez de en vivo

El screener gasta 4 requests de FMP por ticker (perfil + income + balance +
cash flow). Un universo de ~100 empresas son ~400 requests **por carga de
página**, contra un free tier de 250/día — y como la web se auto-analiza al
abrir, el primer visitante quemaría la cuota del día entero.

Por eso el universo grande se calcula una vez (`python -m bot precompute`) y
se versiona en `bot/web/data/top100.json`. Decisiones dentro de esa decisión:

- **Se guarda el mismo payload que devuelve `/api/screen`**, no un formato
  propio: el front lo renderiza sin ramificar, y no hay dos serializaciones
  que puedan desincronizarse.
- **Se genera con yfinance, no con FMP.** yfinance no tiene cuota y corre
  local, donde no hay IP de datacenter que lo bloquee. Precalcular contra FMP
  gastaría de una la cuota que este mecanismo justamente busca ahorrar.
- **Es un artefacto commiteado, no un job automático.** Queda revisable en el
  diff, se despliega con el repo y no agrega infraestructura (ni cron, ni
  almacenamiento externo, ni una dependencia nueva).
- **El universo es una lista curada a mano**, no derivada del volumen diario:
  derivarla exigiría un endpoint que el free tier no da, y cambiaría todos los
  días, con lo cual cada refresco mezclaría "cambió el fundamental" con
  "cambió el universo". Se arma priorizando cobertura sectorial, porque el
  ranking es intra-sector y un sector con menos de `min_peers` queda sin
  rankear.
- **Se declara la antigüedad en la interfaz** ("generado hace 3 h"). Servir un
  dato viejo sin decir que es viejo sería el único uso deshonesto de esto.

## Cache del brief

El `brief` no cacheaba nada: cada request retraía 5 ejercicios de balances y
el histórico de precios, aunque fuera el mismo ticker de hace un minuto — y la
web lo pide sola al abrir, así que era cuota tirada en cada visita. Se cachea
el **payload final** y no el `CompanyProfile`, porque el payload ya es JSON
puro: serializar el objeto entero (historia, valuación, scores) sería trabajo y
superficie de bugs para llegar al mismo lugar. La clave es
`ticker + años + proveedor`, y sólo se guardan los resultados exitosos: un
error se propaga sin quedar cacheado, para que el siguiente intento vuelva a
probar.

Las respuestas de la API además viajan con `Cache-Control: private, max-age`
corto cuando el request pidió cache, para que moverse por la página no
redispare el mismo fetch. Con `cache=0` se manda `no-store`, así el usuario
conserva la forma de forzar datos frescos. Los estáticos siguen en `no-store`
a propósito: sin eso hay que hard-refreshear ante cada cambio de CSS.

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

## Diseño de la interfaz

Las dos vistas tienen tratamientos distintos porque tienen trabajos distintos:
el informe **se lee**, el screener **se escanea**.

- **Informe (`brief`): tearsheet.** Un documento, no un tablero. La estructura
  la dan reglas de 1px y una canaleta izquierda de 56px donde vive el rótulo al
  margen. Ocho tarjetas serían ocho marcos compitiendo con el contenido.
- **Screener: grilla de fichas.** Cada empresa es una ficha en una grilla de 3
  columnas — el patrón de *small multiples*: la misma unidad repetida, que es
  justamente la forma para comparar muchas unidades iguales. Dentro de cada
  ficha, la métrica ocupa dos renglones agrupados por significado: arriba
  "qué y cuánto" (etiqueta + valor), abajo "dónde está frente a sus pares"
  (barra + percentil). Poner el percentil junto al valor mezclaba las dos
  lecturas y, de paso, apretaba la etiqueta hasta truncarla.

  (Una versión intermedia puso el sector como UNA hoja con las empresas como
  filas apiladas. Se descartó a pedido del usuario: en una grilla se comparan
  varias empresas de un vistazo, y una lista vertical obliga a recorrerlas de a
  una.)

**El acento no es un color semántico.** Es la regla que más cambió el resultado.
Antes el verde era el botón, la barra de percentil, el badge "barato" y el
"segura": cuando un color significa todo, no significa nada. Ahora el cromo
(botones, foco, enlaces) es un azul pizarra que no aparece nunca sobre un dato,
y verde / ámbar / rojo quedan reservados a bueno / atención / malo. Los estados
siempre viajan con etiqueta, nunca color solo — es lo que los hace legibles con
daltonismo y en impresión.

Otras reglas que salieron de aplicar la guía de visualización de datos:

- **Las barras de percentil son una rampa neutra, no de color.** Primero se
  hicieron azul-sobre-azul, siguiendo el spec de meters ("el riel es un paso del
  mismo tono, así el estado se lee a lo largo de toda la barra"). Fue un error de
  aplicación: ese spec describe un meter de *severidad*, donde el relleno cambia
  de color según el estado y teñir el riel hace que el estado se lea entero. Acá
  el relleno es de un solo tono a propósito, así que no hay estado en el color —
  el riel teñido no aportaba nada y sí quitaba legibilidad: una fila en el
  percentil 0 mostraba una barra azul de punta a punta y de un vistazo parecía
  llena. En neutro, la longitud es la única codificación (que es lo correcto: el
  tono era redundante) y todo el color queda libre para lo semántico. Un
  percentil bajo usa un paso más claro de la misma rampa neutra, no rojo: estar
  abajo en el sector no es una alarma.
- **La barra va a ancho fijo, no a `1fr`.** Estirada a toda la fila se leía como
  una franja y le ganaba en peso al número, que es el dato que se viene a mirar.
- **Geometría de la marca**: cuadrada en la base, 4px redondeada en la punta del
  dato. Una píldora redondeada de los dos lados miente sobre dónde empieza.
- **Cifras proporcionales en los valores sueltos**; `tabular-nums` sólo donde los
  dígitos se alinean en columna (tablas, filas de métricas). El ancho fijo en un
  titular hace que un "1.000" se vea suelto.
- **La sans lleva todos los números**, incluidos los grandes. La mono queda
  reservada a identificadores (tickers, códigos), donde el ancho fijo es
  funcional y es el vocabulario del rubro — no decoración.
- **El texto no se tiñe con el color del dato.** En el checklist de Piotroski la
  marca ✓/✗ lleva el color y el texto queda en tinta: un renglón entero en rojo
  se lee como error, no como "no cumple".

Sigue sin haber build step, framework ni fuentes externas — una hoja de estilos
y el stack de fuentes del sistema. El tema oscuro no es un agregado: los tokens
se definen para los dos modos y `color-scheme` hace que los controles nativos
acompañen. Los contrastes se verificaron con WCAG contra las dos superficies
reales, y la rampa del meter, monótona en luminancia — computados, no a ojo.

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
