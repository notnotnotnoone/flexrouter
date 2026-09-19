# 0001. One fixed home for settings and keys

Date: 2026-09-18
Status: Accepted

## Context

`FlexRouter` used to find its settings by searching the current working directory (and walking up from it). Every consuming project therefore ended up with its own `flexrouter.yaml`, and, because keys used to live inline in that file, its own copy of every API key. Two projects on the same machine could not share a bucket, a provider, or a credential. This was the owner's single biggest complaint about v1.

## Decision

Delete the cwd search chain entirely. There is one flexrouter home per machine, located by `flexrouter/home.py::home_dir()`:

- `FLEXROUTER_HOME`, if set, always wins.
- Otherwise a platform default: `%LOCALAPPDATA%\flexrouter` on Windows, `$XDG_CONFIG_HOME/flexrouter` or `~/.config/flexrouter` elsewhere.

`FlexRouter(config_path=...)` still accepts an explicit path for tests and one-offs, but auto-discovery no longer looks at the current directory or `~/.flexrouter.yaml`.

## Consequences

- Every project on a machine shares one settings file, one key store, one set of dashboard overrides, one state directory. A key added once is available to all of them.
- A project that wanted its own private settings (a different set of buckets or providers per project) can no longer get that for free — it must set `FLEXROUTER_HOME` itself to point somewhere project-specific, or accept the shared configuration.
- Tests and scripts that relied on dropping a `flexrouter.yaml` next to the code they were running now need `FLEXROUTER_HOME` or an explicit `config_path`.
