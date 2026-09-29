# 0021. The model list is always read; auto-add is optional

Date: 2026-09-26
Status: Accepted. Amends ADR 0014.

## Context

ADR 0014 ran a catalogue refresh at startup and deferred "apply". Commit
`3e0abbc` then made discovery opt-in and off by default, which also switched
off the one `GET /models` per provider that told flexrouter which IDs are
real. Without it, a typo'd or retired model ID only showed up as a 404 at
request time, and AI-pasted model lists could contain invented IDs.

## Decision

Discovery is split in two:

- **Reading** each provider's model list is always on: one read-only
  `GET /models` per provider at startup, and again when "Add models with
  AI" opens. It feeds the AI-paste prompt ("match each row to one of these
  IDs"), review-screen validation, and did-you-mean on a 404.
- **Adding** newly found models stays an option, off by default ("Add new
  models automatically"; the old `experimental_model_discovery` is still
  read as an alias).

A did-you-mean fix is the first real "apply" path: pressing [Use X] writes
the suggested ID to `overrides.json` as a new model in every bucket the old
one was in, and turns the old one off. It never patches a model's identity
and never renames anything without a click. config.yaml is never rewritten
(ADR 0002).

## Consequences

One extra read-only request per provider per start. Listing models isn't a
chat request and, as far as we know, doesn't count against rate limits.
