# 0019. The error brain decides

Date: 2026-09-26
Status: Accepted. Supersedes ADR 0012. Overturns ADR 0013's rule that a bare
400 is `bad_request` at confidence 1.0.

## Context

ADR 0012 made the error brain observability-only: every failure was
classified and the verdict written to the trace, and the router ignored it.
In practice that meant the classifier (JEV) was right and nothing happened.
It correctly called a Mistral 403 a plan problem rather than a bad key, and
the key was benched anyway. Real 400s from the history meant four different
things: "Invalid model" (the model is gone), "does not support chat
endpoints" (the wrong kind of model), tools or images rejected (it can't do
this), and malformed JSON (the caller's fault). A status code alone can't
tell those apart.

## Decision

The verdict drives the model's status (ADR 0020):

- too fast / their end temporary → Busy
- model gone → Needs you, with did-you-mean or Remove
- needs payment / not on plan → Needs you
- bad key → Needs you, on the key only
- genuine caller fault → returned to the caller, no penalty
- unknown or empty → Struggling

JEV gets the deciding vote on 400s and on any error text the built-in rules
don't recognise. The fingerprint drops the model name, so one kind of error
is asked about once, not once per model. The first sighting waits at most
0.5s for JEV; after that the answer comes from memory. When JEV is unsure
or down, the error is treated as the model's problem and the request fails
over. Those errors are listed under "Not sure" on the Status page, where
the owner can say what they mean once.

## Consequences

A classifier mistake now changes routing. The cap on that risk is that an
unsure verdict always fails over rather than blaming the caller, and every
unsure error is shown for correction. Without a classifier configured,
behaviour is the old one: a bare 400 goes back to the caller.
