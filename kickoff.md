# Kickoff

## Estado actual

Proyecto: bot de análisis fundamental en Python.

- La corrección de fuente FMP y la adaptación para Vercel están integradas en Git.
- La integración con Financial Modeling Prep (FMP) fue validada contra la API real usando AAPL.
- `yfinance` sigue siendo el proveedor por defecto.
- FMP funciona mediante `FMP_API_KEY` y se selecciona con `BOT_PROVIDER=fmp` o `--provider fmp`.
- La suite actual tiene `298 tests` y todos pasan sin red.
- Desplegado en Vercel: `https://fundamental-analysis-eight.vercel.app`.

## Última integración

El último bloque de cambios incluye la corrección de la fuente declarada por el
brief, el runtime de Vercel, la configuración de rutas, el cache efímero de
serverless y la documentación operativa. La producción fue validada con FMP.

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
