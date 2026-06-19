from __future__ import annotations
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from flexrouter.config import discover_config, load_config
from flexrouter.dashboard.api import (
    get_config, get_config_validation, get_logs, get_stats, get_status, get_uptime, post_config,
)

STATIC_DIR = Path(__file__).parent / "static"


def _state_dir() -> str:
    path = discover_config()
    if path:
        try:
            return load_config(path).state_dir
        except Exception:
            pass
    return ".flexrouter"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send_json(self, data: dict | list, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/api/"):
            self._handle_api_get()
        else:
            self._serve_static()

    def do_POST(self):
        try:
            if self.path == "/api/config":
                length = int(self.headers.get("Content-Length", 0))
                if length == 0:
                    self._send_json({"error": "empty body"}, 400)
                    return
                body = json.loads(self.rfile.read(length))
                post_config(body)
                self._send_json({"ok": True})
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)

    def _handle_api_get(self):
        try:
            state = _state_dir()
            if self.path == "/api/status":
                self._send_json(get_status(state))
            elif self.path.startswith("/api/logs"):
                self._send_json(get_logs(state))
            elif self.path == "/api/config":
                self._send_json(get_config())
            elif self.path == "/api/stats":
                self._send_json(get_stats(state))
            elif self.path == "/api/uptime":
                self._send_json(get_uptime(state))
            elif self.path == "/api/config/validate":
                self._send_json(get_config_validation())
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)

    def _serve_static(self):
        # Strip query string
        path = self.path.split("?")[0].lstrip("/") or "index.html"
        candidate = STATIC_DIR / path
        # Serve real asset if it exists, else SPA fallback to index.html
        target = candidate if candidate.exists() and candidate.is_file() else STATIC_DIR / "index.html"
        if not target.exists():
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"Dashboard not built. Run: cd dashboard/frontend && npm run build")
            return
        mime, _ = mimetypes.guess_type(str(target))
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime or "text/html")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)


def start_server(port: int = 7352, open_tab: bool = True) -> None:
    server = HTTPServer(("127.0.0.1", port), _Handler)
    server.serve_forever()
