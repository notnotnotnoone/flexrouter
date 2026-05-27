import time
from typing import Optional


class PenaltyBox:
    """Tracks per-(provider, model) exponential backoff penalties."""

    def __init__(self, base_seconds: int, max_seconds: int) -> None:
        self.base_seconds = base_seconds
        self.max_seconds = max_seconds
        # key -> (penalty_until: float, failure_count: int)
        self._state: dict[str, tuple[float, int]] = {}

    def _key(self, provider: str, model: str) -> str:
        return f"{provider}/{model}"

    def penalize(self, provider: str, model: str) -> None:
        k = self._key(provider, model)
        _, count = self._state.get(k, (0.0, 0))
        count += 1
        secs = min(self.base_seconds * (2 ** (count - 1)), self.max_seconds)
        self._state[k] = (time.monotonic() + secs, count)

    def penalize_short(self, provider: str, model: str, seconds: int) -> None:
        k = self._key(provider, model)
        _, count = self._state.get(k, (0.0, 0))
        self._state[k] = (time.monotonic() + seconds, count)

    def clear(self, provider: str, model: str) -> None:
        self._state.pop(self._key(provider, model), None)

    def is_penalized(self, provider: str, model: str) -> bool:
        k = self._key(provider, model)
        if k not in self._state:
            return False
        until, _ = self._state[k]
        if time.monotonic() >= until:
            del self._state[k]
            return False
        return True

    def penalty_seconds(self, provider: str, model: str) -> int:
        k = self._key(provider, model)
        if k not in self._state:
            return 0
        until, count = self._state[k]
        if time.monotonic() >= until:
            return 0
        return int(min(self.base_seconds * (2 ** (count - 1)), self.max_seconds))

    def penalty_until(self, provider: str, model: str) -> Optional[float]:
        k = self._key(provider, model)
        if k not in self._state:
            return None
        until, _ = self._state[k]
        return until if time.monotonic() < until else None
