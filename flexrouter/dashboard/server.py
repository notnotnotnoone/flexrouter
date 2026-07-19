from __future__ import annotations
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

from flexrouter.config import discover_config, load_config
from flexrouter.dashboard.api import (
    get_config, get_config_validation, get_health_current, get_last_refresh, get_logs,
    get_stats, get_status, get_uptime, post_config, run_refresh,
)

_router: Optional[object] = None
_cwd_normalized = False


def _find_config_upwards() -> Optional[Path]:
    """Walk from cwd up through parent directories looking for flexrouter.yaml.

    discover_config() only checks the exact cwd (then the home directory), so
    it misses the common case of the dashboard being started from a
    subdirectory of the project (e.g. `dashboard/frontend`) instead of the
    project root.
    """
    for parent in [Path.cwd(), *Path.cwd().parents]:
        candidate = parent / "flexrouter.yaml"
        if candidate.exists():
            return candidate
    return None


def _resolve_config_path() -> Optional[Path]:
    """Resolve the flexrouter.yaml a real caller would use.

    The dashboard is typically started as its own process (a new terminal, a
    service, a launcher script) whose working directory is not guaranteed to
    match the working directory of the app actually generating traffic. Real
    consumers commonly avoid this ambiguity by pointing at an explicit config
    path (e.g. via an env var such as FLEXROUTER_CONFIG). Mirror that so the
    dashboard doesn't silently fall back to a different, usually-empty
    state_dir. Resolution order:
      1. FLEXROUTER_CONFIG env var, if set and it exists
      2. discover_config()'s existing cwd / home-dir search
      3. Walking upward from cwd (covers being started from a subdirectory)
    """
    env_path = os.environ.get("FLEXROUTER_CONFIG")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    found = discover_config()
    if found:
        return found

    return _find_config_upwards()


def _normalize_cwd() -> None:
    """Move this process's cwd to the resolved config's directory (once).

    `state_dir` in flexrouter.yaml is typically a relative path, resolved
    relative to whatever the current process's cwd happens to be at the time
    (see AuditLogger/RateLimitStore/etc.). Without this, even after finding
    the right flexrouter.yaml, a relative state_dir would still resolve
    against the dashboard's own (possibly unrelated) cwd instead of the
    directory real traffic actually uses -- so router writes and stats reads
    would land in two different places. Normalizing cwd once, up front, keeps
    every relative-path consumer in this process consistent with each other
    and with a real caller who runs from the config's own directory (the
    documented, intended usage).
    """
    global _cwd_normalized
    if _cwd_normalized:
        return
    path = _resolve_config_path()
    if path:
        os.chdir(path.resolve().parent)
    _cwd_normalized = True


def _get_router():
    global _router
    if _router is None:
        from flexrouter._router import FlexRouter
        _normalize_cwd()
        _router = FlexRouter()
    return _router

STATIC_DIR = Path(__file__).parent / "static"


def _state_dir() -> str:
    _normalize_cwd()
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
            if self.path == "/v1/chat/completions":
                self._handle_chat()
                return
            if self.path == "/api/config":
                length = int(self.headers.get("Content-Length", 0))
                if length == 0:
                    self._send_json({"error": "empty body"}, 400)
                    return
                body = json.loads(self.rfile.read(length))
                post_config(body)
                self._send_json({"ok": True})
            elif self.path == "/api/refresh":
                self._send_json(run_refresh(_state_dir()))
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)

    def _handle_chat(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            messages = body.get("messages", [])
            model = body.get("model", "auto-default")
            tier = model.removeprefix("auto-") if model.startswith("auto-") else "default"
            router = _get_router()
            # fallback to first available tier if named tier doesn't exist
            available = list(router._cfg.tiers.keys())
            if tier not in available:
                tier = available[0] if available else "default"
            result = router.generate(messages, tier)
            self._send_json(result)
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
            elif self.path == "/api/health/current":
                self._send_json(get_health_current(state))
            elif self.path == "/api/refresh":
                self._send_json(get_last_refresh(state))
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
