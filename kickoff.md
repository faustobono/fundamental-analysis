# Kickoff

## Estado actual

Proyecto: bot de análisis fundamental en Python.

- La corrección de fuente FMP y la adaptación para Vercel están integradas en Git.
- La integración con Financial Modeling Prep (FMP) fue validada contra la API real usando AAPL.
- `yfinance` sigue siendo el proveedor por defecto.
- FMP funciona mediante `FMP_API_KEY` y se selecciona con `BOT_PROVIDER=fmp` o `--provider fmp`.
- La suite actual tiene `354 tests` y todos pasan sin red.
- Desplegado en Vercel: `https://fundscan.vercel.app` (el proyecto se
  renombró de `fundamental-analysis` a `fundscan`; el dominio viejo
  `fundamental-analysis-eight.vercel.app` puede seguir resolviendo por un
  tiempo pero no es el canónico).
- Los CEDEARs/ADRs argentinos ya no fallan en producción: cuando FMP exige
  plan pago (`HTTP 402`), el bot reintenta con yfinance automáticamente.
  Verificado contra producción (GGAL.BA, YPFD.BA, BMA.BA, SUPV.BA, BBAR.BA).
  Ver el bloque más abajo, al final de "Última integración".

## Última integración

El último bloque de cambios incluye la corrección de la fuente declarada por el
brief, el runtime de Vercel, la configuración de rutas, el cache efímero de
serverless y la documentación operativa. La producción fue validada con FMP.

Sesión de Claude Code (después del deploy): se detectó contra producción
(`/api/brief?ticker=AAPL` con `BOT_PROVIDER=fmp`) que `CompanyProfile.gaps()`
declaraba "Segmentos de ingreso: yfinance no los publica." sin importar el
proveedor real. Se agregó `CompanyProfile.provider` (seteado en
`assemble_profile`, tanto desde `build_profile` como desde `build_profile_fmp`)
y se usa en `gaps()` y en `render_data_block`. Ese cambio quedó commiteado
(`f303d0b`), no pusheado.

Misma sesión, después: se agregaron botones "i" de info en toda la web
(screener y brief) para explicar cada métrica/%/estadística. Archivos nuevos:
`bot/web/static/glossary.js` (glosario estático, 33+ conceptos, clave = mismo
`name`/`metric` que ya manda el JSON del backend) y `bot/web/static/info.js`
(componente de popover, un solo listener delegado). Se tocaron `app.js`,
`brief.js`, `index.html`, `styles.css` y el `STATIC_FILES` de `server.py`. Sin
cambios en Python más allá del allowlist — 302 tests siguen pasando igual.
Verificado en navegador con browser tooling: popover abre/cierra/hace toggle
bien, dark mode y mobile (375px) correctos, accesible (aria-label por botón).
Bug encontrado y corregido en el camino: cerrar el popover en cualquier
`scroll` event era demasiado agresivo (lo cerraba un scroll de un par de
píxeles); ahora reposiciona en vez de cerrar.

Sesión de Claude Code (siguiente, pedido compuesto de 5 pasos): se amplió la
Capa 1 para que un trader tenga más con qué armar su propio análisis
fundamental, se corrió un pase de optimización/refactor con un subagente
nuevo, se renombró el proyecto de Vercel y se desplegó todo a producción.

1. **Más datos fundamentales.** Nuevos campos en `AnnualPeriod`
   (`bot/analysis/series.py`): `total_assets`, `retained_earnings`,
   `dividends_paid`, con properties derivadas `roa`, `asset_turnover`,
   `payout_ratio` (además de `fcf_margin`, que ya existía pero no se exponía
   en ninguna tabla). Alias nuevos en `bot/fetcher/statements.py`
   (yfinance: `Total Assets`, `Retained Earnings`, `Cash Dividends Paid` /
   `Common Stock Dividend Paid` — verificados contra AAPL real) y
   `bot/fetcher/fmp/fields.py` (`totalAssets`, `retainedEarnings`,
   `dividendsPaid`), wireados en `build_history()` y en
   `bot/fetcher/fmp/deep.py::_annual_period()`. Módulo nuevo
   `bot/analysis/scores.py` con **Altman Z-Score** (riesgo de quiebra, usa
   market cap actual — sólo se calcula para el último ejercicio) y
   **Piotroski F-Score** (9 criterios binarios año-contra-año, puramente
   contable). Ningún criterio se fuerza a `False` si falta el dato: queda en
   `None` y se excluye del `max_score`, no penaliza. `CompanyProfile` tiene
   un campo nuevo `financial_strength: Optional[FinancialStrength]`
   calculado en `assemble_profile()`. Todo expuesto en `bot/web/brief_api.py`
   (nuevas filas en `PROFITABILITY_METRICS`/`GROWTH_METRICS` + bloque
   `financial_strength`), `bot/brief/render.py` (nueva sección markdown
   "Fortaleza financiera") y el front (`brief.js`: sección con badge de zona
   Altman + checklist Piotroski; `glossary.js`: entradas nuevas). Verificado
   contra AAPL real (CLI y `/api/brief`): Altman 11.88 "segura", Piotroski
   8/9. Sin WACC ni DCF automático a propósito — sigue la misma decisión de
   siempre (requieren supuestos de mercado).
