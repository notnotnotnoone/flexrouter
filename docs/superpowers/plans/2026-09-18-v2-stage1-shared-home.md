# flexrouter v2 — Stage 1: One Shared Home Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move every setting and every credential into one fixed machine-wide home so no consuming project carries its own copy, and make the settings file read-only to the machine.

**Architecture:** A `home` module resolves one fixed directory (`FLEXROUTER_HOME`, else `%LOCALAPPDATA%\flexrouter`, else `~/.config/flexrouter`). A `store` module does atomic JSON writes and owner-only file permissions. A `keys` module owns `keys.json` — the only writable credential store. An `overrides` module holds every dashboard-made change and is merged over the parsed settings at load time, so `config.yaml` is never rewritten and the owner's comments survive permanently. `config.discover_config()`'s cwd search chain is deleted; that chain is the mechanism behind fault 1.

**Tech Stack:** Python 3.11+, PyYAML, click, pytest, dataclasses, `fnmatch`, `os.replace` for atomic writes.

**Spec:** `docs/superpowers/specs/2026-09-18-flexrouter-v2-design.md` (§1, Migration, open questions 1–3)

**Roadmap:** `docs/superpowers/plans/2026-09-18-v2-roadmap.md`

## Global Constraints

- Python floor is `>=3.11`, already set in `pyproject.toml`. Do not raise it.
- **Nothing in this codebase may write `config.yaml`.** Every machine-made
  change goes to `overrides.json`. This is the whole point of the stage.
- **Secrets are never returned by any HTTP endpoint or printed in full by any
  CLI command.** Only `mask()` output (`…e8d3`).
- `FLEXROUTER_HOME` overrides the home root everywhere, with no exceptions.
- Default service port is **4891** (spec §2). `dashboard_port` stays accepted as
  a deprecated alias so existing settings keep loading.
- `buckets:` is the preferred spelling in settings files; `tiers:` stays
  accepted as an alias. The internal attribute stays `FlexConfig.tiers` —
  renaming it would touch `engine.py`, which the spec forbids refactoring.
- **Do not refactor** `recovery.py`, `engine.py` selection, `window.py`,
  `quota.py`, `rate_limits.py`, `errors.py`, `client.py`. They are listed as
  "reused unchanged" in the spec.
- **Owner decision, 2026-09-18:** the existing 13.5KB `flexrouter.yaml` is
  **not** migrated. The new home starts with bucket names and no models. Only
  credentials are lifted across.
- Tests: `pytest`. `asyncio_mode = "auto"` is already set. Do not use `respx`
  together with FastAPI's `TestClient` — they collide in this repo.
- Every test that touches the home must point `FLEXROUTER_HOME` at `tmp_path`
  via `monkeypatch.setenv`. A test that writes to the real home is a bug.

---

### Task 1: Atomic file store

**Files:**
- Create: `flexrouter/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `read_json(path, default=None) -> Any`,
  `write_json(path, data) -> None`, `harden(path) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_store.py
import json
from pathlib import Path

from flexrouter.store import harden, read_json, write_json


def test_read_json_returns_default_when_missing(tmp_path):
    assert read_json(tmp_path / "nope.json") == {}
    assert read_json(tmp_path / "nope.json", default=[]) == []


def test_read_json_returns_default_when_corrupt(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json at all", encoding="utf-8")
    assert read_json(p) == {}


def test_write_json_creates_parent_directories(tmp_path):
    p = tmp_path / "a" / "b" / "c.json"
    write_json(p, {"hello": "world"})
    assert json.loads(p.read_text(encoding="utf-8")) == {"hello": "world"}


def test_write_json_round_trips(tmp_path):
    p = tmp_path / "x.json"
    write_json(p, {"n": 1})
    write_json(p, {"n": 2})
    assert read_json(p) == {"n": 2}


def test_write_json_leaves_no_temp_files_behind(tmp_path):
    p = tmp_path / "x.json"
    write_json(p, {"n": 1})
    assert [f.name for f in tmp_path.iterdir()] == ["x.json"]


def test_harden_is_safe_on_a_missing_file(tmp_path):
    harden(tmp_path / "absent.json")  # must not raise


def test_harden_leaves_the_file_readable_by_us(tmp_path):
    p = tmp_path / "secret.json"
    write_json(p, {"secret": "s"})
    harden(p)
    assert read_json(p) == {"secret": "s"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flexrouter.store'`

- [ ] **Step 3: Write the implementation**

```python
# flexrouter/store.py
"""Small-file persistence: atomic writes and owner-only permissions.

The service is the only writer of everything under the flexrouter home, so
file state with atomic replace is sufficient and correct — no external
datastore is needed (spec: Architecture Overview).
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def read_json(path: Path | str, default: Any = None) -> Any:
    """Read JSON, returning `default` ({} if unset) when absent or unreadable."""
    fallback: Any = {} if default is None else default
    p = Path(path)
    if not p.exists():
        return fallback
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return fallback


def write_json(path: Path | str, data: Any) -> None:
    """Write JSON so the whole file appears at once or nothing changes."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def harden(path: Path | str) -> None:
    """Restrict a file to the current user. Best effort; never raises."""
    p = Path(path)
    if not p.exists():
        return
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    if os.name != "nt":
        return
    user = os.environ.get("USERNAME")
    if not user:
        return
    try:
        subprocess.run(
            ["icacls", str(p), "/inheritance:r", "/grant:r", f"{user}:F"],
            capture_output=True, check=False, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_store.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add flexrouter/store.py tests/test_store.py
git commit -m "feat(store): atomic JSON writes and owner-only file permissions"
```

---

### Task 2: The one fixed home

**Files:**
- Create: `flexrouter/home.py`
- Test: `tests/test_home.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `DEFAULT_PORT: int = 4891`, `home_dir() -> Path`,
  `config_path() -> Path`, `keys_path() -> Path`, `overrides_path() -> Path`,
  `state_dir() -> Path`, `ensure_home() -> Path`, `STARTER_CONFIG: str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_home.py
from pathlib import Path

import yaml

from flexrouter import home


def test_flexrouter_home_env_var_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path / "custom"))
    assert home.home_dir() == tmp_path / "custom"


def test_falls_back_to_localappdata_on_windows(tmp_path, monkeypatch):
    monkeypatch.delenv("FLEXROUTER_HOME", raising=False)
    monkeypatch.setattr(home.os, "name", "nt")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert home.home_dir() == tmp_path / "Local" / "flexrouter"


def test_falls_back_to_config_dir_elsewhere(tmp_path, monkeypatch):
    monkeypatch.delenv("FLEXROUTER_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(home.os, "name", "posix")
    monkeypatch.setattr(home.Path, "home", staticmethod(lambda: tmp_path))
    assert home.home_dir() == tmp_path / ".config" / "flexrouter"


def test_paths_all_sit_under_the_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path))
    assert home.config_path() == tmp_path / "config.yaml"
    assert home.keys_path() == tmp_path / "keys.json"
    assert home.overrides_path() == tmp_path / "overrides.json"
    assert home.state_dir() == tmp_path / "state"


def test_ensure_home_creates_dirs_and_a_starter_config(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path / "h"))
    home.ensure_home()
    assert (tmp_path / "h" / "state").is_dir()
    raw = yaml.safe_load((tmp_path / "h" / "config.yaml").read_text(encoding="utf-8"))
    assert raw["settings"]["port"] == 4891
    assert raw["providers"] == {}
    assert set(raw["buckets"]) == {"smart", "fast", "long"}
    assert all(v == [] for v in raw["buckets"].values())


