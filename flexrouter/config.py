from __future__ import annotations

import copy
import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from flexrouter import home
from flexrouter.exceptions import ConfigError, ConfigFieldError
from flexrouter.keys import KeyRecord, load_keys
from flexrouter.overrides import apply_overrides, load_overrides

RETRY_PRESETS = {
    "conservative": {"retries": 2, "backoff_seconds": 5.0},
    "balanced":     {"retries": 3, "backoff_seconds": 2.0},
    "aggressive":   {"retries": 5, "backoff_seconds": 1.0},
}

@dataclass
class ModelConfig:
    provider: str
    model: str
    score: int
    rpm: int
    tpm: int
    context_window: int = 200000
    vision: bool = False
    quotas: dict[str, int] = field(default_factory=dict)

@dataclass
class ProviderConfig:
    base_url: str
    api_keys: list[str]  # resolved secret strings, in selection order
    header_parser: str = "openai_compatible"
    keys: list[KeyRecord] = field(default_factory=list)

@dataclass
class RetryConfig:
    retries: int = 3
    backoff_seconds: float = 2.0

@dataclass
class FlexConfig:
    tiers: dict[str, list[ModelConfig]]
    providers: dict[str, ProviderConfig]
    state_dir: str = ".flexrouter"
    window_seconds: int = 60
    penalty_base_seconds: int = 30
    penalty_max_seconds: int = 1800
    session_ttl_minutes: int = 30
    port: int | None = None          # None means "left at the default"
    dashboard_port: int | None = None  # deprecated alias for `port`
    sample_interval_seconds: int = 60
    health_history_days: int = 30
    retry: RetryConfig = field(default_factory=RetryConfig)
    provider_budget: dict[str, float] = field(default_factory=dict)
    hooks: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # `port` is the real field; `dashboard_port` is a deprecated alias that
        # must always end up equal to `port`. A caller may legitimately pass
        # either one: if `dashboard_port` was set explicitly while `port` was
        # left alone, that value flows into `port`. Otherwise `port` wins and
        # `dashboard_port` is brought into line with it.
        #
        # Both default to None rather than to a number, because comparing
        # against the number cannot tell "left at the default" apart from
        # "explicitly passed the default value" — which silently discarded an
        # explicit port of 4891 in favour of the alias.
        if self.port is None:
            self.port = (home.DEFAULT_PORT if self.dashboard_port is None
                         else self.dashboard_port)
        self.dashboard_port = self.port


def _line_of(text: str, needle: str) -> int | None:
    for i, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return i
    return None


def _env_names(praw: dict) -> list[str]:
    """Env var names this provider declares, in order."""
    names: list[str] = []
    single = praw.get("api_key_env")
    if single:
        names.append(single)
    raw_keys = praw.get("api_keys", [])
    if isinstance(raw_keys, str):
        raw_keys = [{"env": raw_keys}]
    for k in raw_keys or []:
        if isinstance(k, dict) and k.get("env"):
            names.append(k["env"])
    return names


def _inline_secrets(praw: dict) -> list[str]:
    """Secrets typed straight into the settings file. Deprecated."""
    found: list[str] = []
    if praw.get("api_key"):
        found.append(str(praw["api_key"]))
    raw_keys = praw.get("api_keys", [])
    if isinstance(raw_keys, str):
        raw_keys = []
    for k in raw_keys or []:
        if isinstance(k, str) and k:
            found.append(k)
        elif isinstance(k, dict) and k.get("key"):
            found.append(str(k["key"]))
    return found


def resolve_keys(provider: str, praw: dict, vault: dict[str, list[KeyRecord]],
                 source_path: Path | None) -> list[KeyRecord]:
    """Credential resolution order (spec 1): stored key, then environment
    variable, then an inline secret - which is deprecated and warns."""
    stored = [r for r in vault.get(provider, []) if r.enabled and r.secret]
    if stored:
        return stored

    from_env: list[KeyRecord] = []
    for name in _env_names(praw):
        value = os.environ.get(name)
        if value:
            from_env.append(KeyRecord(id=f"env:{name}", secret=value,
                                      label=name, source="env"))
    if from_env:
        return from_env

    inline = _inline_secrets(praw)
    if not inline:
        return []

    text = source_path.read_text(encoding="utf-8") if source_path else ""
    where = _line_of(text, inline[0]) if text else None
    filename = source_path.name if source_path else "config.yaml"
    warnings.warn(
        f"Provider {provider!r} has its key typed straight into {filename}"
        f"{f', line {where}' if where else ''}. That file is meant to be "
        f"shareable. Move it with: flexrouter keys add {provider}",
        DeprecationWarning, stacklevel=2,
    )
    return [KeyRecord(id=f"{provider}-inline-{i + 1}", secret=s, source="inline")
            for i, s in enumerate(inline)]