2. **Optimización/refactor.** Se creó `.claude/agents/code-optimizer.md`,
   una definición de subagente nueva y persistente (quedará disponible como
   `subagent_type` en sesiones futuras tras reiniciar; en esta sesión se
   ejecutó su mismo brief vía el agente general-purpose porque el listado de
   agentes ya estaba cargado). Resultado: revisó duplicación
   yfinance/FMP en los fetchers, hot-paths en `series.py`/`valuation.py`/
   `scorer/`, y el front — decidió que casi todo lo investigado no valía la
   pena tocar (duplicación real pero chica, o ya acotada por
   `TARGET_YEARS = 5`) y aplicó un solo cambio real: en
   `bot/analysis/scores.py`, `piotroski_f_score` pasó de 9 bloques
   `checks.append(...)` con un índice posicional hacia una tupla paralela de
   labels (frágil: un reorden desincroniza label y criterio sin que ningún
   test lo detecte) a un dict `_CRITERIA_LABELS` (`name -> label`) más un
   dict literal `passed` armado con comprehension — mismo comportamiento,
   mismo orden, más difícil de romper por accidente. 319 tests y ruff en
   verde antes y después.
3. **Dominio de Vercel.** El proyecto se renombró de `fundamental-analysis`
   a `fundscan` (`vercel project rename`). El alias corto
   `fundscan.vercel.app` se confirmó libre (`DEPLOYMENT_NOT_FOUND` antes del
   rename), pero renombrar el proyecto no reasigna solo el dominio: hizo
   falta un `vercel --prod` nuevo y un `vercel alias set` explícito para
   apuntar `fundscan.vercel.app` al deploy. Ese mismo `vercel --prod` volvió
   a aliasear automáticamente `fundamental-analysis-eight.vercel.app` al
   nuevo deploy (quedó como alias secundario, sirve la versión actual, pero
   ya no es el dominio canónico).
4. **Deploy.** Commit y push explícitamente autorizados por el usuario para
   esta tanda (a diferencia de las tres tandas anteriores de la sesión,
   donde se commiteaba pero no se pusheaba salvo pedido explícito). Al
   desplegar, `fundscan.vercel.app` quedó atrás de la protección SSO de
   Vercel (`ssoProtection: all_except_custom_domains` en la config del
   proyecto — por algún motivo el alias viejo había quedado exceptuado y el
   nuevo no) devolviendo 302 a `vercel.com/sso-api` en vez de la app. Se
   preguntó al usuario antes de tocar una config de seguridad de la cuenta;
   confirmó desactivar SSO (`vercel project protection disable fundscan
   --sso`) porque la app es pública a propósito, igual que con el dominio
   viejo. Verificado post-fix: `/api/health`, `/api/screen` (AAPL+MSFT) y
   `/api/brief?ticker=AAPL` responden 200 sin auth en `fundscan.vercel.app`,
   con `financial_strength` presente (Altman ≈11.9 "segura", Piotroski 8/9).

Sesión de Claude Code (siguiente, a pedido del usuario tras probar la web
desplegada): probando `fundscan.vercel.app` con más tickers se descubrió que
los CEDEARs argentinos (GGAL.BA, YPFD.BA, BMA.BA) fallan en producción con
`FMP: HTTP 402` — confirmado que no es un bug del pipeline (local con
yfinance, GGAL.BA/PAMP.BA traen todo bien); es que el free tier de FMP no
cubre los estados contables de estas empresas. Diagnóstico comparando
`PAM` (subyacente directo) vs `PAMP.BA` (CEDEAR mapeado): el output es
idéntico salvo la etiqueta y el warning — porque `resolve_symbol()` resuelve
el ticker al subyacente *antes* de traer nada, precio incluido, así que el
`.BA` nunca cambia un número, sólo decide si el ticker se resuelve y si se
avisa que es un CEDEAR. Sin ese sufijo, tickers como `PAMP` (que no coincide
con el subyacente `PAM`) no encuentran nada (404).

