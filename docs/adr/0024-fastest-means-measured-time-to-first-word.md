# 0024. "Fastest" means measured time to the first word

Date: 2026-09-26
Status: Accepted.

## Context

The `fastest` strategy ranked models by `tokens_per_second`, a hand-entered
number that was never measured. gemini-3.5-flash-lite had "337 tok/s" and a
12s real median; groq/gpt-oss-20b replied in 0.3s but was skipped for
having no number at all.

## Decision

A bucket using `fastest` ranks by the median time to the first token over
roughly the last 20 **successful** requests to each model
(`flexrouter/speed.py`, in memory). Errors don't count. A non-stream
request has no separate first-token moment, so its total time is used. A
typed `tokens_per_second` is only a starting guess for a model with no
samples yet. A model with neither is tried, not skipped, and measured on
its first real request. The Buckets page says so: "ranked by: first word,
your last 20 requests".

## Consequences

Rankings reset when flexrouter restarts. That is on purpose: speed changes
with the provider's load, and old numbers mislead.
