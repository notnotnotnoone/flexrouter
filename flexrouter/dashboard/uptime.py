from __future__ import annotations
import csv
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from flexrouter.health_history import HealthHistory


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _fraction(samples: list, cutoff: datetime):
    total = up = 0
    for ts, state in samples:
        if ts >= cutoff:
            total += 1
            if state == "up":
                up += 1
    return (up / total) if total else None


def compute_uptime(state_dir: str, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    hh = HealthHistory(state_dir)
    history = hh.read()

    # model -> list[(timestamp, state)] where state in {"up","down"}
    per_model: dict[str, list] = defaultdict(list)
    prov_samples: dict[str, list] = defaultdict(list)
    for sample in history:
        ts = _parse(sample["timestamp"])
        for model, info in (sample.get("models") or {}).items():
            state = "up" if info.get("status") == "up" else "down"
            per_model[model].append((ts, state))
            prov_samples[model.split("/")[0]].append((ts, state))

    c24 = now - timedelta(hours=24)
    c7 = now - timedelta(days=7)
    c30 = now - timedelta(days=30)

    models = []
    for model, samples in sorted(per_model.items()):
        samples.sort(key=lambda x: x[0])
        segments = [{"start": ts.isoformat(timespec="seconds"),
                     "end": ts.isoformat(timespec="seconds"),
                     "state": state if state == "up" else "down"}
                    for ts, state in samples if ts >= c24]
        models.append({
            "model": model,
            "uptime_24h": _fraction(samples, c24),
            "uptime_7d": _fraction(samples, c7),
            "uptime_30d": _fraction(samples, c30),
            "segments": segments,
        })

    providers = [{"provider": p, "uptime_24h": _fraction(s, c24)}
                 for p, s in sorted(prov_samples.items())]

    sys_total = sys_up = 0
    for s in per_model.values():
        for ts, state in s:
            if ts >= c24:
                sys_total += 1
                sys_up += 1 if state == "up" else 0
    system = {"uptime_24h": (sys_up / sys_total) if sys_total else None}

    # Incidents: pair a failure -> next recovered per model
    incidents = []
    epath = Path(state_dir) / "events.csv"
    if epath.exists():
        with epath.open() as f:
            evs = list(csv.DictReader(f))
        open_inc: dict[str, dict] = {}
        for e in evs:
            key = f"{e['provider']}/{e['model']}"
            if e["event_type"] in ("busy", "struggling", "needs_you", "penalized",
                                   "rate_limited", "server_error", "timeout"):
                open_inc.setdefault(key, {
                    "start": e["timestamp"], "provider": e["provider"],
                    "model": e["model"], "event_type": e["event_type"]})
            elif e["event_type"] == "recovered" and key in open_inc:
                inc = open_inc.pop(key)
                start, end = _parse(inc["start"]), _parse(e["timestamp"])
                inc["end"] = e["timestamp"]
                inc["duration_seconds"] = int((end - start).total_seconds())
                incidents.append(inc)
        for inc in open_inc.values():  # still-open incidents
            inc["end"] = None
            inc["duration_seconds"] = None
            incidents.append(inc)

    return {"models": models, "incidents": incidents, "system": system, "providers": providers}
