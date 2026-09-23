"""Read-only views of a flexrouter home, for the terminal to render.

The dashboard's `flexrouter/dashboard/facts.py` computes its own facts from a
running `LocalRouter`. The terminal cannot assume one: the owner may open the
TUI before ever running `flexrouter serve`, or run `flexrouter keys add --list`
on a machine where the service is not up at all. So this module reads the
home's own files - `config.yaml`, `keys.json`, `overrides.json`, and what the
service wrote under `state/` - and never builds a router, never calls a
provider, and never writes.

Same rule as the dashboard's facts: plain data out, nothing here knows what a
table is. And because a TUI that crashes on a half-written state file is worse
than one that shows a blank panel, every reader swallows a missing or
unreadable file and returns an empty/neutral result rather than raising.
"""
from __future__ import annotations

import csv
import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from flexrouter import home
from flexrouter.config import load_config
from flexrouter.exceptions import ConfigError, ConfigFieldError
from flexrouter.keys import KeyRecord, load_keys, mask
from flexrouter.overrides import SECTIONS, load_overrides
from flexrouter.store import read_json

# The same three spellings config.py uses to tell "runs on this machine, needs
# no key" apart from "reached over the network, needs one".
_LOCAL_HOST_HINTS = ("localhost", "127.0.0.1", "::1")


def _is_local(base_url: Optional[str]) -> bool:
    return any(hint in (base_url or "") for hint in _LOCAL_HOST_HINTS)


def _load(config_path=None):
    """Load settings once, reporting failure instead of raising.

    Returns `(cfg, error, is_missing_field, warnings)`. `is_missing_field`
    separates a settings file that cannot be parsed at all from one that is
    readable but is missing a needed field - the two get different sentences,
    the same distinction `flexrouter doctor` has always drawn.

    Warnings are captured rather than shown because a provider with a key
    typed into the settings file makes `resolve_keys` warn, and that warning
    is worth surfacing once in `doctor` - not leaking out of every refresh of
    a TUI panel.
    """
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            cfg = load_config(config_path)
        return cfg, None, False, [str(w.message) for w in caught]
    except ConfigFieldError as e:
        return None, str(e), True, []
    except ConfigError as e:
        return None, str(e), False, []


def _models_of(cfg, provider: str) -> list[str]:
    """Every configured model belonging to one provider, across all buckets."""
    seen: list[str] = []
    for models in cfg.tiers.values():
        for mc in models:
            if mc.provider == provider and mc.model not in seen:
                seen.append(mc.model)
    return seen


@dataclass
class ProviderSummary:
    name: str
    base_url: str
    key_count: int
    keys_enabled: int
    models_total: int
    daily_cost_usd: float
    state: str  # "ok" | "warn" | "bad"


@dataclass
class ServiceOverview:
    loaded: bool
    config_error: Optional[str] = None
    port: Optional[int] = None
    buckets: list[str] = field(default_factory=list)
    providers: list[ProviderSummary] = field(default_factory=list)
    bucket_count: int = 0
    model_count: int = 0
    key_count: int = 0
    keys_enabled: int = 0
    total_cost_usd: float = 0.0
    session_start: Optional[str] = None
    health_present: bool = False


def _provider_state(key_count: int, keys_enabled: int, base_url: str) -> str:
    if key_count == 0:
        # A provider running on this machine needs no key; anything else with
        # no key at all can never be reached.
        return "ok" if _is_local(base_url) else "bad"
    if keys_enabled == 0:
        return "warn"
    return "ok"


