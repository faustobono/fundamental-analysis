"""Configuración por entorno.

Lo único que cambia entre correr local y desplegar: qué proveedor de datos usar.
En local, yfinance sale gratis y sin key. En la nube, yfinance se bloquea (IP de
datacenter), así que se pone `BOT_PROVIDER=fmp` + `FMP_API_KEY` y listo.
"""

from __future__ import annotations

import os

from .fetcher.service import DEFAULT_PROVIDER, PROVIDERS


def default_provider() -> str:
    """Proveedor de `BOT_PROVIDER`, o yfinance. Valida contra los conocidos."""
    provider = os.environ.get("BOT_PROVIDER", DEFAULT_PROVIDER).strip().lower()
    if provider not in PROVIDERS:
        raise ValueError(
            f"BOT_PROVIDER={provider!r} no es válido (opciones: {', '.join(PROVIDERS)})"
        )
    return provider
