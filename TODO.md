# Trabajo pendiente

## Alta prioridad

- [x] Elegir hosting: Vercel.
- [x] Preparar entry point y rutas para Vercel.
- [x] Vincular el proyecto local con Vercel (`faustobonos-projects/fundamental-analysis`).
- [x] Configurar `BOT_PROVIDER=fmp` y `FMP_API_KEY` en Vercel.
- [x] Hacer un deploy de prueba y verificar `/` y `/api/health`.
- [x] Reemplazar la `FMP_API_KEY` de Vercel por una key FMP activa.
- [x] Repetir los smoke-tests de screener y brief con FMP en producción.
- [x] Probar la web completa con `BOT_PROVIDER=fmp`.

## Media prioridad

- [x] Agregar botones "i" de info en la web (screener + brief) explicando cada
      métrica, % y estadística — ROIC, márgenes, múltiplos, percentil vs.
      z-score, cobertura, pocos peers, CEDEAR/monedas mixtas, WACC omitido,
      etc. Glosario estático en `bot/web/static/glossary.js` (33+ entradas),
      componente reusable en `info.js` (popover con click-delegation, no un
      listener por botón). Verificado en navegador: 302 tests Python sin
      cambios (es una feature 100% frontend), sin errores de consola, dark
      mode y mobile (375px) OK. Bug real encontrado y corregido en el camino:
      el popover se cerraba solo ante cualquier scroll (incluso el
      scroll-into-view de un click automatizado) — ahora reposiciona en vez de
      cerrar. Sin commitear todavía.

- [x] Corregir `gaps()` de `CompanyProfile`: acusaba a yfinance de no publicar
      segmentos incluso corriendo con `--provider fmp` (mensaje hardcodeado,
      reproducido contra producción con AAPL). Se agregó `CompanyProfile.provider`
      y se usa tanto en `gaps()` como en `render_data_block` para declarar la
      fuente real. `snapshot.source` no servía para esto: se pisa a `"byma"` en
      el screener (vía `BymaAdapter`), aunque el brief no pasa por ahí hoy.
- [ ] Probar el screener con un universo suficientemente grande por sector.
- [ ] Verificar el comportamiento de CEDEARs/ADRs usando FMP y `cedear_map.json`.
- [ ] Confirmar límites, errores y consumo del free tier de FMP en el hosting elegido.
- [ ] Documentar la URL y el procedimiento de actualización una vez publicado.

## Baja prioridad

- [ ] Evaluar cache para el comando `brief`.
- [ ] Evaluar una fuente de segmentos de ingresos.
- [ ] Evaluar backtesting del ranking.
- [ ] Evaluar soporte para más de cinco ejercicios si una fuente futura lo permite.

## Hecho

- [x] Integrar FMP como proveedor alternativo.
- [x] Agregar selección por `--provider` y `BOT_PROVIDER`.
- [x] Agregar aliases tolerantes para campos FMP.
- [x] Cubrir cliente, adapter, historial y valuación FMP con fixtures.
- [x] Validar FMP contra la API real usando AAPL.
- [x] Corregir la fuente declarada en el informe para que sea dinámica.
- [x] Actualizar README y documentación de reanudación.
- [x] Preparar el runtime Python y las rutas para Vercel.
- [x] Desplegar `fundamental-analysis` en Vercel con FMP.
- [x] Committear la corrección de fuente FMP y la adaptación para Vercel.
