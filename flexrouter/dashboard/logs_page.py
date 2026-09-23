"""The Logs page: the server's own activity log, newest first.

Only reachable when the server was started with `--log`; without it the
route answers 404 and the menu shows no link, so the dashboard looks
exactly as it always did. The log itself is flexrouter.log in the state
directory (see flexrouter/log_setup.py) - startup lines, routing warnings,
provider errors, and uvicorn's HTTP access lines, tailed live.

Like the Requests page, the live block re-fetches itself and morphs in
place, so new lines slide in at the top without the page moving.
"""
from __future__ import annotations

import re

from flexrouter import log_setup
from flexrouter.dashboard import prefs, ui
from flexrouter.dashboard.render import esc, tag

PAGE = 200

# One line as log_setup's FORMAT writes it: "2026-09-23 14:03:22 INFO name:
# message". An explicit datefmt drops the ",mmm" millis, so they are optional
# here. Lines that do not match at all (traceback continuations, an older
# format) are shown plain rather than dropped.
_LINE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d+)?) (\w+) ([\w.\-]+): (.*)$")

_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def _row(line: str) -> str:
    match = _LINE.match(line)
    if match is None:
        return tag("div", tag("span", esc(line), cls="log-msg"), cls="log-row")
    when, level, name, message = match.groups()
    level = level.upper() if level.upper() in _LEVELS else "INFO"
    return tag("div", "".join([
        tag("span", esc(when[11:19]), cls="log-time mono dim"),
        tag("span", esc(level), cls=f"log-level log-level-{level.lower()} mono"),
        tag("span", esc(name), cls="log-name mono faint"),
        tag("span", esc(message), cls="log-msg"),
    ]), cls="log-row", **{"data-level": level})


def rows(limit: int = PAGE) -> str:
    """The live block: the last `limit` lines, newest first."""
    lines = log_setup.tail(limit)
    if not lines:
        inner = ui.empty("Nothing logged yet. Lines appear here as the server "
                         "writes them.")
    else:
        inner = tag("div", "".join(_row(line) for line in reversed(lines)),
                    cls="scroll log-lines")
    refresh = max(5, prefs.load().refresh_seconds // 2)
    return tag("div", inner, id="log-rows", cls="box flush",
               **{"data-live": "", "hx-get": f"/logs?fragment=1&limit={limit}",
                  "hx-trigger": f"every {refresh}s [!document.hidden]",
                  "hx-swap": "morph:innerHTML", "data-enter": ""})


def body(limit: int = PAGE) -> str:
    path = log_setup.log_path()
    status = (f"Tailing the last {limit} lines of {path}" if path
              else "The server's own activity log, newest first.")
    head = tag("div",
               tag("div", tag("h1", "Logs", cls="page-title")
                   + tag("p", esc(status), cls="page-status"), cls="page-head-text")
               + tag("div", tag("span", tag("span", "", cls="live-dot") + "live",
                                cls="live"),
                     cls="page-actions"),
               cls="page-head")
    return head + rows(limit)