def _bare_api_keys_string(praw: dict) -> list[str]:
    """`api_keys: <bare string>`, which cannot be told apart from a secret.

    Credential *resolution* reads this shape as an environment variable name
    (see `_env_names`), and that stays as it is — changing it would start
    sending a variable name to a provider as if it were a key. But a person
    who types their key into the wrong slot ends up here too, and nothing in
    the text says which of the two it is. Since showing a real key can never
    be taken back while hiding a variable name costs nothing but a little
    clarity, every string in this position is treated as a secret when we
    decide what may be shown.
    """
    value = praw.get("api_keys")
    return [value] if isinstance(value, str) and value else []


def _all_inline_secrets(raw: dict) -> list[str]:
    """Every secret typed straight into a parsed settings structure."""
    found: list[str] = []
    providers = raw.get("providers") or {}
    if not isinstance(providers, dict):
        return found
    for praw in providers.values():
        if isinstance(praw, dict):
            found.extend(_inline_secrets(praw))
            found.extend(_bare_api_keys_string(praw))
    return found


def _every_string(node, _seen: set[int] | None = None):
    """Every string anywhere in a parsed YAML structure, keys included.

    Anchors and aliases can make the same object appear twice, and a
    self-referencing anchor can make it appear forever, so containers are
    only walked once.
    """
    if _seen is None:
        _seen = set()
    if isinstance(node, str):
        yield node
        return
    if isinstance(node, (dict, list, tuple, set)):
        if id(node) in _seen:
            return
        _seen.add(id(node))
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _every_string(k, _seen)
            yield from _every_string(v, _seen)
    elif isinstance(node, (list, tuple, set)):
        for v in node:
            yield from _every_string(v, _seen)


def redact_config(raw: dict) -> dict:
    """A copy of parsed settings with every credential in it masked.

    A key typed into the settings file is a supported (deprecated) way to
    reach a provider, so anything that hands the parsed settings to a human
    or over HTTP has to come through here first. Environment variable *names*
    are not secrets and are left alone.
    """
    from flexrouter.keys import mask

    safe = copy.deepcopy(raw)
    for praw in (safe.get("providers") or {}).values():
        if not isinstance(praw, dict):
            continue
        if praw.get("api_key"):
            praw["api_key"] = mask(str(praw["api_key"]))
        entries = praw.get("api_keys")
        if entries is None:
            continue
        if isinstance(entries, str):
            # Resolution reads this as an env var name, but it is exactly
            # where a mistyped key lands, and the two look identical. Mask it.
            praw["api_keys"] = mask(entries)
            continue
        redacted = []
        for e in entries or []:
            if isinstance(e, str):
                redacted.append(mask(e))
            elif isinstance(e, dict):
                e = dict(e)
                if e.get("key"):
                    e["key"] = mask(str(e["key"]))
                redacted.append(e)
            else:
                redacted.append(e)
        praw["api_keys"] = redacted
    return safe


def redact_settings_text(text: str) -> str:
    """The settings file's own text with any secret typed into it masked.

    Works on the text rather than on a re-dumped structure so the owner's
    comments and layout survive — the same reason nothing rewrites
    config.yaml. If the text cannot be parsed we cannot tell which parts are
    secrets, so we refuse rather than hand out something unchecked.
    """
    from flexrouter.keys import mask

    try:
        raw = yaml.safe_load(text) or {}
    except Exception as e:
        raise ConfigError(
            f"Cannot read your settings to check them for keys: "
            f"{type(e).__name__}") from e
    if not isinstance(raw, dict):
        return text
    secrets = {s for s in _all_inline_secrets(raw) if s}
    out = text
    for secret in sorted(secrets, key=len, reverse=True):
        out = out.replace(secret, mask(secret))

    # Replacing text only hides a secret whose *parsed* value appears
    # verbatim in the source. A folded block, a quoted escape, a doubled
    # quote — each parses to something the source text does not contain, so
    # the replacement silently does nothing and the live key sails through.
    # Rather than chase every spelling YAML allows, read the redacted text
    # back and check the whole structure: if any original secret is still in
    # there, anywhere, refuse the export the same way an unreadable settings
    # file already makes us refuse.
    if secrets:
        try:
            checked = yaml.safe_load(out)
        except Exception as e:
            raise ConfigError(
                f"Cannot read your settings back to check them for keys: "
                f"{type(e).__name__}") from e
        for value in _every_string(checked):
            if any(secret in value for secret in secrets):
                raise ConfigError(
                    "One of the keys in your settings file is written in a "
                    "form this copy cannot safely hide")
    return out