def test_ensure_home_never_overwrites_an_existing_config(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path / "h"))
    home.ensure_home()
    cfg = tmp_path / "h" / "config.yaml"
    cfg.write_text("# my own notes\nsettings: {port: 9999}\n", encoding="utf-8")
    home.ensure_home()
    assert "# my own notes" in cfg.read_text(encoding="utf-8")


def test_starter_config_keeps_its_comments(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path / "h"))
    home.ensure_home()
    assert "#" in (tmp_path / "h" / "config.yaml").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_home.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flexrouter.home'`

- [ ] **Step 3: Write the implementation**

```python
# flexrouter/home.py
"""The one fixed place every setting and credential lives.

There is no cwd search chain. Each consuming project used to end up with its
own flexrouter.yaml and its own copy of the keys; that is fault 1 in the v2
spec, and deleting the search is the fix.
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_PORT = 4891

STARTER_CONFIG = """\
# flexrouter settings.
#
# This file is yours. flexrouter never rewrites it, so your comments and
# layout survive forever. Anything you change in the dashboard is saved
# separately in overrides.json and layered on top of this at load time.
#
# Your API keys do NOT belong in here. They live in keys.json next door,
# locked to your user account. Add one with:  flexrouter keys add <provider>

settings:
  port: 4891

# Add providers here, or let the daily catalogue check discover their models.
providers: {}

# Buckets are the names your code asks for, e.g. router.generate(bucket="smart").
buckets:
  smart: []
  fast: []
  long: []
"""


def home_dir() -> Path:
    """The flexrouter home. FLEXROUTER_HOME overrides it, always."""
    env = os.environ.get("FLEXROUTER_HOME")
    if env:
        return Path(env).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "flexrouter"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "flexrouter"
    return Path.home() / ".config" / "flexrouter"


def config_path() -> Path:
    return home_dir() / "config.yaml"


def keys_path() -> Path:
    return home_dir() / "keys.json"


def overrides_path() -> Path:
    return home_dir() / "overrides.json"


def state_dir() -> Path:
    return home_dir() / "state"


def ensure_home() -> Path:
    """Create the home and a starter settings file if they are not there yet."""
    root = home_dir()
    root.mkdir(parents=True, exist_ok=True)
    state_dir().mkdir(parents=True, exist_ok=True)
    cfg = config_path()
    if not cfg.exists():
        cfg.write_text(STARTER_CONFIG, encoding="utf-8")
    return root
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_home.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add flexrouter/home.py tests/test_home.py
git commit -m "feat(home): one fixed home for settings, keys, and state"
```

---

### Task 3: The key vault

**Files:**
- Create: `flexrouter/keys.py`
- Test: `tests/test_keys.py`

**Interfaces:**
- Consumes: `flexrouter.home.keys_path`, `flexrouter.store.read_json/write_json/harden`.
- Produces: `KeyRecord` dataclass with fields
  `id: str, secret: str, label: str, added_at: str, weight: int,
  allow_models: list[str], enabled: bool, source: str`, plus
  `mask(secret) -> str`, `load_keys(path=None) -> dict[str, list[KeyRecord]]`,
  `save_keys(mapping, path=None) -> None`,
  `add_key(provider, secret, label="", path=None) -> KeyRecord`,
  `remove_key(provider, key_id, path=None) -> bool`,
  `allows(record, model_id) -> bool`, `KeyRecord.public() -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_keys.py
import json

import pytest

from flexrouter.keys import (
    KeyRecord, add_key, allows, load_keys, mask, remove_key, save_keys,
)


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path))
    return tmp_path


def test_mask_shows_only_the_last_four():
    assert mask("sk-or-v1-abcdefgh") == "…efgh"
    assert mask("ab") == "…ab"
    assert mask("") == ""


def test_load_keys_is_empty_when_there_is_no_file():
    assert load_keys() == {}


def test_add_key_persists_and_round_trips():
    rec = add_key("openrouter", "sk-or-v1-secret", label="Main account")
    assert rec.id == "openrouter-1"
    assert rec.enabled is True
    assert rec.weight == 1
    assert rec.allow_models == ["*"]
    assert rec.added_at.endswith("Z")

    loaded = load_keys()
    assert [r.secret for r in loaded["openrouter"]] == ["sk-or-v1-secret"]
    assert loaded["openrouter"][0].label == "Main account"


def test_add_key_numbers_ids_within_a_provider():
    add_key("openrouter", "a")
    add_key("openrouter", "b")
    add_key("groq", "c")
    loaded = load_keys()
    assert [r.id for r in loaded["openrouter"]] == ["openrouter-1", "openrouter-2"]
    assert [r.id for r in loaded["groq"]] == ["groq-1"]


def test_remove_key_removes_only_that_key():
    add_key("openrouter", "a")
    add_key("openrouter", "b")
    assert remove_key("openrouter", "openrouter-1") is True
    assert [r.id for r in load_keys()["openrouter"]] == ["openrouter-2"]


def test_remove_key_reports_when_nothing_matched():
    assert remove_key("openrouter", "nope") is False


def test_saved_file_is_json_with_the_expected_shape(_home):
    add_key("openrouter", "sk-secret", label="Main")
    raw = json.loads((_home / "keys.json").read_text(encoding="utf-8"))
    entry = raw["openrouter"][0]
    assert entry["secret"] == "sk-secret"
    assert entry["label"] == "Main"
    assert entry["allow_models"] == ["*"]
    assert entry["enabled"] is True


def test_public_hides_the_secret():
    rec = KeyRecord(id="k", secret="sk-or-v1-abcd")
    pub = rec.public()
    assert "secret" not in pub
    assert pub["masked"] == "…abcd"


def test_allow_models_globs_restrict_a_key():
    free_only = KeyRecord(id="k", secret="s", allow_models=["*:free"])
    assert allows(free_only, "deepseek/deepseek-chat-v3.1:free") is True
    assert allows(free_only, "openai/gpt-4o") is False

    wide = KeyRecord(id="k", secret="s")
    assert allows(wide, "anything/at-all") is True


def test_save_keys_accepts_records_and_reloads_them():
    save_keys({"groq": [KeyRecord(id="groq-1", secret="gsk", weight=3)]})
    assert load_keys()["groq"][0].weight == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_keys.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flexrouter.keys'`

- [ ] **Step 3: Write the implementation**

