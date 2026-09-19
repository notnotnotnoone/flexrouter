# 0003. Credentials never live in the settings file

Date: 2026-09-18
Status: Accepted

## Context

`config.yaml` is meant to be shareable and diffable — the owner wants to be able to paste it, back it up, or hand it to someone else without redacting anything first. Secrets typed directly into it (v1's `api_key:` / inline `api_keys:` entries) worked against that, and also meant a parser error on that file was at constant risk of echoing a key back in an error message (see ADR-0006).

## Decision

Keys are stored in their own file, `keys.json` in the flexrouter home, written by `flexrouter/keys.py` and locked to the current user (`flexrouter/store.py::harden` — `chmod 0600`, plus `icacls` on Windows). They are added with `flexrouter keys add <provider>`, which prompts for the secret without echoing it. Resolution order, implemented in `flexrouter/config.py::resolve_keys`:

1. An enabled saved key from `keys.json` for that provider.
2. An environment variable named in the provider's `api_keys: [{env: ...}]` entries.
3. A secret typed straight into `config.yaml` — still read and still works, but emits a `DeprecationWarning` pointing at `flexrouter keys add`.

## Consequences

- A settings file with providers configured but no keys added is safe to share as-is.
- The deprecated inline path is kept working rather than removed, so nothing breaks for someone who hasn't migrated yet — but it is one more code path (`_inline_secrets`) that has to keep working and being tested.
- Any surface that shows the settings to a person masks the inline path first (`redact_config` for the parsed structure, `redact_settings_text` for the file's own text). Masking text by replacing a secret's parsed value only works when that value appears verbatim in the source, which several perfectly legal YAML spellings break (a folded block, a decoded escape, a doubled quote). So the redacted text is parsed back and checked: if any original secret survives anywhere in it, `config export` refuses rather than emits. Refusing is the only outcome that does not depend on having thought of every spelling in advance.
- `api_keys: <bare string>` is read as an environment variable *name* when a key is resolved (2 above), but it is also where somebody who types their key into the wrong slot lands, and nothing in the file distinguishes the two. Resolution is left as it is; redaction treats the position as a secret. Showing a real key cannot be undone, while hiding a variable name costs only a little clarity in a shared copy.
- `keys.json`'s OS-level lock is best-effort (`harden()` never raises); on a filesystem or platform where `chmod`/`icacls` doesn't apply, the file is not actually protected and flexrouter does not detect or warn about that.
