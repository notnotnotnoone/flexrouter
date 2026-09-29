# 0021. The model list is always read; adding models automatically is optional

Date: 2026-09-28
Status: Accepted. Amends ADR 0014.

## Context

ADR 0014 made the full catalogue check run on every service start, and deferred the "apply" half: nothing could turn a pending finding into a selectable model.

Two things changed. Staging every configured provider's whole catalogue on every start is more than the owner wants without asking, because a plain start quietly fills `catalog_pending.json`. And reading a provider's real model list turned out to be useful on its own: it is how a mistyped or vanished model id is told apart from a real one, and it feeds the Add models with AI prompt and its review screen.

Decided in the v2.3 grilling of 2026-09-25 (the owner: "have auto add as an option like always") and shipped on 2026-09-26.

## Decision

Discovery is split in two.

- **Reading the real model list is always on.** Once per service start, and again when Add models with AI opens, each provider gets one model-list request (`refresh.refresh_known_model_ids`). It only reads. It caches the ids, which `catalogue.is_real` and the did-you-mean suggestion use, and records configured ids the provider no longer lists in `state/unknown_configured_models.json`. It writes nothing to a bucket.
- **Adding new models automatically is an option, off by default** (`auto_add_models`, formerly `experimental_model_discovery`). Only when it is on does a start also run the full catalogue refresh: staging what it finds into `catalog_pending.json` and recording each new model's published context window to `state/model_facts.json`.

Both run on a worker thread, as ADR 0014's event-loop fix requires, and a failure in either is logged and never stops the service from starting. The manual `flexrouter refresh` and the dashboard's refresh button are unchanged.

Applying a finding is no longer deferred. The Models page accepts or rejects each pending entry by hand (`flexrouter/dashboard/pending_actions.py`): an appeared model goes into a bucket the owner picks (`overrides.add_model`), a vanished model is turned off with the existing `enabled: false` override, and a changed field becomes an ordinary field override. A did-you-mean row on What's broken has a Use it button that files the catalogue's suggested id as a new model and turns the old one off (`/statuses/{provider}/{model}/use`). Every one of these is a click. There is still no automatic apply.

## Consequences

- A default install still checks each provider once per start but stages nothing. The pending tray fills only for an owner who turns auto-add on or clicks refresh.
- The constraint ADR 0014 named still holds. `overrides.py`'s `ALLOWED_FIELDS` excludes a model's identity, and new models arrive through the separate additive `new_models` section rather than by weakening that guarantee.
- The rest of ADR 0014, its worker-thread fix in particular, still describes the code.
