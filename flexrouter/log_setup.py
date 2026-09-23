"""The optional server activity log, behind `flexrouter dashboard --log`.

Nothing in flexrouter writes a log file by default - the dashboard already
shows every request. `--log` adds the server's own side of the story:
startup, routing warnings, provider errors, and uvicorn's HTTP lines, in
one rotating file (`flexrouter.log` in the state directory, 5 MB plus
three backups), which the dashboard then tails on its Logs page.

There is exactly one writer to the file. The handler is installed through
uvicorn's own logging config (`uvicorn_log_config`) rather than attached
by hand, for two reasons: uvicorn reconfigures its loggers when it starts
and would wipe a hand-attached handler, and two RotatingFileHandlers on
one file fight over rotation on Windows. flexrouter's own loggers are
named in that same config, so everything shares the single handler.
"""
from __future__ import annotations

import copy
import logging
from collections import deque
from pathlib import Path

LOG_FILENAME = "flexrouter.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 3
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"

_path: Path | None = None
_level: int = logging.INFO


def enable(state_dir: str | Path, level: int = logging.INFO) -> Path:
    """Turn file logging on and say where the file will be.

    The handler itself appears when uvicorn applies `uvicorn_log_config()`
    a moment later; this only creates the directory and records the state,
    which is what the Logs page and the menu check.
    """
    global _path, _level
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    _path = directory / LOG_FILENAME
    _level = level
    return _path


def disable() -> None:
    """Turn file logging off. Used by tests, between cases."""
    global _path, _level
    _path = None
    _level = logging.INFO


def enabled() -> bool:
    return _path is not None


def log_path() -> Path | None:
    return _path


def uvicorn_log_config() -> dict:
    """uvicorn's default logging config, plus the shared rotating file.

    Passed to `uvicorn.run(log_config=...)`. The file handler goes on
    `uvicorn` (which `uvicorn.error` propagates to) and on `uvicorn.access`
    (which does not propagate), and flexrouter's own loggers are routed to
    it as well.
    """
    from uvicorn.config import LOGGING_CONFIG

    if _path is None:
        raise RuntimeError("call enable() first")
    config = copy.deepcopy(LOGGING_CONFIG)
    config.setdefault("formatters", {})["flexrouter_file"] = {
        "format": FORMAT, "datefmt": DATEFMT}
    config.setdefault("handlers", {})["flexrouter_file"] = {
        "()": "logging.handlers.RotatingFileHandler",
        "filename": str(_path),
        "maxBytes": MAX_BYTES,
        "backupCount": BACKUPS,
        "encoding": "utf-8",
        "formatter": "flexrouter_file",
    }
    for name in ("uvicorn", "uvicorn.access"):
        config["loggers"][name].setdefault("handlers", []).append("flexrouter_file")
    config.setdefault("loggers", {})["flexrouter"] = {
        "level": logging.getLevelName(_level),
        "handlers": ["flexrouter_file"],
        "propagate": False,
    }
    return config


def tail(n: int = 200) -> list[str]:
    """The last `n` lines of the log file, oldest first.

    An empty list when logging is off or nothing has been written yet -
    both are normal states the Logs page renders as "nothing yet".
    """
    if _path is None or not _path.exists():
        return []
    with _path.open(encoding="utf-8", errors="replace") as f:
        return [line.rstrip("\n") for line in deque(f, maxlen=n)]
