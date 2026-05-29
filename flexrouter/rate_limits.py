from __future__ import annotations
import json
from pathlib import Path


class RateLimitStore:
    def __init__(self, state_dir: str | None) -> None:
        self._path = Path(state_dir) / "rate_limits.json" if state_dir else None
        self._data: dict = {}
        if self._path and self._path.exists():
            try:
                self._data = json.loads(self._path.read_text())
            except (json.JSONDecodeError, ValueError, OSError):
                pass

    def update(self, provider: str, model: str, rpm: int | None, tpm: int | None) -> None:
        if self._path is None:
            return
        if (rpm is None or rpm <= 0) and (tpm is None or tpm <= 0):
            return
        key = f"{provider}/{model}"
        entry = dict(self._data.get(key, {}))
        changed = False
        if rpm is not None and rpm > 0 and entry.get("rpm") != rpm:
            entry["rpm"] = rpm
            changed = True
        if tpm is not None and tpm > 0 and entry.get("tpm") != tpm:
            entry["tpm"] = tpm
            changed = True
        if changed:
            self._data[key] = entry
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._data, indent=2))

    def get_rpm(self, provider: str, model: str, default: int) -> int:
        return self._data.get(f"{provider}/{model}", {}).get("rpm", default)

    def get_tpm(self, provider: str, model: str, default: int) -> int:
        return self._data.get(f"{provider}/{model}", {}).get("tpm", default)
