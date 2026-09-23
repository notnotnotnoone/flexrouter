# 0017. Error text keeps known names, and the full body

Date: 2026-09-23
Status: Accepted. Amends the "no model-name exception" line in `redact.py`.

## Context

The Error brain showed one clipped sentence per error, and `scrub()` made
even that unreadable: Rule A cuts every run of 16 or more token characters,
so `nvidia/adept/fuyu-8b` became `…u-8b`, `frequency_penalty` became
`…alty`, and a provider's own error codes (`INVALID_ARGUMENT`,
`labs_not_enabled`) vanished from any body it touched. The owner could not
read what had gone wrong, which is the brain's whole job.

## Decision

Two narrow changes, neither a pattern exemption.

1. `set_known_identifiers()`: the provider names and model ids in the
   loaded settings, plus flexrouter's own request field names, are left
   alone by `scrub()` — but only when a run is exactly one of them after
   the usual edge trim. A credential gets through only by being character
   for character a public model id; one that merely contains an id is still
   one run and is scrubbed whole.

2. `scrub_body()`, used for a provider's full response body and for the
   classifier's raw replies in the Error brain: every credential flexrouter
   holds (`set_known_secrets()`, from provider keys and service keys) is
   masked exactly, and Rule B still runs, but Rule A does not. A provider can
   only echo back a key flexrouter sent it, and every such key is in that
   set, so the blind 16-character cut buys nothing there and costs the part
   of the body worth reading.

One-line messages, quarantine reasons and traces keep full `scrub()`.

## Consequences

A long random string in a provider body that is not one of the owner's keys
is now shown. That is an account id or request id, not this owner's
credential. A key supplied some other way than flexrouter's settings and
service keys (hand-set in an HTTP header by a hook, say) is not in the set;
none exists today.
