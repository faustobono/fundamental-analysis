"""Cliente HTTP de Financial Modeling Prep.

Sobre `urllib` de la stdlib: no suma dependencias a un proyecto que hoy sólo
necesita pandas. El transporte (`http_get`) es inyectable —igual que la
`ticker_factory` de yfinance—, así que todo el adapter se testea con JSON
enlatado sin tocar la red.

FMP movió su API de `/api/v3/` a `/stable/` (con parámetros de query en vez de
path) y renombró campos en el camino (`mktCap`→`marketCap`,
`epsdiluted`→`epsDiluted`). Se apunta a `/stable/`; los renames se absorben con
lookup por alias en `fields.py`.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional

from ...models import NoDataError, UpstreamError

logger = logging.getLogger(__name__)

BASE_URL = "https://financialmodelingprep.com/stable"

#: Cuánto espera una request antes de rendirse. FMP responde rápido; si tarda
#: más que esto probablemente esté caído.
DEFAULT_TIMEOUT = 15.0

#: `http_get(url) -> texto`. Se inyecta en los tests.
HttpGet = Callable[[str], str]


def _default_http_get(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "fundamental-bot"})
    with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
        return response.read().decode("utf-8")


class FmpClient:
    """Pega a los endpoints de FMP y devuelve JSON ya parseado.

    La API key sale del argumento o de `FMP_API_KEY`. No se hardcodea nunca: es
    un secreto, y además lo único que cambia entre correr local y desplegar.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: str = BASE_URL,
        http_get: Optional[HttpGet] = None,
    ):
        self._api_key = api_key or os.environ.get("FMP_API_KEY")
        self._base_url = base_url.rstrip("/")
        self._http_get = http_get or _default_http_get

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def get(self, endpoint: str, *, ticker: Optional[str] = None, **params: Any) -> Any:
        """GET a `endpoint` con la key ya puesta. Devuelve lista o dict.

        `ticker` es sólo para el mensaje de error: identifica de quién es la
        request cuando falla, sin ensuciar la firma con un símbolo que a veces
        va como parámetro y a veces no.
        """
        if not self._api_key:
            raise UpstreamError(
                ticker or endpoint,
                "falta la API key de FMP: definí FMP_API_KEY o pasala al cliente",
            )

        query = {k: v for k, v in params.items() if v is not None}
        query["apikey"] = self._api_key
        url = f"{self._base_url}/{endpoint.lstrip('/')}?{urllib.parse.urlencode(query)}"

        try:
            body = self._http_get(url)
        except urllib.error.HTTPError as exc:
            # 401/403 = key inválida; 429 = límite diario (250/día en free).
            hint = {
                401: "API key inválida",
                403: "acceso denegado (¿key sin permiso para este endpoint?)",
                429: "límite de requests alcanzado (250/día en el free tier)",
            }.get(exc.code, f"HTTP {exc.code}")
            raise UpstreamError(ticker or endpoint, f"FMP: {hint}") from exc
        except urllib.error.URLError as exc:
            raise UpstreamError(ticker or endpoint, f"no se pudo conectar con FMP: {exc.reason}") from exc

        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise UpstreamError(ticker or endpoint, f"FMP devolvió algo que no es JSON: {exc}") from exc

        # FMP no usa códigos HTTP para los errores de negocio: manda 200 con un
        # objeto {"Error Message": "..."} cuando la key es mala o se acabó la
        # cuota. Hay que mirar el cuerpo.
        if isinstance(data, dict) and "Error Message" in data:
            message = data["Error Message"]
            if "limit" in message.lower():
                raise UpstreamError(ticker or endpoint, f"FMP: límite alcanzado — {message}")
            raise UpstreamError(ticker or endpoint, f"FMP: {message}")

        return data

    def get_one(self, endpoint: str, *, ticker: Optional[str] = None, **params: Any) -> Optional[dict[str, Any]]:
        """Para endpoints que devuelven una lista de un solo elemento (perfil).

        Devuelve el primer objeto o None si la lista vino vacía —un ticker que
        FMP no cubre—, sin que eso sea un error todavía.
        """
        data = self.get(endpoint, ticker=ticker, **params)
        if isinstance(data, list):
            return data[0] if data else None
        if isinstance(data, dict):
            return data
        raise NoDataError(ticker or endpoint, f"forma inesperada en {endpoint}: {type(data).__name__}")

    def get_list(self, endpoint: str, *, ticker: Optional[str] = None, **params: Any) -> list[dict[str, Any]]:
        """Para endpoints que devuelven una lista (statements, precios)."""
        data = self.get(endpoint, ticker=ticker, **params)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        raise NoDataError(ticker or endpoint, f"forma inesperada en {endpoint}: {type(data).__name__}")
