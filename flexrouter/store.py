"""Small-file persistence: atomic writes and owner-only permissions.

The service is the only writer of everything under the flexrouter home, so
file state with atomic replace is sufficient and correct — no external
datastore is needed (spec: Architecture Overview).
"""
from __future__ import annotations

import getpass
import json
import os
import subprocess
import tempfile
import warnings
from pathlib import Path
from typing import Any


def read_json(path: Path | str, default: Any = None) -> Any:
    """Read JSON, returning `default` ({} if unset) when absent or unreadable."""
    fallback: Any = {} if default is None else default
    p = Path(path)
    if not p.exists():
        return fallback
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return fallback


def write_json(path: Path | str, data: Any) -> None:
    """Write JSON so the whole file appears at once or nothing changes."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def harden(path: Path | str) -> None:
    """Restrict a file to the current user. Best effort; never raises."""
    p = Path(path)
    if not p.exists():
        return
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    if os.name != "nt":
        return
    user = os.environ.get("USERNAME")
    if not user:
        # Without a user name there is nobody to grant to, and the file would
        # silently keep whatever permissions it inherited from its folder.
        try:
            user = getpass.getuser()
        except Exception:
            user = ""
    if not user:
        warnings.warn(
            f"Could not work out which Windows account owns {p.name}, so its "
            f"permissions were left as they were. Anyone who can read the "
            f"folder it is in can read it.", RuntimeWarning, stacklevel=2)
        return
    try:
        subprocess.run(
            ["icacls", str(p), "/inheritance:r", "/grant:r", f"{user}:F"],
            capture_output=True, check=False, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        pass
