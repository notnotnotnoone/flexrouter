# 0020. The error brain decides

Date: 2026-09-28
Status: Accepted. Supersedes ADR 0012. Overturns ADR 0013's rule that a bare 400 is `bad_request` at confidence 1.0, whenever a classifier is configured.

## Context

ADR 0012 made the error brain observability-only: it classified every failure and wrote the verdict to the request trace, but no routing decision read it. That was right for the stage that built it. Every case a status code already settles (401, 402, 403, 404, 410, 429) was already acted on, and a second source of truth for the same fact would only have drifted.

It left a gap that mattered in practice. A bare 400 means nothing by itself. Real 400s from history meant "invalid model" (the model is gone), "does not support chat" (the wrong kind of model), "tools rejected" (it cannot do this), and malformed JSON (the caller's fault). ADR 0013's rule sent all of them back to the caller as `bad_request`. The classifier's correct answer was logged and then ignored.

Decided in the v2.3 grilling of 2026-09-25 and shipped on 2026-09-26 (commit `86d0b17`).

## Decision

The verdict now drives the model's status and the failover decision, for a bare 400 and for error text the rules do not recognise.

- A bare 400 takes its status from the verdict (`status._verdict_failure`). `too_fast` is Busy. `needs_payment` is Needs you. `model_gone` is Needs you, with a did-you-mean when the catalogue has a close match. `bad_key` is Needs you on that one key only (`LocalRouter._handle_provider_error` hands it to `_handle_auth_failure`). Anything else is Busy.
- A 400 goes back to the caller only when the verdict is `bad_request` or there is none. Every other verdict fails over to the next model (`failover.decide_failover`), and `message_too_long` fails over to a model with a bigger context window.
- Unsure or down is the model's problem, not the caller's. A first sighting of an error gets 0.5 seconds for the classifier (`JEV_TIMEOUT_SECONDS`); a timeout is `unknown` at zero confidence, which fails over. The caller is only blamed when the classifier is confident. Entries below the confidence threshold are flagged for review on the Error brain page, where the owner can correct one once (`ErrorBrain.correct`); a hand-corrected entry is never overwritten.
- For 400, 403 and 429 the status rule becomes a prior at confidence 0.6 that a classifier can overturn, and only when a classifier is actually configured (`ErrorBrain._is_contestable`). With none, which is `NullDecider` and the default, the rules answer at full confidence. An install with no classifier therefore behaves as it did before this ADR: a bare 400 is `bad_request` and goes back to the caller.
- The fingerprint drops the model name (`error_brain.fingerprint`), so the same kind of error from every model is asked about once, not once per model.

Unchanged: 401, 402, 403, 404, 410 and 429 are still settled by their status code alone. A 403 whose verdict is `bad_key` is still a refusal of one model, not a bad key (ADR 0016). Only a 400 is re-routed to key status.

## Consequences

- For 400s, routing now depends on an optional external classifier. The failure direction is deliberate: a wrong or missing answer costs one wasted failover, never a wrong "your request is bad" error handed to the caller.
- `NullDecider` is no longer the only implementation, which ADR 0012 listed as a consequence. `HttpDecider` (any OpenAI-compatible endpoint held to a JSON schema) and `DecisionsDecider` (TypeSafe's Jev through OpenRouter's Decisions API) exist, chosen by `build_decider`. The endpoint, model and timeout are settings.
- This ADR words its verdict-to-status mapping in the one-status vocabulary of `flexrouter/status.py` (Ready, Busy, Struggling, Needs you, Off), which replaced quarantine, penalties and benching. That change has no ADR of its own yet, so "benched" in ADR 0011 and "quarantined" in ADR 0016 now read as Needs you.
