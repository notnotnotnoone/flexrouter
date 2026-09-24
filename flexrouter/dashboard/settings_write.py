"""Every write the Settings page can make to an *existing* setting,
provider, or model.

Nothing here writes config.yaml (spec §1, the standing rule every stage
has kept) - only overrides.json, through flexrouter/overrides.py's own
ALLOWED_FIELDS guard. This module is a thin, request-shaped wrapper
around that module's `set_override`/`clear_override`; adding a brand-new
provider, bucket, or model is `overrides.add_provider`/`add_bucket`/
`add_model` directly, not here, because those are additive rather than a
patch onto a field that already exists.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from flexrouter import overrides as ov


def set_settings_field(field: str, value, path: Optional[Path] = None) -> None:
    ov.check_fields("settings", {field: value})
    ov.set_override("settings", field, None, value, path)


def clear_settings_field(field: str, path: Optional[Path] = None) -> None:
    ov.clear_override("settings", field, path)


def set_provider_fields(provider: str, fields: dict, path: Optional[Path] = None) -> None:
    ov.check_fields("providers", fields)
    for field, value in fields.items():
        ov.set_override("providers", provider, field, value, path)


def clear_provider(provider: str, path: Optional[Path] = None) -> None:
    ov.clear_override("providers", provider, path)


def set_bucket_strategy(bucket: str, strategy: str, path: Optional[Path] = None) -> None:
    ov.set_bucket_strategy(bucket, strategy, path)


def set_model_fields(provider: str, model: str, fields: dict, path: Optional[Path] = None) -> None:
    ov.check_fields("models", fields)
    ident = f"{provider}/{model}"
    for field, value in fields.items():
        ov.set_override("models", ident, field, value, path)


def clear_model(provider: str, model: str, path: Optional[Path] = None) -> None:
    ov.clear_override("models", f"{provider}/{model}", path)