```python
# flexrouter/keys.py
"""Credentials. The only place flexrouter stores a secret it was given.

Secrets never leave this module in full except to the provider client: the
HTTP API and the CLI both show mask() output only.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

from flexrouter import home
from flexrouter.store import harden, read_json, write_json


@dataclass
class KeyRecord:
    id: str
    secret: str
    label: str = ""
    added_at: str = ""
    weight: int = 1
    allow_models: list[str] = field(default_factory=lambda: ["*"])
    enabled: bool = True
    source: str = "keys"  # keys | env | inline

    def public(self) -> dict:
        """Everything about this key except the secret itself."""
        data = asdict(self)
        data.pop("secret")
        data["masked"] = mask(self.secret)
        return data


def mask(secret: str) -> str:
    if not secret:
        return ""
    return f"…{secret[-4:]}" if len(secret) >= 4 else f"…{secret}"


def allows(record: KeyRecord, model_id: str) -> bool:
    """Whether this key may be used for this model (fnmatch globs)."""
    patterns = record.allow_models or ["*"]
    return any(fnmatch(model_id, p) for p in patterns)


def _path(path: Path | str | None) -> Path:
    return Path(path) if path else home.keys_path()


def load_keys(path: Path | str | None = None) -> dict[str, list[KeyRecord]]:
    raw = read_json(_path(path), default={})
    out: dict[str, list[KeyRecord]] = {}
    for provider, entries in (raw or {}).items():
        records = []
        for e in entries or []:
            records.append(KeyRecord(
                id=e.get("id", ""),
                secret=e.get("secret", ""),
                label=e.get("label", ""),
                added_at=e.get("added_at", ""),
                weight=int(e.get("weight", 1)),
                allow_models=list(e.get("allow_models") or ["*"]),
                enabled=bool(e.get("enabled", True)),
                source=e.get("source", "keys"),
            ))
        out[provider] = records
    return out


def save_keys(mapping: dict[str, list[KeyRecord]], path: Path | str | None = None) -> None:
    target = _path(path)
    raw = {p: [asdict(r) for r in records] for p, records in mapping.items()}
    write_json(target, raw)
    harden(target)


def add_key(provider: str, secret: str, label: str = "",
            path: Path | str | None = None) -> KeyRecord:
    mapping = load_keys(path)
    existing = mapping.setdefault(provider, [])
    record = KeyRecord(
        id=f"{provider}-{len(existing) + 1}",
        secret=secret,
        label=label,
        added_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    existing.append(record)
    save_keys(mapping, path)
    return record


def remove_key(provider: str, key_id: str, path: Path | str | None = None) -> bool:
    mapping = load_keys(path)
    records = mapping.get(provider, [])
    kept = [r for r in records if r.id != key_id]
    if len(kept) == len(records):
        return False
    mapping[provider] = kept
    save_keys(mapping, path)
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_keys.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add flexrouter/keys.py tests/test_keys.py
git commit -m "feat(keys): keys.json vault with masking, weights, and allow_models globs"
```

---

### Task 4: Overrides layered over the settings file

**Files:**
- Create: `flexrouter/overrides.py`
- Test: `tests/test_overrides.py`

**Interfaces:**
- Consumes: `flexrouter.home.overrides_path`, `flexrouter.store.read_json/write_json`.
- Produces: `load_overrides(path=None) -> dict`,
  `save_overrides(data, path=None) -> None`,
  `set_override(section, key, field, value, path=None) -> None`,
  `clear_override(section, key, path=None) -> bool`,
  `apply_overrides(raw: dict, ov: dict) -> dict`, `SECTIONS: tuple[str, ...]`.
  Override file shape:
  `{"settings": {...}, "providers": {name: {...}}, "models": {"prov/model": {...}}}`.
  A model override of `{"enabled": false}` drops that model from its bucket.
  For the `settings` section, `set_override` ignores `field` and uses `key` as
  the settings name.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_overrides.py
import pytest

from flexrouter.overrides import (
    apply_overrides, clear_override, load_overrides, save_overrides, set_override,
)


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path))
    return tmp_path


BASE = {
    "settings": {"port": 4891, "window_seconds": 60},
    "providers": {"openrouter": {"base_url": "https://openrouter.ai/api/v1"}},
    "buckets": {
        "smart": [
            {"provider": "openrouter", "model": "deepseek-chat", "score": 90,
             "rpm": 20, "tpm": 10000},
            {"provider": "openrouter", "model": "gone-model", "score": 50,
             "rpm": 20, "tpm": 10000},
        ]
    },
}


def test_load_overrides_is_empty_at_first():
    assert load_overrides() == {}


def test_set_and_load_an_override():
    set_override("models", "openrouter/deepseek-chat", "score", 75)
    assert load_overrides()["models"]["openrouter/deepseek-chat"]["score"] == 75


def test_set_override_on_settings_uses_the_key_as_the_name():
    set_override("settings", "port", None, 7000)
    assert load_overrides()["settings"]["port"] == 7000


def test_set_override_rejects_an_unknown_section():
    with pytest.raises(ValueError):
        set_override("nonsense", "x", "y", 1)


def test_clear_override_removes_the_entry():
    set_override("models", "openrouter/deepseek-chat", "score", 75)
    assert clear_override("models", "openrouter/deepseek-chat") is True
    assert load_overrides().get("models", {}) == {}


def test_clear_override_reports_when_nothing_matched():
    assert clear_override("models", "openrouter/nope") is False


def test_apply_overrides_leaves_the_original_untouched():
    merged = apply_overrides(BASE, {"settings": {"port": 9999}})
    assert merged["settings"]["port"] == 9999
    assert BASE["settings"]["port"] == 4891


def test_apply_overrides_merges_settings_field_by_field():
    merged = apply_overrides(BASE, {"settings": {"port": 9999}})
    assert merged["settings"]["window_seconds"] == 60


def test_apply_overrides_merges_a_provider_field():
    merged = apply_overrides(
        BASE, {"providers": {"openrouter": {"base_url": "http://localhost:1234/v1"}}})
    assert merged["providers"]["openrouter"]["base_url"] == "http://localhost:1234/v1"


def test_apply_overrides_changes_a_model_score():
    merged = apply_overrides(
        BASE, {"models": {"openrouter/deepseek-chat": {"score": 75}}})
    smart = merged["buckets"]["smart"]
    assert smart[0]["score"] == 75
    assert smart[0]["rpm"] == 20


def test_apply_overrides_disabling_a_model_drops_it_from_the_bucket():
    merged = apply_overrides(
        BASE, {"models": {"openrouter/gone-model": {"enabled": False}}})
    assert [m["model"] for m in merged["buckets"]["smart"]] == ["deepseek-chat"]


def test_apply_overrides_also_understands_the_tiers_spelling():
    base = {"tiers": {"fast": [{"provider": "groq", "model": "llama", "score": 80,
                                "rpm": 30, "tpm": 6000}]}}
    merged = apply_overrides(base, {"models": {"groq/llama": {"score": 10}}})
    assert merged["tiers"]["fast"][0]["score"] == 10


def test_apply_overrides_with_nothing_set_is_a_faithful_copy():
    assert apply_overrides(BASE, {}) == BASE


def test_save_overrides_round_trips():
    save_overrides({"settings": {"port": 1}})
    assert load_overrides() == {"settings": {"port": 1}}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_overrides.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flexrouter.overrides'`

- [ ] **Step 3: Write the implementation**

```python
# flexrouter/overrides.py
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

    for name, fields in (ov.get("providers") or {}).items():
        target = merged.setdefault("providers", {}).setdefault(name, {})
        target.update(fields or {})

    model_ov = ov.get("models") or {}
    bkey = _bucket_key(merged)
    if model_ov and bkey:
        for bucket_name, models in list((merged.get(bkey) or {}).items()):
            kept = []
            for entry in models or []:
                ident = f"{entry.get('provider')}/{entry.get('model')}"
                fields = dict(model_ov.get(ident) or {})
                if fields.pop("enabled", True) is False:
                    continue
                entry.update(fields)
                kept.append(entry)
            merged[bkey][bucket_name] = kept

    return merged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_overrides.py -v`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add flexrouter/overrides.py tests/test_overrides.py
