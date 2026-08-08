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
