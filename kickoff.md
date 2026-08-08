# Kickoff

## Estado actual

Proyecto: bot de análisis fundamental en Python.

- La corrección de fuente FMP y la adaptación para Vercel están integradas en Git.
- La integración con Financial Modeling Prep (FMP) fue validada contra la API real usando AAPL.
- `yfinance` sigue siendo el proveedor por defecto.
- FMP funciona mediante `FMP_API_KEY` y se selecciona con `BOT_PROVIDER=fmp` o `--provider fmp`.
- La suite actual tiene `319 tests` y todos pasan sin red.
- Desplegado en Vercel: `https://fundscan.vercel.app` (el proyecto se
  renombró de `fundamental-analysis` a `fundscan`; el dominio viejo
  `fundamental-analysis-eight.vercel.app` puede seguir resolviendo por un
  tiempo pero no es el canónico).

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