git commit -m "feat(overrides): dashboard changes layered over a never-rewritten config.yaml"
```

---

### Task 5: Rewire config loading onto the home

**Files:**
- Modify: `flexrouter/config.py` (delete `discover_config`, add `resolve_keys`,
  home-aware `load_config`, `buckets`/`tiers` alias, `port` setting)
- Modify: `tests/test_config.py:3,62-103` (delete the three `discover_config` tests)
- Test: `tests/test_config_home.py` (new)

**Interfaces:**
- Consumes: `flexrouter.home`, `flexrouter.keys`, `flexrouter.overrides`.
- Produces:
  - `ProviderConfig(base_url: str, api_keys: list[str], header_parser: str = "openai_compatible", keys: list[KeyRecord] = [])`
    — `api_keys` keeps its exact current meaning (resolved secret strings, in
    order) so `engine.py` and `client.py` are untouched; `keys` carries the
    full records for later stages.
  - `FlexConfig.port: int = 4891` alongside the existing
    `dashboard_port` (deprecated alias, set to the same value).
  - `load_config(path: Path | str | None = None) -> FlexConfig` — defaults to
    the home's `config.yaml`, calls `home.ensure_home()`, and applies overrides.
  - `resolve_keys(provider: str, praw: dict, vault: dict[str, list[KeyRecord]], source_path: Path | None) -> list[KeyRecord]`
    implementing the credential order: stored key → env var → inline (warn).
  - `discover_config` **no longer exists**.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config_home.py
import warnings

import pytest
import yaml

from flexrouter import home
from flexrouter.config import load_config, resolve_keys
from flexrouter.keys import KeyRecord, add_key
from flexrouter.overrides import set_override

SETTINGS = {
    "settings": {"port": 4891},
    "providers": {
        "openrouter": {"base_url": "https://openrouter.ai/api/v1"},
    },
    "buckets": {
        "smart": [{"provider": "openrouter", "model": "deepseek-chat",
                   "score": 99, "rpm": 20, "tpm": 10000}],
    },
}


@pytest.fixture
def written_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path))
    home.ensure_home()
    home.config_path().write_text(yaml.dump(SETTINGS), encoding="utf-8")
    return tmp_path


def test_discover_config_is_gone():
    import flexrouter.config as config_module
    assert not hasattr(config_module, "discover_config")


def test_load_config_defaults_to_the_home(written_home):
    cfg = load_config()
    assert cfg.port == 4891
    assert "smart" in cfg.tiers


def test_load_config_accepts_the_buckets_spelling(written_home):
    assert [m.model for m in load_config().tiers["smart"]] == ["deepseek-chat"]


def test_load_config_still_accepts_the_tiers_spelling(written_home):
    home.config_path().write_text(
        yaml.dump({"settings": SETTINGS["settings"],
                   "providers": SETTINGS["providers"],
                   "tiers": SETTINGS["buckets"]}),
        encoding="utf-8")
    assert "smart" in load_config().tiers


def test_load_config_creates_a_starter_home_when_there_is_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path / "fresh"))
    cfg = load_config()
    assert cfg.port == 4891
    assert cfg.tiers == {"smart": [], "fast": [], "long": []}


def test_load_config_applies_an_override(written_home):
    set_override("models", "openrouter/deepseek-chat", "score", 42)
    assert load_config().tiers["smart"][0].score == 42


def test_load_config_never_rewrites_the_settings_file(written_home):
    before = home.config_path().read_bytes()
    set_override("settings", "port", None, 7000)
    load_config()
    assert home.config_path().read_bytes() == before


def test_state_dir_defaults_into_the_home(written_home):
    assert load_config().state_dir == str(home.state_dir())


def test_dashboard_port_is_still_accepted_as_an_alias(written_home):
    home.config_path().write_text(
        yaml.dump({"settings": {"dashboard_port": 7352},
                   "providers": SETTINGS["providers"],
                   "buckets": SETTINGS["buckets"]}), encoding="utf-8")
    cfg = load_config()
    assert cfg.port == 7352
    assert cfg.dashboard_port == 7352


def test_stored_key_beats_an_env_var(written_home, monkeypatch):
    add_key("openrouter", "from-vault")
    monkeypatch.setenv("OR_KEY", "from-env")
    cfg = load_config()
    assert cfg.providers["openrouter"].api_keys == ["from-vault"]
    assert cfg.providers["openrouter"].keys[0].source == "keys"


def test_env_var_is_used_when_the_vault_is_empty(written_home, monkeypatch):
    home.config_path().write_text(yaml.dump({
        "settings": SETTINGS["settings"],
        "providers": {"openrouter": {"base_url": "https://x/v1",
                                     "api_key_env": "OR_KEY"}},
        "buckets": SETTINGS["buckets"],
    }), encoding="utf-8")
    monkeypatch.setenv("OR_KEY", "from-env")
    cfg = load_config()
    assert cfg.providers["openrouter"].api_keys == ["from-env"]
    assert cfg.providers["openrouter"].keys[0].source == "env"


def test_disabled_stored_keys_are_not_offered(written_home):
    from flexrouter.keys import save_keys
    save_keys({"openrouter": [
        KeyRecord(id="a", secret="off", enabled=False),
        KeyRecord(id="b", secret="on"),
    ]})
    assert load_config().providers["openrouter"].api_keys == ["on"]


def test_an_inline_key_still_works_but_warns_loudly(written_home):
    home.config_path().write_text(yaml.dump({
        "settings": SETTINGS["settings"],
        "providers": {"openrouter": {"base_url": "https://x/v1",
                                     "api_key": "sk-inline-secret"}},
        "buckets": SETTINGS["buckets"],
    }), encoding="utf-8")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = load_config()
    assert cfg.providers["openrouter"].api_keys == ["sk-inline-secret"]
    assert cfg.providers["openrouter"].keys[0].source == "inline"
    message = str(caught[0].message)
    assert "config.yaml" in message
    assert "line" in message
    assert "sk-inline-secret" not in message


def test_a_provider_with_no_credentials_anywhere_loads_with_none(written_home):
    cfg = load_config()
    assert cfg.providers["openrouter"].api_keys == []


def test_resolve_keys_prefers_the_vault():
    vault = {"groq": [KeyRecord(id="groq-1", secret="v")]}
    got = resolve_keys("groq", {"api_key_env": "NOPE"}, vault, None)
    assert [r.secret for r in got] == ["v"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config_home.py -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_keys' from 'flexrouter.config'`

- [ ] **Step 3: Rewrite the settings-loading half of `flexrouter/config.py`**

Replace the imports, `ProviderConfig`, `FlexConfig`, and `load_config`, and
delete `discover_config` entirely. Leave `RETRY_PRESETS`, `ModelConfig`,
`RetryConfig`, `NON_CHAT_PATTERNS`, `_LOCAL_HOST_HINTS`,
`is_probably_chat_model` and `validate_config` exactly as they are.

```python
# flexrouter/config.py — replace the top imports with:
from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from flexrouter import home
from flexrouter.exceptions import ConfigError
from flexrouter.keys import KeyRecord, load_keys
from flexrouter.overrides import apply_overrides, load_overrides
```

```python
# flexrouter/config.py — replace ProviderConfig with:
@dataclass
class ProviderConfig:
    base_url: str
    api_keys: list[str]  # resolved secret strings, in selection order
    header_parser: str = "openai_compatible"
    keys: list[KeyRecord] = field(default_factory=list)
```

```python
# flexrouter/config.py — in FlexConfig, add `port` directly above `dashboard_port`:
    port: int = 4891
    dashboard_port: int = 7352  # deprecated alias for `port`
```

