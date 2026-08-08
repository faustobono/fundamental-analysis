# Bot de análisis fundamental

Screener que trae fundamentals, los normaliza y **rankea por sector**.
Determinístico: no depende de ningún LLM; puede usar FMP con API key para operar
en producción.

```
fetcher/     yfinance + FMP + BYMA (CEDEARs/ADRs) + cache SQLite con TTL
normalizer/  modelo común, sanity checks, moneda
scorer/      ranking intra-sector por percentil o z-score
analysis/    series 5 años, salud financiera, valuación vs. historia propia
brief/       informe de una empresa listo para pegar en un chat
web/         UI local (stdlib http.server, sin framework)
cli/         python -m bot screener | brief | serve
```

Dos usos distintos: **`screener`** filtra un universo por sector, **`brief`** hace la
inmersión en una sola empresa.

## Proveedores de datos

El bot lee de dos fuentes, detrás de la misma interfaz — el resto del pipeline no
las distingue:

| `--provider` | Qué es | Key | Para |
|---|---|---|---|
| `yfinance` (default) | Scrape de Yahoo | No | Correr local |
| `fmp` | API de Financial Modeling Prep | Sí (`FMP_API_KEY`) | Desplegar |

**Por qué dos.** yfinance scrapea endpoints no oficiales de Yahoo: anda gratis en tu
máquina, pero Yahoo bloquea IPs de datacenter, así que en la nube falla. FMP es una
API con key que sale sin bloqueo — es la que sirve para un deploy.

FMP free: 250 requests/día, 5 años de balances anuales de empresas US (incluye los
subyacentes de CEDEARs/ADRs). Sacás la key gratis en
[financialmodelingprep.com](https://site.financialmodelingprep.com/) (email, sin
tarjeta).

```bash
export FMP_API_KEY=tu_key
export BOT_PROVIDER=fmp        # o pasá --provider fmp en cada comando
```

**Los ratios se calculan igual desde las líneas contables**, no se toman precocidos
de FMP — mismo principio que con yfinance. Por eso el free tier alcanza: sólo hace
falta el statement crudo, el precio y el sector.

### Validar FMP contra la API real (smoke-test)

La integración fue validada contra la API real con AAPL. Para repetir la prueba:

```bash
FMP_API_KEY=tu_key python -m bot brief AAPL --provider fmp --data-only
```

Qué mirar en la salida:
- **Tablas llenas de números** → los campos coinciden. Todo bien.
- **Una columna toda en `—`** → un alias de campo quedó desfasado; el fix es una
  línea en `bot/fetcher/fmp/fields.py`.
- **Dividend yield** → verificá que la magnitud tenga sentido (~0.5–3%). Si da 4x,
  FMP reporta el dividendo trimestral donde el bot asume anual (está anotado en el
  código).

El informe identifica la fuente utilizada (`FMP` o `yfinance`) en la sección `Fuente`.

## Setup

```bash
uv venv --python python3.12 .venv && uv pip install -e ".[dev]"
```

Sin `uv`:

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

Dependencias: `yfinance` y `pandas`. FMP usa sólo la stdlib (`urllib`), sin sumar nada.

## Uso

### Web local

```bash
python -m bot serve
```

Abre `http://127.0.0.1:8000` solo. Escribís el universo, elegís método y sale el
ranking por sector con las barras de percentil; cada tarjeta expande a todos los
ratios, la escala del negocio y los warnings de calidad del dato.

El servidor bindea a `127.0.0.1`: sólo tu máquina. Sirve una allowlist fija de tres
archivos estáticos, así que no hay path traversal posible. Tope de 150 tickers por
corrida para que un pegado accidental no dispare cientos de fetches.

### Vercel

La app se puede desplegar como una función Python de Vercel. El entry point
`api/index.py` reutiliza el mismo handler que la web local y `vercel.json` reescribe
las rutas públicas al handler, por lo que `/`, `/api/screen`, `/api/brief` y
`/api/health` se conservan.

En el proyecto de Vercel configurá estas variables de entorno (Production,
Preview y Development):

```text
BOT_PROVIDER=fmp
FMP_API_KEY=tu_key_de_fmp
```

`FMP_API_KEY` es un secreto del servidor: nunca la pongas en el frontend ni en
`vercel.json`. La función no conserva el cache SQLite entre invocaciones, así que
la cuota de FMP debe considerarse al exponer la app públicamente.

