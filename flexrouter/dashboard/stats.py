from __future__ import annotations
import csv
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


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
