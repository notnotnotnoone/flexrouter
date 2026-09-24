"""Every machine-made settings change.

config.yaml is hand-written and never rewritten, so the owner's comments
survive permanently (spec §1). Anything the dashboard changes lands here and
is merged over the parsed settings at load time.
"""
from __future__ import annotations

import copy
from pathlib import Path

from flexrouter import home
from flexrouter.store import read_json, write_json

SECTIONS = ("settings", "providers", "models")

# What the dashboard is allowed to change, per section. Anything arriving from
# outside is checked against these before it is written: an override that
# load_config cannot make sense of wedges settings loading permanently, from
# an unauthenticated local endpoint, and the override file outlives the
# process that wrote it.
ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "settings": frozenset({
        "port", "dashboard_port", "state_dir", "window_seconds",
        "penalty_base_seconds", "penalty_max_seconds", "session_ttl_minutes",
        "sample_interval_seconds", "health_history_days", "key_concurrency_cap",
        "retry_policy", "retries", "backoff_seconds", "provider_budget", "hooks",
        "decider_base_url", "decider_model", "decider_timeout_seconds",
        "decider_confidence_threshold", "decider_rule_prior_confidence",
        "decider_confidence_ceiling", "decider_contested_statuses",
        "quarantine_seconds", "probe_timeout_seconds", "error_max_length",
        "unscored_fallback_score", "experimental_model_discovery",
    }),
    # Not `decider_api_key`: same rule as auth_token. The classifier's key
    # lives in keys.json through service_keys.py, masked like any other.
    # Not `auth_token`: it is a credential. A key that could be set from the
    # dashboard could be set by anything that reached the dashboard.
    # Not `api_key`/`api_keys` (credentials never go in settings) and not
    # `api_key_env` (it would let a caller point a provider at any environment
    # variable on the machine).
    "providers": frozenset({"base_url", "header_parser", "key_strategy"}),
    # Deliberately not `provider` or `model`: those two fields are the
    # model's identity. Rewriting them turns an override for one model into a
    # different model, possibly on a provider that does not exist. A model
    # that genuinely does not exist yet goes through `add_model()` below
    # instead, which is additive rather than a rewrite of something already
    # there.
    "models": frozenset({
        "enabled", "score", "rpm", "tpm", "context_window", "vision", "quotas",
        # Prices are settings, not credentials: the owner types what the
        # provider charges so the dashboard can stop guessing that
        # everything is free.
        "price_in", "price_out",
    }),
}

# Fields `add_model()` requires, on top of `ALLOWED_FIELDS["models"]` - the
# identity a brand-new model needs that an *existing* model must never have
# rewritten out from under it (see the comment on "models" above).
_NEW_MODEL_IDENTITY = frozenset({"provider", "model"})


def check_fields(section: str, fields: dict) -> None:
    """Raise ValueError if `fields` holds anything this section may not set."""
    if section not in ALLOWED_FIELDS:
        raise ValueError(f"unknown override section {section!r}")
    unknown = sorted(set(fields or {}) - ALLOWED_FIELDS[section])
    if unknown:
        allowed = ", ".join(sorted(ALLOWED_FIELDS[section]))
        raise ValueError(
            f"{', '.join(unknown)} cannot be changed here. "
            f"What can: {allowed}")


def _path(path: Path | str | None) -> Path:
    return Path(path) if path else home.overrides_path()


def load_overrides(path: Path | str | None = None) -> dict:
    return read_json(_path(path), default={}) or {}


def save_overrides(data: dict, path: Path | str | None = None) -> None:
    write_json(_path(path), data)


def set_override(section: str, key: str, field: str | None, value,
                 path: Path | str | None = None) -> None:
    if section not in SECTIONS:
        raise ValueError(f"unknown override section {section!r}")
    data = load_overrides(path)
    if section == "settings":
        data.setdefault("settings", {})[key] = value
    else:
        data.setdefault(section, {}).setdefault(key, {})[field] = value
    save_overrides(data, path)


def clear_override(section: str, key: str, path: Path | str | None = None) -> bool:
    data = load_overrides(path)
    bucket = data.get(section, {})
    if key not in bucket:
        return False
    bucket.pop(key)
    if not bucket:
        data.pop(section, None)
    save_overrides(data, path)
    return True


def add_provider(name: str, fields: dict, path: Path | str | None = None) -> None:
    """Represent a provider `config.yaml` has never heard of.

    Closes the first of the three gaps Stage 8 roadmap ruling R4 named:
    `config.yaml` stays untouched, and the provider only exists at all
    once this override is layered on top of it (`apply_overrides` below).
    """
    fields = dict(fields or {})
    if not fields.get("base_url"):
        raise ValueError("base_url is required")
    extra = sorted(set(fields) - ALLOWED_FIELDS["providers"] - {"base_url"})
    if extra:
        raise ValueError(f"{', '.join(extra)} cannot be set here")
    data = load_overrides(path)
    if name in (data.get("providers") or {}) or name in (data.get("new_providers") or {}):
        raise ValueError(f"a provider named {name!r} already has changes recorded")
    data.setdefault("new_providers", {})[name] = fields
    save_overrides(data, path)