La producción actual está disponible en
[`fundscan.vercel.app`](https://fundscan.vercel.app).

Detalles de la UI que valen la pena:

- **El top-N filtra en cliente**, sin refetch. Cambiar de Top 3 a Todas es instantáneo.
- **Las barras del z-score se recortan a ±2.5σ.** Sin eso un z de 8 pintaría una barra
  de 800% de ancho.
- **Los faltantes se muestran en gris con "sin dato"** en vez de esconderse: que a un
  banco le falte ROIC es información, no un hueco.

### Informe de una empresa

```bash
python -m bot brief AAPL --out aapl.md
```

Emite el prompt de análisis fundamental **con las métricas ya calculadas adentro**,
listo para pegar en cualquier chat. Es la Capa 1 de un esquema de dos:

| | Qué hace | Dónde corre |
|---|---|---|
| **Capa 1** | Trae y calcula: ROIC, ROA y márgenes a 5 años, deuda neta/EBITDA, cobertura de intereses, días de caja, FCF neto de SBC, margen de FCF, payout ratio, acciones en circulación, rotación de activos, múltiplos vs. su propia mediana histórica, Altman Z-Score (riesgo de quiebra) y Piotroski F-Score (fortaleza fundamental año contra año) | Este bot. Determinístico, gratis, testeado |
| **Capa 2** | Tesis, moat, riesgos, veredicto | Vos, pegando el output en un chat |

`--data-only` emite sólo las tablas, sin el prompt. `--context-file` reemplaza el
contexto de inversor por el tuyo.

### Screener de un universo

```bash
python -m bot screener --tickers AAPL,GGAL.BA,MSFT
```

Modo batch desde archivo (un ticker por línea, `#` comenta):

```bash
python -m bot screener --tickers-file universo.txt --top-n 3 --json-out resultado.json
```

Flags que importan:

| Flag | Qué hace |
|---|---|
| `--method percentile\|zscore` | percentil es robusto a outliers; z-score premia magnitud |
| `--metrics 'roic:2,fcf_yield,debt_to_equity_trend:1:lower'` | métricas y pesos a medida (`nombre[:peso[:lower\|higher]]`) |
| `--no-cache` / `--cache-ttl-hours` | control del cache (default 24hs, en `~/.cache/fundamental-bot/`) |
| `--top-n` | cuántas mostrar por sector |
| `--json-out` | vuelca ranking + snapshots + fallas a JSON |

## Decisiones de diseño

**El ranking es siempre intra-sector.** Un banco apalancado 10x no es "peor" que una
software con caja neta: en banca el apalancamiento *es* el negocio. Comparar ratios
cross-sector produce un ranking que parece riguroso y no significa nada.

**Los datos faltantes no se imputan.** Si a una empresa le falta FCF yield, se la
excluye de esa métrica y se renormalizan los pesos sobre lo que sí hay. Rellenar con
0 o con la mediana castiga (o premia) a la empresa por el silencio del proveedor.
Debajo de `--min-metrics` no se rankea: se reporta aparte.

**Los ratios absurdos se descartan, no se corrigen.** Un P/E de 99.999 pasa a `None`
con un warning. Inventar un valor plausible es peor que no tener el dato.

**Todo el pipeline es determinístico.** Mismo universo, mismo ranking, siempre. Nada
de esto depende de un servicio pago ni de un modelo: el "por qué rankeó así" ya está
en la salida, métrica por métrica, con el percentil sectorial y cuántos peers tenían
dato. Eso es auditable; una narrativa generada no lo sería.

### Tres cosas que la API real rompe y el bot maneja

1. **`.info` de yfinance mezcla unidades entre campos.** `dividendYield` viene en
   porcentaje (`0.32` = 0.32%) y `trailingAnnualDividendYield` en fracción (`0.0031`).
   Elegir por magnitud es imposible, así que el yield se reconstruye desde
   `dividendRate / price`, donde la unidad es inequívoca.

2. **Un ADR argentino reporta el balance en ARS y cotiza en USD.** Cualquier ratio que
   cruce mercado con balance (FCF yield, market cap / patrimonio) queda mal por un
   factor igual al tipo de cambio. Cuando las monedas difieren, esos ratios no se
   calculan y queda el warning. Los ratios de una sola punta (ROIC, márgenes, D/E)
   siguen siendo válidos: la moneda se cancela.

3. **Los bancos reportan margen bruto en 0.** No es un margen del 0%: es un campo que
   el negocio no tiene. Dejarlo entrar los mostraría como los peores del sector.

4. **yfinance devuelve un ejercicio fantasma.** El quinto año de AAPL trae 6 valores
   de 39 filas. Contarlo como año infla el período declarado y mete una fila de
   guiones en cada tabla, así que se descarta.

5. **Las empresas dejan de reportar líneas.** Apple discontinuó `Interest Expense`
   como línea propia en 2024, pero la informó hasta 2023. Tomar sólo el último
   ejercicio dejaría el costo de deuda vacío teniéndolo: se busca hacia atrás y se
   informa de qué año salió.

### CEDEARs y ADRs

`GGAL.BA` no trae fundamentals confiables en yfinance, así que se resuelve el
subyacente (`GGAL`, NASDAQ) vía [`bot/fetcher/cedear_map.json`](bot/fetcher/cedear_map.json)
y se piden ahí. Es correcto sin ajustar por el ratio de conversión del CEDEAR: los
ratios son adimensionales.

⚠ **El mapping está curado a mano y no se valida contra ninguna fuente.** Verificá el
símbolo antes de sacar conclusiones. `TXAR.BA` está deliberadamente en la lista de
"sin subyacente": Ternium Argentina no es la misma entidad que Ternium S.A. (`TX`), y
usar `TX` daría los fundamentals de otra empresa.

### Valuación contra la propia historia

"¿Está cara respecto de cómo cotizó los últimos 5 años?" se arma cruzando 60 precios
mensuales con los fundamentals del ejercicio **que ya estaba publicado** en cada fecha.

Ese rezago —90 días por defecto— es lo que hace que el número signifique algo. Un
balance con cierre en diciembre no es público hasta febrero o marzo; usarlo para
valuar enero es sesgo de anticipación y produce una historia de múltiplos que nunca
existió.

Los múltiplos se calculan sobre el **último ejercicio anual**, no sobre los últimos
doce meses. El P/E de acá y el `trailingPE` de yfinance no son la misma cifra; la
ventaja es que el actual y el histórico se calculan igual, así que se pueden comparar
entre sí.

### Por qué no hay WACC

El `brief` entrega los componentes verificables —beta, costo de deuda derivado del
balance, estructura de capital, tasa efectiva— pero **no calcula el WACC**.

Calcularlo exige una tasa libre de riesgo y un equity risk premium: supuestos de
mercado, no datos de la empresa. Hardcodearlos es el mismo error que hardcodear un
tipo de cambio — en seis meses el número miente y nadie se entera. Y el spread
ROIC−WACC es justo la métrica donde un supuesto malo invierte la conclusión.

El prompt le pide a la Capa 2 que lo arme declarando los supuestos, lo marque
`[ESTIMADO]` y diga cuánto cambia la conclusión si el WACC se mueve ±2 puntos.

### ROIC

Aproximación: `EBIT × (1 - tax_rate) / (deuda + patrimonio)`. El capital invertido "de
libro" no resta caja excedente ni excluye goodwill, y NOPAT usa la tasa efectiva y no
la marginal. Sirve para comparar empresas del mismo sector entre sí, que es el único
uso que le da el scorer. No es un ROIC-vs-WACC de valuación. Los bancos no reportan
EBIT, así que ahí queda en `None` — y está bien, el ratio no aplica.

## Tests

```bash
.venv/bin/python -m pytest
```

293 tests, sin red. El fetcher recibe una `ticker_factory` inyectable (yfinance) o un
cliente HTTP inyectable (FMP), y el cache un reloj inyectable, así que todo el pipeline
—fetcher → normalizer → scorer → analysis → web— se testea de punta a punta con dobles,
para las dos fuentes.

## Limitaciones conocidas

- **Máximo 5 ejercicios, y a veces 4.** yfinance no da más. Un análisis de ciclo
  completo querría 10. El `screener` sigue usando un solo período por ticker; las
  series son cosa del `brief`.
- **Sin segmentos de ingreso.** yfinance no los publica, así que el punto 1 del
  informe (cómo gana plata la empresa) lo tiene que aportar la Capa 2. Es el
  argumento concreto para una fuente paga.
- **El `brief` no usa el cache.** Trae 5 años de balances y 60 precios en cada
  corrida. Es de a un ticker, así que molesta poco, pero está sin cachear.
- **La conversión de moneda pide un `FxProvider` explícito.** No hay tipo de cambio
  hardcodeado a propósito: quedaría viejo y mentiría en silencio.
- **No hay backtesting.** El screener dice cómo se ve una empresa hoy contra sus
  peers, no si ese criterio hubiera funcionado.
- Esto es una herramienta de estudio, no una recomendación de inversión.
