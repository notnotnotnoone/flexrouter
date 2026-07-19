"""Regression tests for dashboard config/state_dir resolution.

Bug: the dashboard resolves its config (and therefore its state_dir) purely
via discover_config(), which only checks the exact process cwd then the home
directory. When the dashboard is started from a different working directory
than the one flexrouter.yaml lives in (a subdirectory of the project, or an
unrelated directory), it silently falls back to an empty/wrong state_dir and
shows "no data" forever, even while real traffic is landing elsewhere.
"""
from __future__ import annotations

import csv

from flexrouter.dashboard import server


MINIMAL_CONFIG = """
providers:
  groq:
    base_url: https://api.groq.com/openai/v1
    api_keys:
      - key: test-key

tiers:
  default:
    - provider: groq
      model: test-model
      score: 90
      rpm: 30
      tpm: 60000

settings:
  state_dir: .flexrouter
"""


def _reset_normalization(monkeypatch):
    monkeypatch.setattr(server, "_cwd_normalized", False)
    monkeypatch.setattr(server, "_router", None)


def test_resolve_config_path_finds_cwd_config(tmp_path, monkeypatch):
    _reset_normalization(monkeypatch)
    (tmp_path / "flexrouter.yaml").write_text(MINIMAL_CONFIG)
    monkeypatch.chdir(tmp_path)

    found = server._resolve_config_path()

    assert found.resolve() == (tmp_path / "flexrouter.yaml").resolve()


def test_resolve_config_path_walks_up_from_subdirectory(tmp_path, monkeypatch):
    # Regression case: dashboard started from a nested subdirectory of the
    # project (e.g. `dashboard/frontend`) instead of the project root.
    _reset_normalization(monkeypatch)
    (tmp_path / "flexrouter.yaml").write_text(MINIMAL_CONFIG)
    sub = tmp_path / "dashboard" / "frontend"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)

    found = server._resolve_config_path()

    assert found == (tmp_path / "flexrouter.yaml")


def test_resolve_config_path_prefers_env_var(tmp_path, monkeypatch):
    # Regression case: dashboard started from a cwd unrelated to the project
    # entirely (not an ancestor of the config's directory), the same way a
    # real caller like Stash pins config via an explicit env-provided path.
    _reset_normalization(monkeypatch)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config_path = project_dir / "flexrouter.yaml"
    config_path.write_text(MINIMAL_CONFIG)

    unrelated_dir = tmp_path / "unrelated"
    unrelated_dir.mkdir()
    monkeypatch.chdir(unrelated_dir)
    monkeypatch.setenv("FLEXROUTER_CONFIG", str(config_path))

    found = server._resolve_config_path()

    assert found == config_path


def test_state_dir_resolves_consistently_from_wrong_cwd(tmp_path, monkeypatch):
    # End-to-end regression: real traffic writes audit.csv relative to the
    # project directory. Starting the dashboard from an unrelated directory
    # (with FLEXROUTER_CONFIG set, mirroring a real deployment) must still
    # read that same audit.csv, not silently report "no data".
    _reset_normalization(monkeypatch)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config_path = project_dir / "flexrouter.yaml"
    config_path.write_text(MINIMAL_CONFIG)

    state_dir = project_dir / ".flexrouter"
    state_dir.mkdir()
    with (state_dir / "audit.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp", "tier", "provider", "model",
            "prompt_tokens", "completion_tokens", "cost_usd", "latency_ms", "status",
        ])
        writer.writerow([
            "2026-01-01T00:00:00+00:00", "default", "groq", "test-model",
            10, 5, 0.0, 100, "ok",
        ])

    unrelated_dir = tmp_path / "unrelated"
    unrelated_dir.mkdir()
    monkeypatch.chdir(unrelated_dir)
    monkeypatch.setenv("FLEXROUTER_CONFIG", str(config_path))

    resolved = server._state_dir()

    from flexrouter.dashboard.stats import compute_stats
    stats = compute_stats(resolved)
    assert stats["totals"]["requests"] == 1
