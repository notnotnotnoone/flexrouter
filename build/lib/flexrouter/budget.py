import datetime
from typing import Optional


class DailyBudget:
    """Tracks per-provider daily spend against configurable limits."""

    def __init__(self, limits: dict[str, float]) -> None:
        self._limits = limits
        self._spend: dict[str, float] = {}
        self._day: dict[str, str] = {}

    def record(self, provider: str, cost_usd: float) -> None:
        self._maybe_reset(provider)
        self._spend[provider] = self._spend.get(provider, 0.0) + cost_usd

    def is_available(self, provider: str) -> bool:
        if provider not in self._limits:
            return True
        self._maybe_reset(provider)
        return self._spend.get(provider, 0.0) < self._limits[provider]

    def daily_spend(self, provider: str) -> float:
        self._maybe_reset(provider)
        return self._spend.get(provider, 0.0)

    def _maybe_reset(self, provider: str) -> None:
        today = datetime.date.today().isoformat()
        if self._day.get(provider) != today:
            self._spend[provider] = 0.0
            self._day[provider] = today
