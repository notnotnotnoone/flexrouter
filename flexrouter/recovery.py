from __future__ import annotations
import json
import os
import time
from pathlib import Path
from typing import Callable, Optional


QUARANTINE_SECONDS = 24 * 60 * 60

# Model slot used for a provider-wide quarantine. No real model id is "*".
PROVIDER_WILDCARD = "*"


class PenaltyBox:
    """Tracks per-(provider, model) exponential backoff penalties.

    Uses wall-clock time so penalties survive process restart when a
    state_dir is given. Emits 'penalized'/'recovered' events via on_event.
    """

    def __init__(self, base_seconds: int, max_seconds: int,
                 state_dir: Optional[str] = None,
                 on_event: Optional[Callable[[str, str, str, int], None]] = None) -> None:
        self.base_seconds = base_seconds
        self.max_seconds = max_seconds
        self._on_event = on_event
        self._path = Path(state_dir) / "penalties.json" if state_dir else None
        self._quarantine_path = Path(state_dir) / "quarantine.json" if state_dir else None
        # key -> (until: epoch float, count: int)
        self._state: dict[str, tuple[float, int]] = {}
        # key -> {"until": epoch float, "reason": str}
        self._quarantine: dict[str, dict] = {}
        self._load()
        self._load_quarantine()

    def _key(self, provider: str, model: str) -> str:
        return f"{provider}/{model}"

    def _split(self, key: str) -> tuple[str, str]:
        provider, _, model = key.partition("/")
        return provider, model

    def _load(self) -> None:
        if not self._path or not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except Exception:
            return
        now = time.time()
        for k, v in raw.items():
            until, count = float(v["until"]), int(v["count"])
            if until > now:  # drop already-expired penalties
                self._state[k] = (until, count)

    def _load_quarantine(self) -> None:
        if not self._quarantine_path or not self._quarantine_path.exists():
            return
        try:
            raw = json.loads(self._quarantine_path.read_text())
        except Exception:
            return
        now = time.time()
        for k, v in raw.items():
            if float(v["until"]) > now:
                self._quarantine[k] = {"until": float(v["until"]), "reason": str(v.get("reason", ""))}

    def _save_quarantine(self) -> None:
        if not self._quarantine_path:
            return
        tmp = self._quarantine_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._quarantine))
        os.replace(tmp, self._quarantine_path)

    def _save(self) -> None:
        if not self._path:
            return
        data = {k: {"until": u, "count": c} for k, (u, c) in self._state.items()}
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, self._path)

    def _emit(self, provider: str, model: str, event_type: str, secs: int) -> None:
        if self._on_event:
            self._on_event(provider, model, event_type, secs)

    def penalize(self, provider: str, model: str) -> None:
        k = self._key(provider, model)
        _, count = self._state.get(k, (0.0, 0))
        count += 1
        secs = min(self.base_seconds * (2 ** (count - 1)), self.max_seconds)
        self._state[k] = (time.time() + secs, count)
        self._save()
        self._emit(provider, model, "penalized", int(secs))

    def penalize_short(self, provider: str, model: str, seconds: int) -> None:
        k = self._key(provider, model)
        _, count = self._state.get(k, (0.0, 0))
        self._state[k] = (time.time() + seconds, count)
        self._save()
        self._emit(provider, model, "penalized", int(seconds))

    def quarantine(self, provider: str, model: str, reason: str,
                   seconds: int = QUARANTINE_SECONDS) -> None:
        """Sideline a route that answered with a permanent failure.

        Distinct from penalize() so the dashboard can say "the provider says
        this model is gone" instead of showing a backoff countdown that will
        never lead anywhere. Still time-boxed rather than forever, so a
        provider that 404s by mistake heals itself without manual cleanup.
        """
        k = self._key(provider, model)
        self._quarantine[k] = {"until": time.time() + seconds, "reason": reason}
        self._save_quarantine()
        self._emit(provider, model, "quarantined", int(seconds))

    def quarantine_provider(self, provider: str, reason: str,
                            seconds: int = QUARANTINE_SECONDS) -> None:
        """Sideline every model on a provider at once.

        A rejected API key is a fact about the provider, not about one model —
        every route through it will fail identically. Without this, a single
        expired key makes each request march through all that provider's
        models collecting the same 401 before giving up.
        """
        self.quarantine(provider, PROVIDER_WILDCARD, reason, seconds)

    def _live(self, key: str) -> bool:
        entry = self._quarantine.get(key)
        if entry is None:
            return False
        if time.time() >= entry["until"]:
            del self._quarantine[key]
            self._save_quarantine()
            self._emit(*self._split(key), "recovered", 0)
            return False
        return True

    def is_quarantined(self, provider: str, model: str) -> bool:
        if model != PROVIDER_WILDCARD and self._live(self._key(provider, PROVIDER_WILDCARD)):
            return True
        return self._live(self._key(provider, model))

    def quarantine_reason(self, provider: str, model: str) -> Optional[str]:
        for key in (self._key(provider, model),
                    self._key(provider, PROVIDER_WILDCARD)):
            entry = self._quarantine.get(key)
            if entry:
                return entry["reason"]
        return None

    def clear_quarantine(self, provider: str, model: str) -> None:
        if self._quarantine.pop(self._key(provider, model), None) is not None:
            self._save_quarantine()
            self._emit(provider, model, "recovered", 0)

    def quarantined(self) -> dict[str, dict]:
        now = time.time()
        return {k: dict(v) for k, v in self._quarantine.items() if v["until"] > now}

    def clear(self, provider: str, model: str) -> None:
        if self._state.pop(self._key(provider, model), None) is not None:
            self._save()
            self._emit(provider, model, "recovered", 0)

    def is_penalized(self, provider: str, model: str) -> bool:
        if self.is_quarantined(provider, model):
            return True
        k = self._key(provider, model)
        if k not in self._state:
            return False
        until, _ = self._state[k]
        if time.time() >= until:
            del self._state[k]
            self._save()
            self._emit(*self._split(k), "recovered", 0)
            return False
        return True

    def penalty_seconds(self, provider: str, model: str) -> int:
        k = self._key(provider, model)
        if k not in self._state:
            return 0
        until, count = self._state[k]
        if time.time() >= until:
            return 0
        return int(min(self.base_seconds * (2 ** (count - 1)), self.max_seconds))

    def penalty_until(self, provider: str, model: str) -> Optional[float]:
        k = self._key(provider, model)
        if k not in self._state:
            return None
        until, _ = self._state[k]
        return until if time.time() < until else None

    def active_penalties(self) -> dict[str, dict]:
        now = time.time()
        return {k: {"until": u, "count": c}
                for k, (u, c) in self._state.items() if u > now}
