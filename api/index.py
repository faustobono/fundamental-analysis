"""Entrada serverless para Vercel.

Vercel detecta una clase ``handler`` que hereda de ``BaseHTTPRequestHandler``.
El handler reutiliza las rutas de la web local y toma el proveedor desde
``BOT_PROVIDER`` (FMP en producción).
"""

from urllib.parse import parse_qs, urlencode, urlparse

from bot.web.server import ScreenerHandler


def public_path(request_path: str) -> str:
    """Restaura la ruta original que Vercel reescribe hacia esta función."""
    route = urlparse(request_path)
    query = parse_qs(route.query, keep_blank_values=True)
    original = query.pop("__path", [None])[0]
    if not original:
        return request_path

    # La regla de Vercel siempre agrega una ruta relativa al origen. Esta
    # validación evita que un valor malformado cambie el esquema del request.
    if not original.startswith("/"):
        original = f"/{original}"
    remaining = urlencode(query, doseq=True)
    return f"{original}?{remaining}" if remaining else original


class handler(ScreenerHandler):
    """Adaptador de las rutas existentes al runtime Python de Vercel."""

    def do_GET(self) -> None:  # noqa: N802 (nombre impuesto por el runtime)
        self.path = public_path(self.path)
        return super().do_GET()
