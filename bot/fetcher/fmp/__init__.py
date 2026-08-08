"""Proveedor Financial Modeling Prep (API con key, no scraper).

Adapter alternativo a yfinance, pensado para desplegar: una API con key sale de
una IP de datacenter sin que la bloqueen, cosa que el scrape de Yahoo no. Vive
detrás de la misma interfaz (`.fetch() -> FundamentalSnapshot`) y alimenta el
mismo `assemble_profile`, así que el resto del pipeline no lo distingue.

Free tier: 250 requests/día, 5 años de balances anuales de empresas US. La key
sale de `FMP_API_KEY`.
"""

from .adapter import FmpAdapter
from .client import FmpClient
from .deep import build_profile_fmp

__all__ = ["FmpAdapter", "FmpClient", "build_profile_fmp"]
