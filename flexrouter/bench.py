from __future__ import annotations

from flexrouter.dashboard.stats import compute_stats
from flexrouter.recovery import PenaltyBox

BENCH_SECONDS = 7 * 24 * 60 * 60

# How often the background pass re-checks response rates. Coarse on purpose:
# a week-long bench doesn't need near-real-time reaction, and this rereads
# the whole audit.csv each tick.
BENCH_INTERVAL_SECONDS = 10 * 60


def check_response_rates(state_dir: str, penalties: PenaltyBox, *,
                          min_requests: int = 10, threshold: float = 0.20,
                          bench_seconds: int = BENCH_SECONDS) -> list[tuple[str, str]]:
    """Quarantine any model that has answered fewer than `threshold` of its
    last `min_requests`+ requests, for `bench_seconds`.

    Reads the same per-model error rate the Models page already computes
    (`stats.compute_stats`), so this never disagrees with what the dashboard
    shows. A model already quarantined for any reason is left alone - this
    must not reset an existing bench's clock (or a provider-wide one) every
    time it runs.
    """
    stats = compute_stats(state_dir)
    benched: list[tuple[str, str]] = []
    for entry in stats["errors"]["per_model"]:
        requests = entry["requests"]
        if requests <= min_requests:
            continue
        response_rate = 1.0 - entry["rate"]
        if response_rate >= threshold:
            continue
        provider, _, model = entry["model"].partition("/")
        if penalties.is_quarantined(provider, model):
            continue
        reason = f"auto-benched: {response_rate:.0%} response rate over {requests} requests"
        penalties.quarantine(provider, model, reason, seconds=bench_seconds)
        benched.append((provider, model))
    return benched
