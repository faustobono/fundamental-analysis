# Instrucciones para agentes

Este archivo es el contexto operativo compartido para Claude Code, GPT y OpenCode.

## Antes de modificar

1. Leer `kickoff.md`, `README.md`, `DECISIONS.md` y `TODO.md`.
2. Revisar `git status`, `git log --oneline -5` y los cambios existentes.
3. No eliminar ni revertir cambios hechos por otro agente.
4. Confirmar el alcance de la tarea antes de tocar código.

## Reglas del proyecto

- Mantener la separación entre fetcher, normalizer, scorer, analysis, brief y web.
- Los proveedores deben producir el mismo modelo común (`FundamentalSnapshot` o
  `CompanyProfile`) para que el resto del pipeline no dependa del proveedor.
- Calcular ratios desde datos contables crudos; no copiar ratios precalculados sin
  verificar unidades y significado.
- No imputar datos faltantes ni inventar valores plausibles. Usar `None` y warnings.
- Mantener el ranking intra-sector.
- No agregar dependencias nuevas sin justificarlo.
- No calcular WACC sin supuestos explícitos de mercado.
- No guardar API keys en código, documentación, fixtures, logs o commits.

## Pruebas obligatorias

Después de modificar Python:

```bash
.venv/bin/python -m pytest
```

Los tests deben seguir funcionando sin red. Las llamadas reales a FMP solo se hacen
manualmente como smoke-test y nunca dentro de la suite.

## Smoke-tests manuales

```bash
FMP_API_KEY=tu_key .venv/bin/python -m bot brief AAPL --provider fmp --data-only
FMP_API_KEY=tu_key .venv/bin/python -m bot screener --provider fmp --tickers AAPL,MSFT,GOOGL
```

## Al terminar una tarea

- Ejecutar las pruebas relevantes y anotar el resultado.
- Actualizar `TODO.md` si cambió el estado de una tarea.
- Actualizar `DECISIONS.md` si se tomó una decisión de diseño.
- Actualizar `kickoff.md` si cambió el punto de reanudación.
- Informar archivos modificados, pruebas ejecutadas y pendientes.
- No hacer commit, push o deploy salvo que el usuario lo pida explícitamente.

## Prioridad de documentación

- `README.md`: uso público y explicación del producto.
- `kickoff.md`: estado de reanudación de la sesión actual.
- `TODO.md`: trabajo pendiente priorizado.
- `DECISIONS.md`: decisiones permanentes y sus motivos.
- `AGENTS.md`: reglas compartidas para agentes.
