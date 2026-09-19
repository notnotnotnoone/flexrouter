# 0002. The settings file is never rewritten by the tool

Date: 2026-09-18
Status: Accepted

## Context

A settings file the tool can silently rewrite is a settings file the owner cannot safely hand-edit or comment: any comment, any deliberate ordering, any unusual formatting is one machine write away from being lost. The dashboard, though, needs to let people change settings without opening a text editor.

## Decision

`config.yaml` (`flexrouter/home.py::config_path()`) is created once, from `STARTER_CONFIG`, and after that is only ever read, never written, by flexrouter itself. Everything the dashboard changes is written instead to `overrides.json` (`flexrouter/overrides.py`), keyed by section (`settings`, `providers`, `models`) and, for providers/models, by name. `load_config()` parses `config.yaml`, then calls `apply_overrides()` to layer `overrides.json` on top before anything else happens (`flexrouter/config.py::load_config`).

## Consequences

- Comments, layout, and any deliberate structure in `config.yaml` survive forever — the file is genuinely hand-owned.
- The value in effect for a given key can now live in two places at once. `doctor` reports overrides separately for this reason (`flexrouter/cli.py::doctor`), but a reader of `config.yaml` alone no longer sees the whole picture.
- Clearing an override (`clear_override`) reverts to whatever `config.yaml` says, not to some other default — the settings file remains the ground truth underneath the overrides.

## Note

`flexrouter refresh` (`flexrouter/refresh.py::refresh_config`) is an exception: it still overwrites `config.yaml` directly (after writing a timestamped backup to `state/backups/`). That command predates Stage 1 and was not brought in line with this decision as part of this stage — anyone using `refresh` should know their comments and layout do not survive it, unlike everything else described above.