def _number(settings: dict, name: str, default, cast):
    """Read one numeric setting, reporting a bad value as a ConfigError.

    A bare int()/float() here raises ValueError, which is not a ConfigError,
    so every caller's "your settings are broken" handler misses it and the
    whole program tracebacks instead. A setting can carry any text at all:
    the settings file is hand-written, and overrides.json is written from an
    unauthenticated local endpoint.
    """
    value = settings.get(name, default)
    try:
        return cast(value)
    except (ValueError, TypeError) as e:
        raise ConfigError(
            f"settings.{name} should be a number, but it is {value!r}. "
            f"Fix it in your settings file, or undo a dashboard change with: "
            f"flexrouter config reset settings {name}") from e


def load_config(path: Path | str | None = None) -> FlexConfig:
    """Load settings from the flexrouter home, with overrides layered on top."""
    home.ensure_home()
    source = Path(path) if path else home.config_path()
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except Exception as e:
        # Never interpolate str(e) here: PyYAML's parser errors embed a
        # literal snippet of the offending source line, which can contain a
        # secret typed inline (e.g. `api_key: sk-...`). Report only the file,
        # the exception type, and — when PyYAML supplies it — the line and
        # column, never the source text itself.
        mark = getattr(e, "problem_mark", None)
        location = f", line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise ConfigError(
            f"Cannot read {source}: {type(e).__name__}{location}") from e

    if not raw:
        raise ConfigError(f"{source} is empty")

    raw = apply_overrides(raw, load_overrides())
    settings = raw.get("settings") or {}

    preset_name = settings.get("retry_policy", "balanced")
    preset = RETRY_PRESETS.get(preset_name, RETRY_PRESETS["balanced"])
    retry = RetryConfig(
        retries=_number(settings, "retries", preset["retries"], int),
        backoff_seconds=_number(settings, "backoff_seconds",
                                preset["backoff_seconds"], float),
    )

    vault = load_keys()
    providers: dict[str, ProviderConfig] = {}
    for name, praw in (raw.get("providers") or {}).items():
        praw = praw or {}
        if "base_url" not in praw:
            raise ConfigFieldError(
                f"providers.{name}.base_url is missing in {source}")
        records = resolve_keys(name, praw, vault, source)
        providers[name] = ProviderConfig(
            base_url=praw["base_url"],
            api_keys=[r.secret for r in records],
            header_parser=praw.get("header_parser", "openai_compatible"),
            keys=records,
        )

    buckets_raw = raw.get("buckets")
    if buckets_raw is None:
        buckets_raw = raw.get("tiers")
    tiers: dict[str, list[ModelConfig]] = {}
    for bucket_name, models in (buckets_raw or {}).items():
        parsed_models: list[ModelConfig] = []
        for i, m in enumerate(models or []):
            m = m or {}
            for field_name in ("provider", "model", "score", "rpm", "tpm"):
                if field_name not in m:
                    raise ConfigFieldError(
                        f"buckets.{bucket_name}[{i}].{field_name} is missing "
                        f"in {source}")
            parsed_models.append(ModelConfig(
                provider=m["provider"],
                model=m["model"],
                score=m["score"],
                rpm=m["rpm"],
                tpm=m["tpm"],
                context_window=m.get("context_window", 200000),
                vision=m.get("vision", False),
                quotas=m.get("quotas", {}),
            ))
        tiers[bucket_name] = parsed_models

    port_name = "port" if "port" in settings else "dashboard_port"
    port = _number(settings, port_name, home.DEFAULT_PORT, int)
    return FlexConfig(
        tiers=tiers,
        providers=providers,
        state_dir=settings.get("state_dir", str(home.state_dir())),
        window_seconds=_number(settings, "window_seconds", 60, int),
        penalty_base_seconds=_number(settings, "penalty_base_seconds", 30, int),
        penalty_max_seconds=_number(settings, "penalty_max_seconds", 1800, int),
        session_ttl_minutes=_number(settings, "session_ttl_minutes", 30, int),
        port=port,
        dashboard_port=port,
        sample_interval_seconds=_number(settings, "sample_interval_seconds", 60, int),
        health_history_days=_number(settings, "health_history_days", 30, int),
        retry=retry,
        provider_budget=settings.get("provider_budget", {}),
        hooks=settings.get("hooks", []),
    )