```python
# flexrouter/config.py — add above load_config():
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
    """Credential resolution order (spec §1): stored key, then environment
    variable, then an inline secret — which is deprecated and warns."""
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
```

```python
# flexrouter/config.py — replace load_config() with:
def load_config(path: Path | str | None = None) -> FlexConfig:
    """Load settings from the flexrouter home, with overrides layered on top."""
    home.ensure_home()
    source = Path(path) if path else home.config_path()
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except Exception as e:
        raise ConfigError(f"Cannot read {source}: {e}") from e

    if not raw:
        raise ConfigError(f"{source} is empty")

    raw = apply_overrides(raw, load_overrides())
    settings = raw.get("settings") or {}

    preset_name = settings.get("retry_policy", "balanced")
    preset = RETRY_PRESETS.get(preset_name, RETRY_PRESETS["balanced"])
    retry = RetryConfig(
        retries=settings.get("retries", preset["retries"]),
        backoff_seconds=float(settings.get("backoff_seconds", preset["backoff_seconds"])),
    )

    vault = load_keys()
    providers: dict[str, ProviderConfig] = {}
    for name, praw in (raw.get("providers") or {}).items():
        praw = praw or {}
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
        tiers[bucket_name] = [
            ModelConfig(
                provider=m["provider"],
                model=m["model"],
                score=m["score"],
                rpm=m["rpm"],
                tpm=m["tpm"],
                context_window=m.get("context_window", 200000),
                vision=m.get("vision", False),
                quotas=m.get("quotas", {}),
            )
            for m in (models or [])
        ]

    port = int(settings.get("port", settings.get("dashboard_port", home.DEFAULT_PORT)))
    return FlexConfig(
        tiers=tiers,
        providers=providers,
        state_dir=settings.get("state_dir", str(home.state_dir())),
        window_seconds=int(settings.get("window_seconds", 60)),
        penalty_base_seconds=int(settings.get("penalty_base_seconds", 30)),
        penalty_max_seconds=int(settings.get("penalty_max_seconds", 1800)),
        session_ttl_minutes=int(settings.get("session_ttl_minutes", 30)),
        port=port,
        dashboard_port=port,
        sample_interval_seconds=int(settings.get("sample_interval_seconds", 60)),
        health_history_days=int(settings.get("health_history_days", 30)),
        retry=retry,
        provider_budget=settings.get("provider_budget", {}),
        hooks=settings.get("hooks", []),
    )
```

Then delete the whole `discover_config()` function, including its docstring,
and drop the now-unused `from typing import Optional` import if nothing else
uses it.

- [ ] **Step 4: Delete the obsolete discovery tests**

Delete `test_discover_config_finds_cwd`, `test_discover_config_env_var_takes_precedence`
and `test_discover_config_ignores_missing_env_var_path` from
`tests/test_config.py:62-103`, and drop `discover_config` from that file's
import on line 3.

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_config_home.py tests/test_config.py tests/test_config_validate.py -v`
Expected: all pass. `load_config(path)` with an explicit path still works, so
the remaining `test_config.py` cases keep passing unchanged. If a
`test_config.py` case writes an inline key and now trips the deprecation
warning, that is correct behaviour — leave the warning and let the test see it.

- [ ] **Step 6: Commit**

```bash
git add flexrouter/config.py tests/test_config.py tests/test_config_home.py
git commit -m "feat(config): load from the fixed home; delete the cwd search chain"
```

---

### Task 6: Point every caller at the home

**Files:**
- Modify: `flexrouter/_router.py:12` (import) and `:69-77` (constructor)
- Modify: `flexrouter/dashboard/api.py:1-90` (`get_config`, `post_config`, `run_refresh`)
- Modify: `flexrouter/cli.py` (drop `discover_config`, use the home)
- Modify: `tests/test_cli_refresh.py:9`
- Test: `tests/test_dashboard_api.py` (append the post_config cases)

**Interfaces:**
- Consumes: `load_config` from Task 5, `flexrouter.home`, `flexrouter.overrides`.
- Produces: `dashboard.api.get_config() -> dict` (the home's settings with
  overrides applied), `dashboard.api.post_config(raw: dict) -> None` (writes
  **overrides only**, never the YAML; raises `ValueError` if handed
  credentials), `dashboard.api.get_overrides() -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dashboard_api.py — append these
import pytest

from flexrouter import home
from flexrouter.dashboard import api


@pytest.fixture
def api_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path))
    home.ensure_home()
    home.config_path().write_text(
        "# keep me\nsettings:\n  port: 4891\nproviders: {}\nbuckets:\n  smart: []\n",
        encoding="utf-8")
    return tmp_path


def test_get_config_reads_the_home(api_home):
    assert api.get_config()["settings"]["port"] == 4891


def test_post_config_never_touches_the_settings_file(api_home):
    before = home.config_path().read_bytes()
    api.post_config({"settings": {"port": 7000}})
    assert home.config_path().read_bytes() == before
    assert "# keep me" in home.config_path().read_text(encoding="utf-8")


def test_post_config_writes_an_override_instead(api_home):
    api.post_config({"settings": {"port": 7000}})
    assert api.get_overrides()["settings"]["port"] == 7000


def test_post_config_shows_up_in_get_config(api_home):
    api.post_config({"settings": {"port": 7000}})
    assert api.get_config()["settings"]["port"] == 7000


def test_post_config_rejects_credentials(api_home):
    with pytest.raises(ValueError):
        api.post_config({"providers": {"groq": {"api_key": "sk-nope"}}})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_dashboard_api.py -v`
Expected: FAIL — `AttributeError: module 'flexrouter.dashboard.api' has no attribute 'get_overrides'`

- [ ] **Step 3: Rewrite the settings half of `flexrouter/dashboard/api.py`**

```python
# flexrouter/dashboard/api.py — line 4 becomes:
from flexrouter.config import validate_config
```

```python
# flexrouter/dashboard/api.py — replace get_config/post_config with:
def get_config() -> dict:
    """The settings as flexrouter sees them: the file, plus overrides."""
    import yaml

    from flexrouter import home
    from flexrouter.overrides import apply_overrides, load_overrides

    home.ensure_home()
    raw = yaml.safe_load(home.config_path().read_text(encoding="utf-8")) or {}
    return apply_overrides(raw, load_overrides())


def get_overrides() -> dict:
    from flexrouter.overrides import load_overrides
    return load_overrides()


def post_config(raw: dict) -> None:
    """Record a settings change as an override.

    config.yaml is hand-written and is never rewritten — that is what keeps
    the owner's comments alive (spec §1).
    """
    from flexrouter.overrides import load_overrides, save_overrides

    for name, praw in (raw.get("providers") or {}).items():
        if not isinstance(praw, dict):
            continue
        if praw.get("api_key") or praw.get("api_keys"):
            raise ValueError(
                f"Credentials for {name!r} don't go in settings — "
                f"add them with: flexrouter keys add {name}")

    data = load_overrides()
    for key, value in (raw.get("settings") or {}).items():
        data.setdefault("settings", {})[key] = value
    for name, fields in (raw.get("providers") or {}).items():
        data.setdefault("providers", {}).setdefault(name, {}).update(fields or {})
    for ident, fields in (raw.get("models") or {}).items():
        data.setdefault("models", {}).setdefault(ident, {}).update(fields or {})
    save_overrides(data)
