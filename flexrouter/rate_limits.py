from __future__ import annotations
import json
import time
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
            self._persist()

    def get_rpm(self, provider: str, model: str, default: int) -> int:
        return self._data.get(f"{provider}/{model}", {}).get("rpm", default)

    def get_tpm(self, provider: str, model: str, default: int) -> int:
        return self._data.get(f"{provider}/{model}", {}).get("tpm", default)

    def _persist(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2))

    def update_headroom(self, provider: str, model: str,
                        remaining_requests: int | None = None,
                        remaining_tokens: int | None = None,
                        reset_requests_at: float | None = None,
                        reset_tokens_at: float | None = None) -> None:
        if self._path is None:
            return
        key = f"{provider}/{model}"
        entry = dict(self._data.get(key, {}))
        for field_name, val in (
            ("remaining_requests", remaining_requests),
            ("remaining_tokens", remaining_tokens),
            ("reset_requests_at", reset_requests_at),
            ("reset_tokens_at", reset_tokens_at),
        ):
            if val is not None:
                entry[field_name] = val
        self._data[key] = entry
        self._persist()

    def _exhausted_resets(self, provider: str, model: str) -> list[float]:
        entry = self._data.get(f"{provider}/{model}", {})
        now = time.time()
        resets: list[float] = []
        for rem_key, reset_key in (("remaining_requests", "reset_requests_at"),
                                   ("remaining_tokens", "reset_tokens_at")):
            rem = entry.get(rem_key)
            reset = entry.get(reset_key)
            if rem is not None and rem <= 0 and reset is not None and now < reset:
                resets.append(reset)
        return resets

    def is_exhausted(self, provider: str, model: str) -> bool:
        return len(self._exhausted_resets(provider, model)) > 0

    def available_at(self, provider: str, model: str) -> float | None:
        resets = self._exhausted_resets(provider, model)
        return min(resets) if resets else None