def overview(config_path=None) -> ServiceOverview:
    """Totals and a per-provider roll-up, from settings + health.json."""
    cfg, error, _, _ = _load(config_path)
    if cfg is None:
        return ServiceOverview(loaded=False, config_error=error)

    state_dir = Path(cfg.state_dir)
    health = read_json(state_dir / "health.json", default={}) or {}
    health_present = bool(health)
    health_providers = health.get("providers") or {}
    vault = load_keys()

    providers: list[ProviderSummary] = []
    for name in sorted(set(cfg.providers) | set(vault)):
        pcfg = cfg.providers.get(name)
        base_url = pcfg.base_url if pcfg else ""
        # The resolved keys (saved, then env, then inline) are what actually
        # reaches the provider, so they are what the count should reflect;
        # fall back to the raw vault for a provider that is only in keys.json.
        records = list((pcfg.keys if pcfg else None) or vault.get(name, []))
        enabled = sum(1 for r in records if r.enabled and r.secret)
        cost = float((health_providers.get(name) or {}).get("daily_cost_usd", 0.0) or 0.0)
        providers.append(ProviderSummary(
            name=name,
            base_url=base_url,
            key_count=len(records),
            keys_enabled=enabled,
            models_total=len(_models_of(cfg, name)) if pcfg else 0,
            daily_cost_usd=cost,
            state=_provider_state(len(records), enabled, base_url),
        ))

    return ServiceOverview(
        loaded=True,
        port=cfg.port,
        buckets=list(cfg.tiers),
        providers=providers,
        bucket_count=len(cfg.tiers),
        model_count=sum(len(m) for m in cfg.tiers.values()),
        key_count=sum(p.key_count for p in providers),
        keys_enabled=sum(p.keys_enabled for p in providers),
        total_cost_usd=float(health.get("total_cost_usd", 0.0) or 0.0),
        session_start=health.get("session_start"),
        health_present=health_present,
    )


@dataclass
class ProviderKeys:
    """One provider and whatever credential reaches it, if any.

    `saved` is what `flexrouter keys add` put in keys.json; `resolved_from`
    covers the other two routes (an environment variable, or a key typed into
    the settings file) that have no saved record but still reach the provider.
    """
    name: str
    in_settings: bool
    base_url: Optional[str]
    saved: list[KeyRecord] = field(default_factory=list)
    resolved_from: Optional[str] = None  # "env" | "inline" | None
    resolved_masked: str = ""

    @property
    def has_any_key(self) -> bool:
        return bool(self.saved) or self.resolved_from is not None


def providers_and_keys(config_path=None) -> list[ProviderKeys]:
    """Every provider - with a key and without one - in one place.

    The union of what settings declares and what keys.json holds, so a
    provider the owner declared but has not added a key to shows up right
    beside one that has two keys saved. This is the single source for both
    `flexrouter keys add --list` and the TUI's Keys view.
    """
    cfg, _, _, _ = _load(config_path)
    vault = load_keys()
    names = sorted(set(vault) | (set(cfg.providers) if cfg else set()))

    out: list[ProviderKeys] = []
    for name in names:
        pcfg = cfg.providers.get(name) if cfg else None
        saved = list(vault.get(name, []))
        resolved_from: Optional[str] = None
        resolved_masked = ""
        if pcfg is not None and not saved:
            for record in (pcfg.keys or []):
                if record.source in ("env", "inline"):
                    resolved_from = record.source
                    resolved_masked = mask(record.secret)
                    break
        out.append(ProviderKeys(
            name=name,
            in_settings=pcfg is not None,
            base_url=pcfg.base_url if pcfg else None,
            saved=saved,
            resolved_from=resolved_from,
            resolved_masked=resolved_masked,
        ))
    return out


@dataclass
class RequestRow:
    timestamp: str
    bucket: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: int
    status: str


def _read_audit_csv(path: Path, limit: int) -> list[RequestRow]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = text.splitlines()
    if not lines:
        return []

    header = next(csv.reader([lines[0]]), [])
    index = {name: i for i, name in enumerate(header)}

    def cell(cells: list[str], name: str, default: str = "") -> str:
        i = index.get(name)
        return cells[i] if i is not None and i < len(cells) else default

    rows: list[RequestRow] = []
    for line in lines[1:][-limit:]:
        if not line.strip():
            continue
        cells = next(csv.reader([line]), [])
        try:
            rows.append(RequestRow(
                timestamp=cell(cells, "timestamp"),
                bucket=cell(cells, "tier"),
                provider=cell(cells, "provider"),
                model=cell(cells, "model"),
                prompt_tokens=int(float(cell(cells, "prompt_tokens", "0") or 0)),
                completion_tokens=int(float(cell(cells, "completion_tokens", "0") or 0)),
                cost_usd=float(cell(cells, "cost_usd", "0") or 0),
                latency_ms=int(float(cell(cells, "latency_ms", "0") or 0)),
                status=cell(cells, "status"),
            ))
        except ValueError:
            continue  # a torn or hand-edited row is skipped, never fatal
    rows.reverse()  # newest first
    return rows