A partir de ahí, el usuario pidió que la web ya tenga el análisis de las
empresas más importantes/con mayor volumen al abrir, sin tocar nada. Se
preguntó alcance (¿sólo screener, o también brief?) y universo (¿EE.UU. y
Argentina, sabiendo que los CEDEARs fallan hoy en prod?) antes de tocar
código. Elegido: ambas pestañas se auto-analizan al cargar, y el universo
default es sólo EE.UU. por ahora (el problema de FMP con CEDEARs sigue sin
resolver). Cambios, sólo frontend (sin tocar Python):

- `bot/web/static/app.js`: nueva constante `TOP_VOLUME` (`AAPL, MSFT, NVDA,
  GOOGL, AMZN, META, TSLA, JPM`), nuevo preset "Top volumen" (primero en la
  lista), default de la caja de texto pasó de `PRESETS["Mixto"]` (que tiene
  un CEDEAR) a `TOP_VOLUME`, y se agregó `runScreen()` al final del arranque
  para que corra sola al cargar — con la última búsqueda del usuario
  (`localStorage`) si ya usó la web antes, o con el default si es la primera
  vez.
- `bot/web/static/brief.js`: default del ticker pasó de `""` a `"AAPL"`, y se
  agregó `run()` al final del arranque, mismo criterio.
- Las dos pestañas cargan sus datos en paralelo al abrir la página aunque
  sólo una esté visible (son dos `<main>` con `hidden` alternado, no un
  router) — así al cambiar de pestaña ya está resuelta, no hay que esperar.
- Verificado en navegador con `localStorage.clear()` (simulando primera
  visita): screener trae las 8 empresas de `TOP_VOLUME` con datos, brief
  trae AAPL con las 8 secciones, sin errores de consola. 319 tests Python
  sin cambios (feature 100% frontend).

