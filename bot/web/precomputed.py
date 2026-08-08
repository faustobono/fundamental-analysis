"""Ranking precalculado del universo grande, para servirlo sin gastar cuota.

El problema concreto: el screener gasta 4 requests de FMP por ticker (perfil +
income + balance + cash flow). Con ~100 tickers son ~400 requests **por carga
de página**, contra un free tier de 250/día — y como la web se auto-analiza al
abrir, el primer visitante quemaría la cuota del día entero.

La salida acá es el mismo payload que devuelve `/api/screen`, calculado una vez
y versionado en el repo. La web lo sirve tal cual: el front no distingue uno de
otro salvo por el sello de cuándo se generó.

Se genera **local con yfinance**, que no tiene cuota (`python -m bot precompute`).
No corre en Vercel: allá yfinance puede bloquear IPs de datacenter y FMP no
alcanza. Es un artefacto de build hecho a mano, no un job automático.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

#: Dónde vive el artefacto. Va versionado: es lo que Vercel despliega, y que
#: esté en git lo hace revisable (un diff muestra qué cambió entre refrescos).
DEFAULT_PATH = Path(__file__).parent / "data" / "top100.json"

#: Universo curado de las ~100 empresas más líquidas de EE.UU.
#:
#: **Es una lista curada a mano, no derivada del volumen diario.** Derivarla en
#: vivo exigiría un endpoint de "most actives" que el free tier no da, y además
#: la haría cambiar todos los días: el ranking dejaría de ser reproducible y
#: cada refresco mezclaría "cambió el fundamental" con "cambió el universo".
#:
#: El criterio de armado es cobertura sectorial, no sólo tamaño: el ranking es
#: intra-sector, así que un sector con menos de `min_peers` empresas queda sin
#: rankear. Por eso hay energía, utilities, materiales y real estate aunque no
#: sean los nombres de mayor volumen del mercado.
TOP_UNIVERSE: tuple[str, ...] = (
    # --- tecnología y semiconductores ---
    "AAPL", "MSFT", "NVDA", "AVGO", "AMD", "INTC", "QCOM", "TXN", "MU", "AMAT",
    "LRCX", "ADI", "CRM", "ORCL", "ADBE", "CSCO", "IBM", "NOW", "INTU", "PANW",
    # --- comunicación ---
    "GOOGL", "META", "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS",
    # --- consumo discrecional ---
    "AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "LOW", "TJX", "BKNG",
    # --- consumo defensivo ---
    "WMT", "COST", "PG", "KO", "PEP", "PM", "CL", "TGT",
    # --- financieras ---
    "BRK-B", "JPM", "BAC", "WFC", "GS", "MS", "C", "SCHW", "AXP", "BLK",
    "SPGI", "V", "MA",
    # --- salud ---
    "UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "TMO", "ABT", "DHR", "BMY",
    "AMGN", "GILD", "MDT", "ISRG",
    # --- energía ---
    "XOM", "CVX", "COP", "SLB", "EOG", "PSX", "MPC", "OXY",
    # --- industriales ---
    "CAT", "BA", "HON", "GE", "UPS", "RTX", "LMT", "DE", "UNP",
    # --- utilities ---
    "NEE", "DUK", "SO", "D",
    # --- materiales ---
    "LIN", "SHW", "FCX", "NEM",
    # --- real estate ---
    "AMT", "PLD", "SPG",
)


def stamp(payload: dict[str, Any], *, universe_size: int) -> dict[str, Any]:
    """Marca el payload como precalculado y le pone fecha de generación.

    Va dentro de `meta` y no al lado, para que el front reciba exactamente la
    misma forma que `/api/screen` y pueda renderizarlo sin ramificar.
    """
    payload["meta"]["precomputed"] = True
    payload["meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    payload["meta"]["universe_size"] = universe_size
    return payload


def save(payload: dict[str, Any], path: Optional[Path] = None) -> Path:
    destination = Path(path) if path else DEFAULT_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return destination


def load(path: Optional[Path] = None) -> Optional[dict[str, Any]]:
    """El payload precalculado, o None si todavía no se generó.

    None no es un error del servidor: es "corré `python -m bot precompute`".
    Quien llama decide cómo comunicarlo.
    """
    source = Path(path) if path else DEFAULT_PATH
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