def add_bucket(name: str, path: Path | str | None = None) -> None:
    """Represent a bucket `config.yaml` has never heard of - starts empty;
    `add_model()` is what puts a model in it."""
    data = load_overrides(path)
    names = data.setdefault("new_buckets", [])
    if name not in names:
        names.append(name)
    save_overrides(data, path)


def add_model(bucket: str, fields: dict, path: Path | str | None = None) -> None:
    """Represent a model `config.yaml` has never heard of, in `bucket`.

    Unlike `set_override("models", ...)`, this is additive rather than a
    patch onto something that already exists, so it is the one place a
    model's identity (`provider`/`model`) is allowed to be set at all.
    """
    fields = dict(fields or {})
    missing = sorted(_NEW_MODEL_IDENTITY - set(fields))
    if missing:
        raise ValueError(f"{', '.join(missing)} required")
    allowed = _NEW_MODEL_IDENTITY | ALLOWED_FIELDS["models"]
    extra = sorted(set(fields) - allowed)
    if extra:
        raise ValueError(f"{', '.join(extra)} cannot be set here")
    for required in ("score", "rpm", "tpm"):
        if required not in fields:
            raise ValueError(f"{required} is required")
    data = load_overrides(path)
    data.setdefault("new_models", {}).setdefault(bucket, []).append(fields)
    save_overrides(data, path)


def _is_off(value) -> bool:
    """Whether a model's `enabled` field means "off".

    This arrives in a JSON body, so "off" can turn up as `false`, `0`, `null`
    or the string `"false"` depending on what wrote it. Testing `is False`
    recognised only the first of those and quietly left the model enabled —
    the opposite of what the owner asked for.
    """
    if isinstance(value, str):
        return value.strip().lower() in {"false", "0", "no", "off", ""}
    return not value


def _bucket_key(raw: dict) -> str | None:
    if raw.get("buckets"):
        return "buckets"
    if raw.get("tiers"):
        return "tiers"
    return None


def apply_overrides(raw: dict, ov: dict) -> dict:
    """Merge overrides over parsed settings. Shallow, per settings key,
    per provider, and per provider/model. Returns a new dict."""
    merged = copy.deepcopy(raw)
    if not ov:
        return merged

    for key, value in (ov.get("settings") or {}).items():
        merged.setdefault("settings", {})[key] = value

    # A brand-new provider is layered in before ordinary provider overrides
    # are applied, so nothing about how those are merged needs to change to
    # cover it - by the time the loop below runs, it is just another entry
    # in merged["providers"].
    for name, fields in (ov.get("new_providers") or {}).items():
        merged.setdefault("providers", {}).setdefault(name, dict(fields or {}))

    for name, fields in (ov.get("providers") or {}).items():
        target = merged.setdefault("providers", {}).setdefault(name, {})
        target.update(fields or {})

    bkey = _bucket_key(merged) or "tiers"

    # A brand-new bucket starts empty; add_model() is what puts anything in
    # it. Read alongside a config.yaml that has never heard of it, this is
    # the only override that can create a bucket key that did not exist at
    # all before overrides were applied.
    for name in (ov.get("new_buckets") or []):
        merged.setdefault(bkey, {}).setdefault(name, [])

    # A brand-new model is folded in before the per-model override loop
    # below runs, not after: config.yaml's `buckets:` stays empty forever
    # (spec §1), so every model this app has ever added arrived through
    # `new_models`, and a later override targeting that same model - a
    # score AA just matched, a rate limit learned from traffic, "disable" -
    # has to reach an entry that already exists when the loop below walks
    # the bucket. Splicing new_models in afterwards, as this used to do,
    # meant no override could ever take effect on a model added this way,
    # silently, forever - the fields changed in overrides.json, the live
    # config never did.
    for bucket_name, new_entries in (ov.get("new_models") or {}).items():
        merged.setdefault(bkey, {}).setdefault(bucket_name, [])
        merged[bkey][bucket_name] = [
            *merged[bkey][bucket_name], *copy.deepcopy(new_entries),
        ]

    model_ov = ov.get("models") or {}
    if model_ov:
        for bucket_name, models in list((merged.get(bkey) or {}).items()):
            kept = []
            for entry in models or []:
                ident = f"{entry.get('provider')}/{entry.get('model')}"
                fields = dict(model_ov.get(ident) or {})
                if _is_off(fields.pop("enabled", True)):
                    continue
                entry.update(fields)
                kept.append(entry)
            merged[bkey][bucket_name] = kept

    return merged
