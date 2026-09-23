# 0016. 402 and 403 refuse a model, not an account

Date: 2026-09-23
Status: Accepted. Supersedes the 402 and 403 rows of ADR 0012's context.

## Context

Until now a 403 benched the key that got it, like a 401, and a 402
quarantined the whole provider (`ProviderError.is_provider_wide`). Both
assumed the refusal was about the account.

A playground run showed neither is safe to assume. Mistral answered 403
`labs_not_enabled` for one Labs model while the same key served every other
Mistral model. llm7 answered 402 "Insufficient balance" for its paid `pro`
models while its free `turbo` models kept working. Each single refusal took
a whole provider down for 24 hours, including models that had just answered.

## Decision

Only 401 means the key is bad (`RouterError`, key benched). 402 and 403 are
`ProviderError`s with `is_permanent`: the one model is quarantined and the
request fails over. `is_provider_wide` is gone.

## Consequences

The two possible mistakes are not symmetric. Treating a model-level refusal
as account-wide silently removes working models for a day. Treating an
account-wide refusal (Cerebras's 402 "Payment required", a Google project
without the API enabled) as model-level costs one fast failover per model,
after which each one is quarantined on its own. The second degrades
gracefully, so it is the default.