Sesión de Claude Code (siguiente, a pedido del usuario: "arreglá el 402 de
FMP para los CEDEARs"). Diagnóstico primero: sin `FMP_API_KEY` local para
probar contra la API real, se usó `vercel env pull` (el valor viene
redactado por el CLI, así que sólo sirvió para confirmar que la var existe,
no para leerla) y finalmente se verificó directamente contra producción y
contra un deploy de preview — el de preview tiene una `FMP_API_KEY` inválida
en su configuración (Preview ≠ Production en Vercel; pendiente aparte, sin
relación con este fix). El fix:

- `bot/models.py`: `FetchError`/`UpstreamError` ganaron un campo
  `status_code: Optional[int]` (default `None`) para que el resto del
  pipeline pueda distinguir "FMP pide plan pago (402)" de otros errores
  (429 rate limit, 401 key inválida, etc.) sin parsear el mensaje.
- `bot/fetcher/fmp/client.py`: `FmpClient.get()` setea `status_code=exc.code`
  en el `UpstreamError` que lanza ante un `HTTPError`, y agrega el hint
  "este dato exige un plan pago de FMP" para 402.
- `bot/fetcher/service.py` (camino del screener): nueva clase
  `_FmpWithYfinanceFallback`, que envuelve el adapter de FMP — si devuelve
  402, reintenta con yfinance y marca el snapshot resultante con un warning
  (`FundamentalSnapshot.with_warning`). Se usa como `primary` en
  `_build_adapters("fmp")`, así que cubre tanto tickers directos como
  CEDEARs (que la pasan como `underlying_adapter` de `BymaAdapter`). Otros
  códigos de error (429, 401, etc.) NO caen al fallback — sólo 402, porque
  esos otros sí son errores reales que no tiene sentido esconder.
- `bot/analysis/profile.py` (camino del brief): mismo criterio en
  `company_profile()` — si `build_profile_fmp()` tira un 402, reintenta con
  `build_profile()` (yfinance) y agrega el mismo warning al
  `CompanyProfile` resultante vía `dataclasses.replace()`. Si el fallback
  también falla, el error final menciona las dos fuentes que se probaron.
- Si el fallback funciona, `profile.provider`/`snapshot.source` quedan en
  `"yfinance"` (o `"byma"` para un CEDEAR, que ya lo pisa igual) — reflejan
  la fuente real, no lo que pedía `BOT_PROVIDER`.
- 10 tests nuevos (`tests/test_fmp.py`, `tests/test_service.py`,
  `tests/test_analysis.py`), todos con dobles/mocks — sin red, como el
  resto de la suite. 329 tests en total, ruff limpio.
- **Verificado contra producción con datos reales** (la única forma de
  probar el 402 real, sin key local): GGAL.BA, YPFD.BA, BMA.BA, SUPV.BA y
  BBAR.BA ahora responden bien tanto en `/api/screen` como en `/api/brief`,
  con el warning de fallback visible. Control sobre tickers de EE.UU.
  (AAPL, MSFT, NVDA, JPM) sin cambios, sin ese warning. Se preguntó al
  usuario antes de desplegar a producción (no había pedido explícito de
  deploy/push para este fix puntual) — confirmó, y confirmó también el
  commit + push después de ver los resultados reales.
- **Importante:** el fallback depende de que yfinance no esté bloqueando la
  IP de Vercel en el momento de la consulta. Hoy funcionó — pero la
  documentación previa de este proyecto advertía que yfinance sí puede
  bloquear IPs de datacenter, así que esto es "mejor que fallar siempre",
  no una garantía permanente. Si vuelve a romperse, revisar primero si
  yfinance está bloqueando, no asumir que el fix dejó de andar.

Sesión de Claude Code (siguiente). El usuario preguntó en qué había quedado
"lo de los 100 tickers con mayor volumen" — nunca había pedido 100: había
pedido "lo más importante y con mayor volumen" sin número, y había quedado en
8 mega-caps. Se le aclaró y se le mostró la cuenta que lo bloqueaba (4 requests
de FMP por ticker × 100 = ~400 por carga, contra 250/día de free tier, con la
web auto-analizando en cada visita). Eligió precalcular. Cuatro cambios:

1. **Ranking precalculado.** Nuevo `bot/web/precomputed.py` (universo curado de
   100 tickers de EE.UU. con cobertura sectorial, `stamp`/`save`/`load`) y
   comando `python -m bot precompute`, que reusa `run_screen()` — o sea que el
   JSON generado es exactamente el payload de `/api/screen` y el front lo pinta
   sin ramificar. Se genera **local con yfinance** (sin cuota) y se versiona en
   `bot/web/data/top100.json` (~426 KB). Nuevo endpoint `/api/top` que lo sirve
   sin tocar el proveedor; si el archivo no está, 404 con el comando a correr.
   El front lo carga al abrir y ofrece un botón "Top 100" para volver; el
   textarea queda con el universo chico para las corridas en vivo. Se declara
   la antigüedad en la interfaz ("generado hace 3 h"). Primera generación:
   100/100 tickers ok, 11 sectores, ninguno "thin".
2. **Cache del brief** (pedido del usuario: "si de un ticker se pidió request,
   que no se vuelva a pedir"). El brief no cacheaba nada. Nuevo `PayloadCache`
   en `bot/fetcher/cache.py` (tabla `payloads` aparte, mismo SQLite y mismo
   TTL) que guarda el payload final —ya es JSON puro, no hace falta serializar
   el `CompanyProfile`— con clave `ticker+años+proveedor`. Sólo cachea éxitos:
   un `FetchError` se propaga sin guardarse. `run_brief` acepta `use_cache` y
   `cache_path` (inyectable, como `build_service`).
3. **Cacheo HTTP.** Las respuestas de la API iban todas con `no-store`, así que
   el navegador re-pedía siempre. Ahora `/api/screen` y `/api/brief` mandan
   `private, max-age=300` cuando el request pidió cache (`cache=0` sigue siendo
   `no-store`), y `/api/top` 900. Los errores nunca se cachean. Los estáticos
   siguen en `no-store` a propósito.
4. **Rediseño visual** (pedido: "más moderno y minimalista, cambio rotundo,
   simple pero profesional"). `styles.css` reescrito entero conservando todos
   los selectores que genera el JS. Paleta neutra más fría y contrastada, header
   translúcido con blur, pestañas como control segmentado, secciones del brief
   sin marco (aire + línea de 1px en vez de ocho tarjetas), nombre de la empresa
   como título de 22px, `tabular-nums` en todas las cifras, foco con anillo
   suave. Dos bugs reales encontrados y corregidos verificando en el navegador:
   (a) `.field label` (0,1,1) le ganaba a `.toggle` (0,1,0) y "Usar cache"
   salía en mayúsculas — se subió a `.field label.toggle`; (b) en
   `.identity-line`, que es flex con `gap`, los paréntesis sueltos alrededor del
   ticker eran items del flex y quedaban separados ("Apple Inc. ( AAPL )") — se
   sacaron del markup en `brief.js`.

Verificación: 354 tests sin red, ruff limpio, y en navegador — dark y light,
1280px y 375px, sin overflow horizontal, tablas anchas scrolleando dentro de su
contenedor, sin errores de consola. El panel del navegador se ocultó a mitad de
camino (problema de entorno, ya había pasado antes en esta sesión), así que la
parte final se verificó por geometría y estilos computados en vez de
screenshots — más preciso, de hecho.

Sesión de Claude Code (siguiente). El usuario rechazó el rediseño anterior
("parece muy app barata") y pidió rehacerlo cargando skills de diseño. Se
cargaron `dataviz` y `artifact-design`, y el diagnóstico salió de ahí, no del
gusto: (a) el acento verde era **también** el color semántico — la guía es
explícita en que el color de estado va separado del acento y no cuenta como
acento; con el verde siendo botón, barra, "barato" y "segura" a la vez, el color
no comunicaba nada; (b) el resultado caía en dos de los looks que la guía marca
como genéricos ("near-black con un pop verde", "rounded cards en todos lados").

Se rehízo `styles.css` entero bajo el concepto **tearsheet** (ver `DECISIONS.md`
para el criterio completo). Cambios de fondo: acento azul pizarra separado de los
semánticos; una hoja por sector con filas en vez de baraja de tarjetas; canaleta
izquierda de 56px como canto común; rampa secuencial de un tono para las barras
(riel = paso claro del mismo azul, no gris); cifras proporcionales en valores
sueltos y `tabular-nums` sólo en columnas; la sans lleva los números y la mono
queda para identificadores; el texto no se tiñe con el color del dato.

Sobre el validador de paletas: se corrió `scripts/validate_palette.js` y **dio
FAIL, pero por mal uso mío** — su alcance es "categorical palettes only" y le
pasé una mezcla de acento + rampa + estados como si fueran series. El chequeo que
sí correspondía (contraste WCAG contra las dos superficies reales, y monotonía de
la rampa del meter) pasa en los 15 pares. Vale recordarlo si alguien vuelve a
correr el validador sobre esta paleta y ve rojo: no aplica tal cual.

Verificado: 354 tests, ruff limpio, y en navegador light y dark a 1280px y 375px
— sin overflow horizontal, tablas anchas scrolleando adentro, hero sin desbordar
en mobile, sin errores de consola. Nota de entorno: los screenshots no siguen el
scroll hecho por JS (salen en blanco), así que lo que está fuera del primer
viewport se verificó por geometría y estilos computados.

Sesión de Claude Code (siguiente, dos iteraciones de diseño más).

1. **Barras de percentil a rampa neutra.** El usuario mandó una captura: en
   dark, una fila `p0/4` mostraba una barra azul de punta a punta y parecía
   llena — el peor del sector se leía como el mejor. Causa: yo había aplicado
   mal el spec de meters ("el riel es un paso del mismo tono, así el estado se
   lee a lo largo de toda la barra"), que describe un meter de *severidad*,
   donde el relleno cambia de color según el estado. El nuestro es de un solo
   tono a propósito, así que el riel teñido no aportaba nada. Ahora la rampa es
   neutra: separación relleno/riel 8.1:1 en light y 5.4:1 en dark, riel a
   1.25:1 contra la hoja. Verificado que `p0` renderiza 0px de relleno.
2. **Screener a grilla de fichas** (pedido: "no quiero uno abajo del otro,
   quiero cuadros"). Se revirtió la hoja-con-filas y quedó una grilla de 3
   columnas (`minmax(300px, 1fr)`), que es el patrón de *small multiples*. La
   métrica pasó a dos renglones agrupados por significado — arriba etiqueta +
   valor, abajo barra + percentil. Ese reagrupamiento no es cosmético: tener el
   percentil arriba se comía los ~47px que hacían que la etiqueta más larga
   ("Crecimiento de ingresos YoY ↑", 180px medidos) se truncara en una ficha
   angosta. El informe **no** se tocó: sigue siendo el tearsheet con canaleta,
   porque se lee en vez de escanearse (ver `DECISIONS.md`).

Sobre las skills: están cargadas las dos de diseño que existen (`dataviz` y
`artifact-design`; la tercera, `artifact-diagramming`, es para diagramas). Lo
que quedaba por minar eran los archivos de referencia de `dataviz` —
`choosing-a-form.md` y `components.md` fueron los que dieron el encuadre de esta
tanda. Quedan sin leer `interaction.md` y `color-formula.md`.

Nota de entorno que empeoró: los screenshots del navegador se volvieron poco
confiables — se renderizan a escala mínima después de cada `resize_window`, y
no siguen el scroll hecho por JS. Funcionan bien una vez, en una pestaña recién
creada y sin redimensionar. El resto se verificó midiendo en el DOM (ancho de
columnas, si alguna etiqueta trunca vía `scrollWidth > clientWidth`, overflow,
contrastes) — que para esto es más preciso que mirar.

## Protocolo de reanudación

Codex, Claude Code y OpenCode comparten el mismo contexto versionado. Al iniciar
una nueva sesión, deben leer `AGENTS.md`, `kickoff.md`, `README.md`,
`DECISIONS.md` y `TODO.md` antes de modificar archivos. `CLAUDE.md` apunta a
este mismo protocolo para que Claude Code lo cargue automáticamente.

## Arquitectura

- `bot/fetcher/`: proveedores de datos, cache y resolución de CEDEARs/ADRs.
- `bot/fetcher/fmp/`: cliente HTTP stdlib, aliases de campos, adapter normal y adapter profundo.
- `bot/normalizer/`: modelo común, moneda y validaciones.
- `bot/scorer/`: ranking intra-sector por percentil o z-score.
- `bot/analysis/`: historial financiero, salud, ratios y valuación histórica.
- `bot/brief/`: informe determinístico listo para pasar a un modelo cualitativo.
- `bot/web/`: interfaz web local con stdlib.
- `bot/cli/`: comandos `screener`, `brief` y `serve`.
- `tests/`: pruebas unitarias y de integración con fixtures, sin red.

## Configuración local

Desde la raíz del proyecto:

```bash
cd ~/Desktop/fundamentalAnalysis
export FMP_API_KEY="TU_API_KEY_REAL"
export BOT_PROVIDER=fmp
```

No guardar la API key en archivos del repositorio ni subirla a GitHub.

## Comandos de verificación

Suite completa:

```bash
.venv/bin/python -m pytest
```

Informe de una empresa:

```bash
.venv/bin/python -m bot brief AAPL --data-only
```

Screener:

```bash
.venv/bin/python -m bot screener --tickers AAPL,MSFT,GOOGL
```

Web:

```bash
.venv/bin/python -m bot serve --no-browser
```

Abrir `http://127.0.0.1:8000`.

Si aparece `Address already in use`, ya hay un servidor ejecutándose en ese puerto.

## Próximos pasos

1. Probar un universo más grande para que el ranking sectorial tenga suficientes peers.
2. Verificar el comportamiento de CEDEARs/ADRs usando FMP y `cedear_map.json`.
3. Monitorear el consumo y los límites del plan FMP en producción.
4. Documentar el procedimiento de actualización de Vercel si cambia el flujo.

## Preparación para Vercel

- `api/index.py` expone el handler existente como Vercel Function.
- `vercel.json` reescribe las rutas públicas hacia esa función.
- No se requiere comando de arranque: Vercel detecta el runtime Python.
- El cache SQLite local es efímero en serverless; no debe considerarse persistente.
- Producción validada con FMP: `/api/health`, screener de `AAPL,MSFT,GOOGL` y
  brief de `AAPL` respondieron HTTP 200 sin fallas.

## Decisiones importantes

- Los ratios se calculan desde líneas contables crudas, no desde ratios precalculados por FMP.
- Los campos FMP se buscan mediante aliases para tolerar cambios entre versiones de la API.
- No se calcula WACC automáticamente porque requiere supuestos de mercado.
- El ranking es intra-sector; con pocos peers el resultado debe interpretarse con cautela.
- No agregar la API key a commits, logs, fixtures ni documentación.
