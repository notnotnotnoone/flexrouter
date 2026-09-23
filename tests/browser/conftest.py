"""A real flexrouter server for the browser tests to drive.

Each test gets a fresh home and settings file (the root conftest isolates
FLEXROUTER_HOME), served by uvicorn on a free port in a background thread.
"""
import os
import socket
import threading
import time

import pytest
import uvicorn

from flexrouter.app import create_app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(config_file):
    port = _free_port()
    config = uvicorn.Config(create_app(str(config_file)), host="127.0.0.1", port=port,
                            log_level="warning")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not srv.started and time.time() < deadline:
        time.sleep(0.05)
    assert srv.started, "the server did not start"
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def errors(page):
    """Every JavaScript error the page raises; tests assert it stays empty."""
    seen = []
    page.on("pageerror", lambda exc: seen.append(str(exc)))
    page.on("console", lambda msg: seen.append(msg.text) if msg.type == "error" else None)
    return seen


# Headless by default; PWDEBUG=1 or HEADED=1 shows the browser.
@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    return {**browser_type_launch_args, "headless": not os.environ.get("HEADED")}