```

```python
# flexrouter/dashboard/api.py — replace run_refresh with:
def run_refresh(state_dir: str) -> dict:
    from dataclasses import asdict

    from flexrouter import home
    from flexrouter.refresh import refresh_config
    return asdict(refresh_config(str(home.config_path()), state_dir))
```

`get_config_validation()` keeps calling `validate_config(get_config())` and now
validates the merged view, which is what the dashboard should be showing.

- [ ] **Step 4: Point `_router.py` at the home**

```python
# flexrouter/_router.py — line 12 becomes:
from flexrouter.config import FlexConfig, load_config
```

```python
# flexrouter/_router.py — replace the opening of __init__ (lines 69-77) with:
    def __init__(self, config_path: Optional[str] = None) -> None:
        from flexrouter import home

        path = Path(config_path) if config_path else home.config_path()
        self._config_path = path
        self._cfg: FlexConfig = load_config(path if config_path else None)
```

`load_config` now creates the home and its starter settings on first use, so
the old `ConfigError("No flexrouter.yaml found")` branch is gone — there is
always a settings file. Delete that branch.

- [ ] **Step 5: Point `cli.py` at the home**

In `flexrouter/cli.py`:

1. Change line 9 to `from flexrouter.config import load_config` and add
   `from flexrouter import home` to the imports.
2. Replace every `config_path or discover_config()` and bare `discover_config()`
   with `Path(config_path) if config_path else home.config_path()`.
3. `status`, `refresh` and `config_export` each bail out with "No
   flexrouter.yaml found." — delete those three guards; the home always has a
   settings file.
4. In `_resolve_port`, change the final fallback from `7352` to
   `home.DEFAULT_PORT`, and read `load_config(...).port` instead of
   `.dashboard_port`.
5. In `_run_daemon`, replace the "No flexrouter.yaml found" error branch with
   `click.echo(f"Settings: {home.config_path()}")`.
6. `_config_option`: drop `envvar="FLEXROUTER_CONFIG"` — `FLEXROUTER_HOME` is
   the one override now — and set the help text to
   `"Settings file to use. Defaults to the flexrouter home (see: flexrouter doctor)."`
7. Replace `config_import`'s body, which currently writes `flexrouter.yaml`
   into the cwd:

```python
@config.command("import")
@click.argument("token")
def config_import(token: str):
    """Show settings from a token (it will not overwrite yours)."""
    data = base64.b64decode(token.encode()).decode("utf-8", "replace")
    click.echo("flexrouter never overwrites your settings file. Here it is — "
               f"paste what you want into {home.config_path()}:\n")
    click.echo(data)
```

- [ ] **Step 6: Fix the one test that patched the old discovery**

```python
# tests/test_cli_refresh.py — line 9 becomes:
    monkeypatch.setattr(cli.home, "config_path", lambda: cfg)
```

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest -q`
Expected: everything passes except `tests/test_server.py` and
`tests/test_dashboard_server.py`, which Task 9 deletes. If any other test fails
because it wrote to the real home, add
`monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path))` to it.

- [ ] **Step 8: Commit**

```bash
git add flexrouter/_router.py flexrouter/cli.py flexrouter/dashboard/api.py tests/
git commit -m "feat: every caller reads the shared home; dashboard edits become overrides"
```

---

### Task 7: `flexrouter keys` — add, list, remove, and lift keys across

**Files:**
- Modify: `flexrouter/cli.py` (new `keys` group)
- Test: `tests/test_cli_keys.py`

**Interfaces:**
- Consumes: `flexrouter.keys.add_key/remove_key/load_keys/mask`.
- Produces: CLI commands `flexrouter keys list`, `flexrouter keys add <provider>`,
  `flexrouter keys rm <provider> <key-id>`, `flexrouter keys import <old-file>`.
  Plus `_import_keys_from(path) -> list[tuple[str, str]]` in `cli.py`,
  returning `(provider, key_id)` for each key lifted, and the module alias
  `keyvault` (`from flexrouter import keys as keyvault`) which Task 8 also uses.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli_keys.py
import pytest
import yaml
from click.testing import CliRunner

from flexrouter import cli, home
from flexrouter.keys import load_keys


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path))
    home.ensure_home()
    return tmp_path


def test_keys_add_stores_the_key():
    result = CliRunner().invoke(cli.cli, ["keys", "add", "groq", "--secret", "gsk-abcd"])
    assert result.exit_code == 0
    assert load_keys()["groq"][0].secret == "gsk-abcd"


def test_keys_add_never_echoes_the_secret():
    result = CliRunner().invoke(cli.cli, ["keys", "add", "groq", "--secret", "gsk-abcd"])
    assert "gsk-abcd" not in result.output
    assert "…abcd" in result.output


def test_keys_list_shows_masked_values_only():
    CliRunner().invoke(cli.cli, ["keys", "add", "groq", "--secret", "gsk-abcd"])
    result = CliRunner().invoke(cli.cli, ["keys", "list"])
    assert "gsk-abcd" not in result.output
    assert "…abcd" in result.output
    assert "groq-1" in result.output


def test_keys_list_says_so_when_empty():
    result = CliRunner().invoke(cli.cli, ["keys", "list"])
    assert result.exit_code == 0
    assert "no keys" in result.output.lower()


def test_keys_rm_removes_it():
    CliRunner().invoke(cli.cli, ["keys", "add", "groq", "--secret", "gsk-abcd"])
    result = CliRunner().invoke(cli.cli, ["keys", "rm", "groq", "groq-1"])
    assert result.exit_code == 0
    assert load_keys().get("groq") == []


def test_keys_rm_is_clear_when_there_is_no_such_key():
    result = CliRunner().invoke(cli.cli, ["keys", "rm", "groq", "groq-9"])
    assert result.exit_code == 1
    assert "no key" in result.output.lower()


def test_keys_import_lifts_keys_out_of_an_old_settings_file(tmp_path):
    old = tmp_path / "old-flexrouter.yaml"
    old.write_text(yaml.dump({
        "providers": {
            "groq": {"base_url": "https://api.groq.com/openai/v1",
                     "api_keys": ["gsk-one", {"key": "gsk-two"}]},
            "ollama": {"base_url": "http://localhost:11434/v1", "api_keys": []},
        }
    }), encoding="utf-8")

    result = CliRunner().invoke(cli.cli, ["keys", "import", str(old)])
    assert result.exit_code == 0
    assert [r.secret for r in load_keys()["groq"]] == ["gsk-one", "gsk-two"]
    assert "gsk-one" not in result.output


def test_keys_import_skips_env_var_references(tmp_path):
    old = tmp_path / "old-flexrouter.yaml"
    old.write_text(yaml.dump({
        "providers": {"groq": {"base_url": "https://x/v1",
                               "api_keys": [{"env": "GROQ_API_KEY"}]}}
    }), encoding="utf-8")
    CliRunner().invoke(cli.cli, ["keys", "import", str(old)])
    assert load_keys() == {}


def test_keys_import_does_not_modify_the_old_file(tmp_path):
    old = tmp_path / "old-flexrouter.yaml"
    old.write_text("providers:\n  groq:\n    base_url: https://x/v1\n"
                   "    api_keys: [gsk-one]  # my note\n", encoding="utf-8")
    before = old.read_bytes()
    CliRunner().invoke(cli.cli, ["keys", "import", str(old)])
    assert old.read_bytes() == before
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cli_keys.py -v`
Expected: FAIL — exit code 2, `Error: No such command 'keys'`

- [ ] **Step 3: Add the `keys` group to `flexrouter/cli.py`**

```python
# flexrouter/cli.py — append
from flexrouter import keys as keyvault


