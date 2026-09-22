from __future__ import annotations
import csv
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _hour_key(ts: str) -> str:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    return dt.replace(minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


def compute_stats(state_dir: str) -> dict:
    path = Path(state_dir) / "audit.csv"
    rows: list[dict] = []
    if path.exists():
        with path.open() as f:
            rows = list(csv.DictReader(f))

    empty = {
        "totals": {"requests": 0, "this_hour": 0, "peak_rpm": 0},
        "hourly": [], "latency": {"per_model": [], "histogram": []},
        "distribution": {"by_provider": [], "top_models": [], "diversity": 0.0},
        "errors": {"rate_by_hour": [], "by_type": [], "per_model": []},
        "tiers": {"by_tier": []},
    }
    if not rows:
        return empty

    by_hour = Counter()
    by_hour_err = Counter()
    lat_by_model = defaultdict(list)
    req_by_model = Counter()
    err_by_model = Counter()
    by_provider = Counter()
    by_status = Counter()
    by_tier = Counter()
    all_latencies: list[float] = []

    for r in rows:
        model = f"{r['provider']}/{r['model']}"
        status = r["status"]
        hour = _hour_key(r["timestamp"])
        by_hour[hour] += 1
        by_status[status] += 1
        by_provider[r["provider"]] += 1
        by_tier[r["tier"]] += 1
        req_by_model[model] += 1
        if status != "ok":
            by_hour_err[hour] += 1
            err_by_model[model] += 1
        else:
            lat = float(r["latency_ms"] or 0)
            lat_by_model[model].append(lat)
            all_latencies.append(lat)

    hours_sorted = sorted(by_hour)
    this_hour_key = _hour_key(datetime.now(timezone.utc).isoformat(timespec="seconds"))

    buckets = [(0, 250), (250, 500), (500, 1000), (1000, 2000), (2000, 5000), (5000, 10**9)]
    histogram = []
    for lo, hi in buckets:
        label = f"{lo}-{hi}" if hi < 10**9 else f"{lo}+"
        histogram.append({"bucket_ms": label, "count": sum(1 for v in all_latencies if lo <= v < hi)})

    total = len(rows)
    diversity = 0.0
    for c in by_provider.values():
        p = c / total
        diversity -= p * math.log(p, 2) if p > 0 else 0.0

    return {
        "totals": {
            "requests": total,
            "this_hour": by_hour.get(this_hour_key, 0),
            "peak_rpm": max(by_hour.values()) if by_hour else 0,
        },
        "hourly": [{"hour": h, "requests": by_hour[h]} for h in hours_sorted],
        "latency": {
            "per_model": [
                {"model": m, "p50": round(_percentile(v, 0.5)),
                 "p95": round(_percentile(v, 0.95)), "count": len(v)}
                for m, v in sorted(lat_by_model.items())
            ],
            "histogram": histogram,
        },
        "distribution": {
            "by_provider": [{"provider": p, "requests": c} for p, c in by_provider.most_common()],
            "top_models": [{"model": m, "requests": c} for m, c in req_by_model.most_common(10)],
            "diversity": round(diversity, 3),
        },
        "errors": {
            "rate_by_hour": [
                {"hour": h, "total": by_hour[h], "errors": by_hour_err.get(h, 0),
                 "rate": round(by_hour_err.get(h, 0) / by_hour[h], 3) if by_hour[h] else 0.0}
                for h in hours_sorted
            ],
            "by_type": [{"status": s, "count": c} for s, c in by_status.most_common()],
            "per_model": [
                {"model": m, "requests": req_by_model[m], "errors": err_by_model.get(m, 0),
                 "rate": round(err_by_model.get(m, 0) / req_by_model[m], 3) if req_by_model[m] else 0.0}
                for m in sorted(req_by_model)
            ],
        },
        "tiers": {"by_tier": [{"tier": t, "requests": c} for t, c in by_tier.most_common()]},
    }


# ── the last N hours, as the Overview needs it ──────────────────────────
#
# `compute_stats` above reads the whole audit log and buckets by hour over
# all of history; it was written for the retired React front end, and every
# figure it returns is lifetime-to-date. The Overview asks a narrower
# question - "what has it been doing lately?" - so this does its own pass
# over the same file and answers only for a fixed, recent window.
#
# Hours are bucketed in the machine's *local* time, not UTC. The owner runs
# this on their own computer and means their own clock when they read
# "14:00" off the chart.


def _local_hour(ts: str) -> datetime:
    """The local-time hour a timestamp falls in."""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
    return dt.replace(minute=0, second=0, microsecond=0)


def _audit_rows(state_dir: str) -> list[dict]:
    path = Path(state_dir) / "audit.csv"
    if not path.exists():
        return []
    with path.open() as f:
        return list(csv.DictReader(f))


def recent_window(state_dir: str, hours: int = 24,
                  now: Optional[datetime] = None) -> dict:
    """Everything the Overview draws, for the last `hours` hours.

    One pass over `audit.csv`, one clock. The page renders several things
    off this - a stacked chart, a per-provider table, a latency list, a
    bucket split - and computing them together is what stops two of them
    disagreeing about a request that landed while the page was rendering.

    The window always has exactly `hours` buckets, oldest first, including
    empty ones: a chart with a gap in it should show the gap, not close it.
    """
    now = (now or datetime.now()).astimezone()
    end = now.replace(minute=0, second=0, microsecond=0)
    starts = [end - timedelta(hours=hours - 1 - i) for i in range(hours)]
    slot = {s: i for i, s in enumerate(starts)}

    by_provider: dict[str, list[int]] = {}
    fails_by_hour = [0] * hours
    totals_by_hour = [0] * hours
    # provider -> hour -> [ok_count, error_count]
    health: dict[str, list[list[int]]] = {}

    lat_by_model: dict[str, list[float]] = defaultdict(list)
    req_by_model: Counter = Counter()
    by_bucket: Counter = Counter()
    all_latencies: list[float] = []
    total = ok_total = 0

    for r in _audit_rows(state_dir):
        try:
            hour = _local_hour(r["timestamp"])
        except (ValueError, KeyError):
            continue
        i = slot.get(hour)
        if i is None:
            continue

        provider = r.get("provider") or "unknown"
        status = r.get("status") or ""
        good = status == "ok"

        by_provider.setdefault(provider, [0] * hours)[i] += 1
        health.setdefault(provider, [[0, 0] for _ in range(hours)])
        health[provider][i][0 if good else 1] += 1

        totals_by_hour[i] += 1
        total += 1
        model = f"{provider}/{r.get('model', '')}"
        req_by_model[model] += 1
        by_bucket[r.get("tier") or "unknown"] += 1

        if good:
            ok_total += 1
            try:
                lat = float(r.get("latency_ms") or 0)
            except ValueError:
                lat = 0.0
            lat_by_model[model].append(lat)
            all_latencies.append(lat)
        else:
            fails_by_hour[i] += 1

    # Busiest provider first: the stack reads best with the big band at the
    # bottom, and the table reads best with the provider you care about on
    # top. Same order for both, so the legend colours agree.
    order = sorted(by_provider, key=lambda p: (-sum(by_provider[p]), p))

    def hour_state(counts: list[int]) -> str:
        good, bad = counts
        if good == 0 and bad == 0:
            return "none"
        if bad == 0:
            return "ok"
        return "bad" if good == 0 else "part"

    providers = [{
        "name": name,
        "values": by_provider[name],
        "requests": sum(by_provider[name]),
        "states": [hour_state(c) for c in health[name]],
        "failed": sum(c[1] for c in health[name]),
    } for name in order]

    return {
        "hours": hours,
        "labels": [s.strftime("%H:%M") for s in starts],
        "starts": [s.isoformat(timespec="seconds") for s in starts],
        "providers": providers,
        "fails_by_hour": fails_by_hour,
        "totals_by_hour": totals_by_hour,
        "peak_hour": max(totals_by_hour) if totals_by_hour else 0,
        "requests": total,
        "answered": ok_total,
        "failed": total - ok_total,
        "answered_rate": (ok_total / total) if total else None,
        "latency": {
            "p50": round(_percentile(all_latencies, 0.5)) if all_latencies else None,
            "p95": round(_percentile(all_latencies, 0.95)) if all_latencies else None,
            "per_model": sorted(
                ({"model": m,
                  "p50": round(_percentile(v, 0.5)),
                  "p95": round(_percentile(v, 0.95)),
                  "requests": req_by_model[m]}
                 for m, v in lat_by_model.items()),
                key=lambda d: -d["requests"],
            ),
        },
        "buckets": [{"bucket": b, "requests": c} for b, c in by_bucket.most_common()],
    }
