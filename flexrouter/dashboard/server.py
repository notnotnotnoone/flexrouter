from __future__ import annotations
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from flexrouter.config import discover_config, load_config
from flexrouter.dashboard.api import get_config, get_logs, get_status, post_config

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
        if self.path == "/api/config":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            post_config(body)
            self._send_json({"ok": True})
        else:
            self._send_json({"error": "not found"}, 404)

    def _handle_api_get(self):
        state = _state_dir()
        if self.path == "/api/status":
            self._send_json(get_status(state))
        elif self.path.startswith("/api/logs"):
            self._send_json(get_logs(state))
        elif self.path == "/api/config":
            self._send_json(get_config())
        else:
            self._send_json({"error": "not found"}, 404)

    def _serve_static(self):
        index = STATIC_DIR / "index.html"
        if not index.exists():
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"Dashboard not built. Run: cd dashboard/frontend && npm run build")
            return
        mime, _ = mimetypes.guess_type(str(index))
        body = index.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime or "text/html")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)


def start_server(port: int = 7352, open_tab: bool = True) -> None:
    server = HTTPServer(("127.0.0.1", port), _Handler)
    server.serve_forever()
