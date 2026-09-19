# 0007. The `init` wizard is deleted, not ported

Date: 2026-09-19
Status: Accepted

## Context

`flexrouter init` (`flexrouter/onboard.py`) was v1's interactive setup wizard. It recreated, in one command, both of the faults Stage 1 exists to remove:

- It wrote `flexrouter.yaml` into the **current working directory**, i.e. one settings file per consuming project — fault 1 in the v2 spec, the mechanism the deleted `discover_config()` cwd chain used to serve.
- It wrote the collected API keys into that file in plaintext, under `api_keys: - key: <secret>` — fault 2.

It also emitted the legacy `tiers:` and `dashboard_port: 7352` spellings, and carried a second, weaker masking helper that printed the first six characters of a secret, contradicting the constraint that only `keys.mask()` output ever reaches a human.

Porting it would have meant rewriting the wizard to drive `keys.add_key()` and to leave `config.yaml` alone — a new interactive surface with nothing behind it, since the replacement path already exists.

## Decision

Delete the `init` command from `flexrouter/cli.py`, delete `flexrouter/onboard.py`, and remove every documentation reference to it.

The read-only half of that module — the `PROVIDERS` registry, `discover_models`, `discover_ollama`, `score_with_aa`, `_context_window` — is **not** wizard code: `flexrouter/refresh.py` and `flexrouter/probe.py` depend on it for the daily catalogue check. It moves to a new module, `flexrouter/catalogue.py`, unchanged. Its tests move with it, from `tests/test_onboard.py` to `tests/test_catalogue.py`; the wizard-only tests (`build_yaml`, `run_onboard`) are deleted with the wizard.

## Consequences

- Setting up flexrouter is now: `flexrouter keys add <provider>` for credentials, hand-editing `config.yaml` for buckets and providers, `flexrouter doctor` to see where everything is, and the dashboard for the rest.
- No code path writes a settings file into the current directory any more.
- `flexrouter.onboard` is not importable. Anything importing `PROVIDERS`, `discover_models`, `discover_ollama`, `score_with_aa` or `_context_window` must import them from `flexrouter.catalogue`.
- `build_yaml` is gone entirely. Nothing generates a settings file; the owner writes it.