NON_CHAT_PATTERNS = ("whisper", "tts", "orpheus", "image", "lyria", "guard",
                     "native-audio", "-live", "embedding", "rerank")
_LOCAL_HOST_HINTS = ("localhost", "127.0.0.1", "::1")


def is_probably_chat_model(model_id: str) -> bool:
    mid = (model_id or "").lower()
    return not any(p in mid for p in NON_CHAT_PATTERNS)


def _has_a_credential(name: str, praw: dict,
                      vault: dict[str, list[KeyRecord]]) -> bool:
    """Whether this provider can be reached at all, by any of the three
    routes: a key saved with `flexrouter keys add`, an environment variable
    the settings name, or a key typed into the settings file."""
    if any(r.enabled and r.secret for r in vault.get(name, [])):
        return True
    if any(os.environ.get(n) for n in _env_names(praw)):
        return True
    return bool(_inline_secrets(praw))


def validate_config(raw: dict) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    providers = raw.get("providers") or {}
    if not providers:
        errors.append("providers: no providers defined")

    try:
        vault = load_keys()
    except Exception:
        vault = {}

    for name, praw in providers.items():
        if not isinstance(praw, dict) or not praw.get("base_url"):
            errors.append(f"providers.{name}: missing base_url")
            continue
        base_url = str(praw.get("base_url", ""))
        if not base_url.startswith(("http://", "https://")):
            errors.append(f"providers.{name}.base_url: must start with http:// or https://")
        is_local = any(h in base_url for h in _LOCAL_HOST_HINTS)
        # An empty `api_keys` in the settings file is now the *correct* state:
        # credentials live in keys.json, not here. Only warn if there is no
        # way at all to reach this provider.
        if not is_local and not _has_a_credential(name, praw, vault):
            warnings.append(
                f"providers.{name}: no key found — add one with: "
                f"flexrouter keys add {name}")

    # `buckets:` is the preferred spelling, `tiers:` the accepted fallback,
    # exactly as load_config reads them. Reading only `tiers:` reported the
    # tool's own starter settings file as a hard error.
    bucket_key = "buckets" if raw.get("buckets") is not None else "tiers"
    buckets = raw.get(bucket_key) or {}
    if not buckets:
        errors.append(f"{bucket_key}: no {bucket_key} defined")

    for tier_name, models in buckets.items():
        for i, m in enumerate(models or []):
            where = f"{bucket_key}.{tier_name}[{i}]"
            if not isinstance(m, dict):
                errors.append(f"{where}: not a mapping")
                continue
            provider = m.get("provider")
            model = m.get("model")
            if not provider:
                errors.append(f"{where}.provider: missing")
            elif provider not in providers:
                errors.append(f"{where}.provider: {provider!r} not defined in providers")
            if not model:
                errors.append(f"{where}.model: missing")
            for field_name in ("score", "rpm", "tpm"):
                val = m.get(field_name)
                if val is None:
                    errors.append(f"{where}.{field_name}: missing")
                elif not isinstance(val, (int, float)) or val <= 0:
                    errors.append(f"{where}.{field_name}: must be a positive number, got {val!r}")
            ctx = m.get("context_window")
            if isinstance(ctx, (int, float)) and ctx < 1000:
                warnings.append(
                    f"{where}.context_window: {ctx} is suspiciously low — "
                    f"{model!r} may not be a chat model")
            if model and not is_probably_chat_model(str(model)):
                warnings.append(
                    f"{where}: {model!r} matches a non-chat name pattern — "
                    f"likely not a chat-completions model")

    return {"errors": errors, "warnings": warnings}