@cli.group()
def keys():
    """Add, list, and remove your API keys."""


@keys.command("list")
def keys_list():
    """Show your saved keys (masked — the full value is never printed)."""
    vault = keyvault.load_keys()
    if not any(vault.values()):
        click.echo("No keys saved yet. Add one with: flexrouter keys add <provider>")
        return
    for provider, records in sorted(vault.items()):
        click.echo(provider)
        for r in records:
            state = "" if r.enabled else "  (off)"
            label = f"  {r.label}" if r.label else ""
            click.echo(f"  {r.id:<16} {keyvault.mask(r.secret)}{label}{state}")


@keys.command("add")
@click.argument("provider")
@click.option("--secret", prompt=True, hide_input=True,
              help="The key itself. Leave it off and you'll be asked without it showing.")
@click.option("--label", default="", help="A name to recognise it by.")
def keys_add(provider: str, secret: str, label: str):
    """Save a key for PROVIDER."""
    record = keyvault.add_key(provider, secret.strip(), label=label)
    click.echo(f"Saved {record.id} for {provider}: {keyvault.mask(record.secret)}")


@keys.command("rm")
@click.argument("provider")
@click.argument("key_id")
def keys_rm(provider: str, key_id: str):
    """Remove a saved key."""
    if not keyvault.remove_key(provider, key_id):
        click.echo(f"No key {key_id!r} for {provider}.", err=True)
        raise SystemExit(1)
    click.echo(f"Removed {key_id} from {provider}.")


def _import_keys_from(path: Path) -> list[tuple[str, str]]:
    """Lift plain secrets out of an old settings file. Env references are
    left alone — they already resolve. The old file is never modified."""
    import yaml

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    added: list[tuple[str, str]] = []
    for provider, praw in (raw.get("providers") or {}).items():
        if not isinstance(praw, dict):
            continue
        secrets: list[str] = []
        if praw.get("api_key"):
            secrets.append(str(praw["api_key"]))
        entries = praw.get("api_keys", [])
        if isinstance(entries, str):
            entries = []
        for e in entries or []:
            if isinstance(e, str) and e:
                secrets.append(e)
            elif isinstance(e, dict) and e.get("key"):
                secrets.append(str(e["key"]))
        for s in secrets:
            added.append((provider, keyvault.add_key(provider, s, label="imported").id))
    return added


@keys.command("import")
@click.argument("old_file", type=click.Path(exists=True, dir_okay=False))
def keys_import(old_file: str):
    """Copy the keys out of an old flexrouter.yaml into the shared home."""
    added = _import_keys_from(Path(old_file))
    if not added:
        click.echo("Found no keys to copy — that file only references "
                   "environment variables, which already work as they are.")
        return
    for provider, key_id in added:
        click.echo(f"Copied {provider} -> {key_id}")
    click.echo(f"\nCopied {len(added)} key(s). Your old file was not changed; "
               f"delete it when you're happy.")
```

Note the `keys` command group shadows nothing: `flexrouter/keys.py` is imported
as `keyvault` precisely so the click group can own the name `keys`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli_keys.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add flexrouter/cli.py tests/test_cli_keys.py
git commit -m "feat(cli): flexrouter keys add/list/rm/import"
```

---

### Task 8: `flexrouter doctor`

**Files:**
- Modify: `flexrouter/cli.py` (new `doctor` command)
- Test: `tests/test_cli_doctor.py`

**Interfaces:**
- Consumes: `flexrouter.home`, `flexrouter.config.load_config`,
  `keyvault.mask` (Task 7's alias), `flexrouter.overrides.load_overrides`.
- Produces: CLI command `flexrouter doctor`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli_doctor.py
import pytest
import yaml
from click.testing import CliRunner

from flexrouter import cli, home

SETTINGS = {
    "settings": {"port": 4891},
    "providers": {
        "openrouter": {"base_url": "https://openrouter.ai/api/v1"},
        "groq": {"base_url": "https://api.groq.com/openai/v1",
                 "api_key_env": "GROQ_API_KEY"},
        "cerebras": {"base_url": "https://api.cerebras.ai/v1"},
    },
    "buckets": {"smart": [{"provider": "openrouter", "model": "deepseek-chat",
                           "score": 90, "rpm": 20, "tpm": 10000}]},
}


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path))
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    home.ensure_home()
    home.config_path().write_text(yaml.dump(SETTINGS), encoding="utf-8")
    return tmp_path


def test_doctor_prints_the_home(_home):
    result = CliRunner().invoke(cli.cli, ["doctor"])
    assert result.exit_code == 0
    assert str(_home) in result.output


def test_doctor_says_where_each_key_came_from(monkeypatch):
    from flexrouter.keys import add_key
    add_key("openrouter", "sk-or-abcd")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-env")

    result = CliRunner().invoke(cli.cli, ["doctor"])
    assert "openrouter" in result.output
    assert "…abcd" in result.output
    assert "GROQ_API_KEY" in result.output
    assert "no key" in result.output.lower()  # cerebras


def test_doctor_never_prints_a_secret():
    from flexrouter.keys import add_key
    add_key("openrouter", "sk-or-abcd")
    result = CliRunner().invoke(cli.cli, ["doctor"])
    assert "sk-or-abcd" not in result.output


def test_doctor_counts_buckets_and_models():
    result = CliRunner().invoke(cli.cli, ["doctor"])
    assert "1 bucket" in result.output
    assert "1 model" in result.output


def test_doctor_reports_unreadable_settings(_home):
    home.config_path().write_text("settings: [this: is: not: valid\n", encoding="utf-8")
    result = CliRunner().invoke(cli.cli, ["doctor"])
    assert result.exit_code == 1
    assert "could not read" in result.output.lower()