def _read_traces(path: Path, limit: int) -> list[RequestRow]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    rows: list[RequestRow] = []
    for line in lines[-limit:]:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        tokens = entry.get("tokens") or {}
        answered = entry.get("answered_by") or {}
        rows.append(RequestRow(
            timestamp=entry.get("at", ""),
            bucket=(entry.get("asked") or {}).get("bucket", ""),
            provider=answered.get("provider", ""),
            model=answered.get("model", ""),
            prompt_tokens=int(tokens.get("in", 0) or 0),
            completion_tokens=int(tokens.get("out", 0) or 0),
            cost_usd=0.0,
            latency_ms=int(entry.get("ms_total", 0) or 0),
            status="ok" if entry.get("ok") else "fail",
        ))
    rows.reverse()
    return rows


def recent_requests(limit: int = 100, config_path=None) -> list[RequestRow]:
    """The last `limit` requests, newest first.

    Prefers the audit CSV (what `flexrouter` calls the request log); falls
    back to the per-request trace file for a home that has only ever been
    served by a build that wrote traces but not audit rows.
    """
    cfg, _, _, _ = _load(config_path)
    state_dir = Path(cfg.state_dir) if cfg else home.state_dir()

    csv_path = state_dir / "audit.csv"
    if csv_path.exists():
        rows = _read_audit_csv(csv_path, limit)
        if rows:
            return rows
    return _read_traces(state_dir / "traces.jsonl", limit)


@dataclass
class ProviderKeySource:
    name: str
    where: str
    masked: str = ""
    extra: int = 0


@dataclass
class DoctorReport:
    home_dir: str
    config_name: str
    keys_name: str
    overrides_name: str
    state_name: str
    state_dir: str
    is_new_home: bool
    error: Optional[str] = None
    error_is_missing_field: bool = False
    bucket_count: int = 0
    model_count: int = 0
    provider_count: int = 0
    port: Optional[int] = None
    key_sources: list[ProviderKeySource] = field(default_factory=list)
    overrides: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def doctor_report(config_path=None) -> DoctorReport:
    """Where everything lives, and which key each provider will reach for.

    Reproduces the same account `flexrouter doctor` has always given - the
    same precedence (saved key, then env, then inline) and the same sentences,
    so the terminal's styled rendering stays greppable.
    """
    # Checked before ensure_home(): otherwise reading the file we are about to
    # create would make a brand-new home look like an old one.
    is_new_home = not home.config_path().exists()
    home.ensure_home()

    cfg, error, is_missing_field, warned = _load(config_path)

    report = DoctorReport(
        home_dir=str(home.home_dir()),
        config_name=home.config_path().name,
        keys_name=home.keys_path().name,
        overrides_name=home.overrides_path().name,
        state_name=home.state_dir().name,
        state_dir=str(home.state_dir()),
        is_new_home=is_new_home,
        error=error,
        error_is_missing_field=is_missing_field,
        warnings=warned,
    )

    if cfg is not None:
        report.bucket_count = len(cfg.tiers)
        report.model_count = sum(len(m) for m in cfg.tiers.values())
        report.provider_count = len(cfg.providers)
        report.port = cfg.port

        vault = load_keys()
        for name, provider in sorted(cfg.providers.items()):
            if not provider.keys:
                disabled = [r for r in vault.get(name, []) if not r.enabled]
                if disabled:
                    where = f"no usable key — {len(disabled)} saved but disabled"
                else:
                    where = "no key found"
                report.key_sources.append(ProviderKeySource(name=name, where=where))
                continue
            first = provider.keys[0]
            if first.source == "env":
                where = f"{first.label} (environment)"
            elif first.source == "inline":
                where = (f"typed into {home.config_path().name} — move it with: "
                         f"flexrouter keys add {name}")
            else:
                where = f"saved key {first.id}"
            report.key_sources.append(ProviderKeySource(
                name=name, where=where, masked=mask(first.secret),
                extra=len(provider.keys) - 1,
            ))

    changes = load_overrides()
    for section in SECTIONS:
        for key, value in (changes.get(section) or {}).items():
            report.overrides.append((key, str(value)))

    return report
