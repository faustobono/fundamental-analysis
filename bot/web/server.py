"""Servidor local del screener, sobre `http.server` de la stdlib.

Sin framework a propósito: es una herramienta de un solo usuario en localhost,
y no justifica sumar dependencias a un proyecto que hoy sólo necesita yfinance
y pandas.

`ThreadingHTTPServer` porque un fetch a yfinance bloquea varios segundos y no
puede congelar el resto de la página mientras tanto.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..cli.main import parse_metrics
from ..models import FetchError
from ..scorer.metrics import Method
from .api import parse_tickers, run_screen

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

#: Allowlist explícita en vez de servir el directorio: elimina de raíz cualquier
#: path traversal, sin tener que confiar en normalizar la ruta que llega.
STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/app.js": "app.js",
    "/brief.js": "brief.js",
    "/format.js": "format.js",
    "/tabs.js": "tabs.js",
    "/styles.css": "styles.css",
}

#: Tope defensivo: el universo lo escribe una persona en un textarea, no un
#: proceso. Sin esto, un pegado accidental dispara cientos de fetches.
MAX_TICKERS = 150


class ScreenerHandler(BaseHTTPRequestHandler):
    server_version = "fundamental-bot"

    # --- ruteo -------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (nombre impuesto por BaseHTTPRequestHandler)
        route = urlparse(self.path)
        path = route.path

        if path in STATIC_FILES:
            return self._serve_static(STATIC_FILES[path])
        if path == "/api/screen":
            return self._serve_screen(parse_qs(route.query))
        if path == "/api/brief":
            return self._serve_brief(parse_qs(route.query))
        if path == "/api/health":
            return self._send_json({"status": "ok"})
        return self._send_error(HTTPStatus.NOT_FOUND, f"no existe {path}")

    # --- endpoints ---------------------------------------------------------

    def _serve_screen(self, query: dict[str, list[str]]) -> None:
        def first(key: str, default: str = "") -> str:
            values = query.get(key)
            return values[0] if values else default

        tickers = parse_tickers(first("tickers"))
        if not tickers:
            return self._send_error(HTTPStatus.BAD_REQUEST, "no mandaste ningún ticker")
        if len(tickers) > MAX_TICKERS:
            return self._send_error(
                HTTPStatus.BAD_REQUEST,
                f"{len(tickers)} tickers supera el máximo de {MAX_TICKERS}",
            )

        try:
            method = Method(first("method", Method.PERCENTILE.value))
            metrics = parse_metrics(first("metrics") or None)
            min_peers = int(first("min_peers", "3"))
            min_metrics = int(first("min_metrics", "2"))
            use_cache = first("cache", "1") != "0"
        except ValueError as exc:
            return self._send_error(HTTPStatus.BAD_REQUEST, f"parámetro inválido: {exc}")

        provider = self.server.provider  # type: ignore[attr-defined]
        logger.info(
            "screen: %d ticker(s), provider=%s, method=%s, cache=%s",
            len(tickers), provider, method.value, use_cache,
        )
        try:
            payload = run_screen(
                tickers,
                metrics=metrics,
                method=method,
                min_peers=min_peers,
                min_metrics=min_metrics,
                use_cache=use_cache,
                provider=provider,
            )
        except Exception as exc:  # noqa: BLE001
            # El batch ya tolera fallas por ticker; si explota acá es un bug
            # nuestro. Devolvemos 500 con stacktrace en el log del servidor.
            logger.exception("screen falló")
            return self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, f"error interno: {exc}")

        self._send_json(payload)

    def _serve_brief(self, query: dict[str, list[str]]) -> None:
        def first(key: str, default: str = "") -> str:
            values = query.get(key)
            return values[0] if values else default

        ticker = first("ticker").strip()
        if not ticker:
            return self._send_error(HTTPStatus.BAD_REQUEST, "no mandaste ningún ticker")

        try:
            years = int(first("years", "5"))
        except ValueError as exc:
            return self._send_error(HTTPStatus.BAD_REQUEST, f"parámetro inválido: {exc}")

        # Import diferido: traer 5 años de balances y precios es mucho más caro
        # que el screener, y esta ruta no se pega en cada arranque del server.
        from .brief_api import run_brief

        provider = self.server.provider  # type: ignore[attr-defined]
        logger.info("brief: %s (%d años, provider=%s)", ticker, years, provider)
        try:
            payload = run_brief(ticker, years=years, provider=provider)
        except FetchError as exc:
            # Ticker inexistente o CEDEAR sin subyacente mapeado: culpa del
            # input, no del servidor.
            return self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("brief falló")
            return self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, f"error interno: {exc}")

        self._send_json(payload)

    def _serve_static(self, filename: str) -> None:
        path = STATIC_DIR / filename
        try:
            body = path.read_bytes()
        except OSError:
            return self._send_error(HTTPStatus.NOT_FOUND, f"falta el archivo {filename}")

        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        if content_type.startswith(("text/", "application/javascript")):
            content_type += "; charset=utf-8"
        self._send(HTTPStatus.OK, content_type, body, cache=False)

    # --- respuestas --------------------------------------------------------

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", body)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message}, status)

    def _send(self, status: HTTPStatus, content_type: str, body: bytes, cache: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if not cache:
            # Sin esto hay que hacer hard-refresh cada vez que se toca el CSS.
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            # El usuario cerró la pestaña con un fetch en curso. Normal.
            logger.debug("cliente desconectado durante la respuesta")

    def log_message(self, format: str, *args) -> None:
        # El default escribe a stderr sin formato; lo mandamos al logger.
        logger.debug("%s - %s", self.address_string(), format % args)


def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    open_browser: bool = True,
    provider: str = "yfinance",
) -> None:
    """Levanta el servidor. Bloquea hasta Ctrl-C."""
    httpd = ThreadingHTTPServer((host, port), ScreenerHandler)
    # El handler se instancia por request; el provider vive en el server y el
    # handler lo lee de ahí (self.server.provider).
    httpd.provider = provider  # type: ignore[attr-defined]
    url = f"http://{host}:{port}"

    print(f"\n  Screener en {url}  (datos: {provider})")
    print("  Ctrl-C para cortar\n")

    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  cortando...")
    finally:
        httpd.shutdown()
        httpd.server_close()