def test_doctor_lists_overrides():
    from flexrouter.overrides import set_override
    set_override("models", "openrouter/deepseek-chat", "score", 42)
    result = CliRunner().invoke(cli.cli, ["doctor"])
    assert "openrouter/deepseek-chat" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cli_doctor.py -v`
Expected: FAIL — exit code 2, `Error: No such command 'doctor'`

- [ ] **Step 3: Add the `doctor` command to `flexrouter/cli.py`**

```python
# flexrouter/cli.py — append
@cli.command()
def doctor():
    """Show where flexrouter keeps things and which key it will use."""
    import os
    import warnings

    from flexrouter import overrides as ov

    home.ensure_home()
    click.echo(f"flexrouter home: {home.home_dir()}")
    click.echo(f"  settings   {home.config_path().name}")
    click.echo(f"  keys       {home.keys_path().name}")
    click.echo(f"  changes    {home.overrides_path().name}")
    click.echo(f"  records    {home.state_dir().name}{os.sep}")
    click.echo("")

    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            cfg = load_config()
    except Exception as e:
        click.echo(f"Could not read your settings: {e}", err=True)
        raise SystemExit(1)

    models = sum(len(v) for v in cfg.tiers.values())
    click.echo(f"{len(cfg.tiers)} bucket(s), {models} model(s), "
               f"{len(cfg.providers)} provider(s). Serving on port {cfg.port}.")
    click.echo("")

    click.echo("Which key each provider will use:")
    for name, provider in sorted(cfg.providers.items()):
        if not provider.keys:
            click.echo(f"  {name:<14} no key found")
            continue
        first = provider.keys[0]
        if first.source == "env":
            where = f"{first.label} (environment)"
        elif first.source == "inline":
            where = (f"typed into {home.config_path().name} — move it with: "
                     f"flexrouter keys add {name}")
        else:
            where = f"saved key {first.id}  {keyvault.mask(first.secret)}"
        extra = f"  (+{len(provider.keys) - 1} more)" if len(provider.keys) > 1 else ""
        click.echo(f"  {name:<14} {where}{extra}")

    changes = ov.load_overrides()
    click.echo("")
    if not changes:
        click.echo("No dashboard changes on top of your settings file.")
    else:
        click.echo("Changes layered on top of your settings file:")
        for section in ov.SECTIONS:
            for key, value in (changes.get(section) or {}).items():
                click.echo(f"  {key}: {value}")

    for w in caught:
        click.echo(f"\n! {w.message}", err=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli_doctor.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add flexrouter/cli.py tests/test_cli_doctor.py
git commit -m "feat(cli): flexrouter doctor — resolved home and credential sources"
```

---

### Task 9: Delete the superseded modules and update the docs

**Files:**
- Delete: `flexrouter/server.py`, `tests/test_server.py`,
  `flexrouter/dashboard/server.py`, `tests/test_dashboard_server.py`
- Modify: `flexrouter/__init__.py:29,32-35` (drop `start_server`)
- Modify: `docs/1-Getting-Started.md`, `docs/5-API-Reference.md`
- Test: `tests/test_public_surface.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `flexrouter.__all__` no longer contains `"start_server"`.

Owner decision, 2026-09-18: these four files are superseded by `app.py` and the
dashboard API, nothing references them, and they are removed. They stay in git
history if they are ever wanted back.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_public_surface.py
import importlib

import pytest


def test_start_server_is_gone():
    import flexrouter
    assert "start_server" not in flexrouter.__all__
    assert not hasattr(flexrouter, "start_server")


def test_the_old_server_modules_are_gone():
    for name in ("flexrouter.server", "flexrouter.dashboard.server"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_public_surface.py -v`
Expected: FAIL — `assert 'start_server' not in [...]`

- [ ] **Step 3: Delete the files**

```bash
git rm flexrouter/server.py tests/test_server.py flexrouter/dashboard/server.py tests/test_dashboard_server.py
```

- [ ] **Step 4: Drop `start_server` from `flexrouter/__init__.py`**

Remove the string `"start_server",` from `__all__` (line 29) and delete the
`start_server` function at the bottom of the file (lines 32-35), along with the
now-unneeded trailing blank lines.

- [ ] **Step 5: Update the two docs pages**

In `docs/1-Getting-Started.md` and `docs/5-API-Reference.md`, replace every
mention of a settings file found next to your project, of `$FLEXROUTER_CONFIG`,
and of `flexrouter.start_server()`, with the new story:

- settings and keys live in one place; find it with `flexrouter doctor`
- `FLEXROUTER_HOME` moves that place
- keys go in with `flexrouter keys add <provider>`, never in the settings file
- the service starts with `flexrouter serve` and listens on port 4891

Run `grep -rn "FLEXROUTER_CONFIG\|start_server\|flexrouter.yaml" docs/*.md` and
fix every hit.

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: all pass, no collection errors.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: delete the superseded server modules; docs point at the shared home"
```

---

### Task 10: End-to-end check of the stage

**Files:**
- Test: `tests/test_shared_home_e2e.py`

**Interfaces:**
- Consumes: everything above. Produces nothing new.

This is the gate for the whole stage: it proves the owner's stated top
complaint — "every single app has its own config and everything" — is actually
fixed, by loading from two different working directories and getting the same
answer.

- [ ] **Step 1: Write the test**

```python
# tests/test_shared_home_e2e.py
import os

import pytest
import yaml
from click.testing import CliRunner

from flexrouter import cli, home
from flexrouter.config import load_config

SETTINGS = {
    "settings": {"port": 4891},
    "providers": {"groq": {"base_url": "https://api.groq.com/openai/v1"}},
    "buckets": {"fast": [{"provider": "groq", "model": "llama-3.3-70b-versatile",
                          "score": 90, "rpm": 30, "tpm": 6000}]},
}


@pytest.fixture
def shared_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLEXROUTER_HOME", str(tmp_path / "home"))
    home.ensure_home()
    home.config_path().write_text(yaml.dump(SETTINGS), encoding="utf-8")
    CliRunner().invoke(cli.cli, ["keys", "add", "groq", "--secret", "gsk-shared"])
    return tmp_path


def test_two_different_projects_see_the_same_settings_and_key(shared_home, monkeypatch):
    project_a = shared_home / "project-a"
    project_b = shared_home / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    # A stray settings file in one project must now be ignored entirely.
    (project_a / "flexrouter.yaml").write_text("settings: {port: 1}\n", encoding="utf-8")

    monkeypatch.chdir(project_a)
    from_a = load_config()
    monkeypatch.chdir(project_b)
    from_b = load_config()

    assert from_a.port == from_b.port == 4891
    assert from_a.providers["groq"].api_keys == ["gsk-shared"]
    assert from_b.providers["groq"].api_keys == ["gsk-shared"]


def test_the_settings_file_is_byte_identical_after_a_full_run(shared_home):
    before = home.config_path().read_bytes()
    load_config()
    CliRunner().invoke(cli.cli, ["doctor"])
    CliRunner().invoke(cli.cli, ["keys", "list"])
    assert home.config_path().read_bytes() == before


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_the_key_file_is_not_world_readable(shared_home):
    assert os.stat(home.keys_path()).st_mode & 0o777 == 0o600
```

- [ ] **Step 2: Run it**

Run: `python -m pytest tests/test_shared_home_e2e.py -v`
Expected: passes first time — every piece is already built. If it fails, the
stage is not actually done.

- [ ] **Step 3: Run the whole suite one more time**

Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_shared_home_e2e.py
git commit -m "test: prove two projects share one home, one settings file, one key"
```

---

## Self-review notes

- **Spec §1 coverage:** the locations table → Task 2; credential resolution
  order → Task 5; "the config file is read-only to the machine" → Tasks 4 and 6,
  plus the byte-identical check in Task 10; `keys.json` shape including
  `allow_models` globs and masking → Task 3.
- **Spec Migration coverage:** step 1 (lift inline keys) → Task 7's
  `keys import`, deliberately narrowed to credentials only, per the owner's
  "start fresh" decision. It does **not** rewrite the old YAML — it leaves it
  untouched and tells the owner to delete it, which is safer than the spec's
  blank-the-secrets rewrite and loses nothing given nothing else is migrated.
  Step 3 (`flexrouter doctor`) → Task 8. Step 2 (catalogue refresh on first
  run) belongs to Stage 7.
- **Open question 3** → Task 9.
- **Deviation from the spec, recorded:** the spec asks the deprecation warning
  to name "the file and line". `_line_of()` finds the line by searching the raw
  text for the secret, which is accurate but reports the first matching line if
  the same secret appears twice. Acceptable.
- **Out of scope for this stage, by design:** the OpenAI surface (§2), traces
  (§3), the decision layer (§4), per-key selection strategies (§5 — Task 3
  stores `weight` and `allow_models` but nothing reads them yet), catalogue
  refresh (§6), the dashboard rebuild (§7), ranking (§8).
