# 0022. Conversations are saved for 7 days

Date: 2026-09-26
Status: Accepted. Amends ADR 0010.

## Context

ADR 0010 keeps the request trace scrubbed, not verbatim, so it is safe to
keep and share. The owner wanted to see what was actually asked and
answered: the prompt, the reply and the model's reasoning, per request.

## Decision

A separate conversation store (`flexrouter/conversations.py`) saves the
prompt, reply and reasoning for every request, as sent, one JSONL file per
UTC day under `state/conversations/`. Long messages are cut at about 20 KB.
Files older than 7 days are deleted. Settings has one off switch ("Save
conversations") and the number of days.

Keys flexrouter holds are masked by exact match
(`redact.mask_known_secrets`). The heuristic scrub the trace uses is **not**
applied to conversation text: it would mangle the very text the owner wants
to read.

## Consequences

The privacy trade-off, stated plainly: anything a caller puts in a prompt,
including anything sensitive, sits in `state/` on this machine for up to 7
days. The trace stays scrubbed, so sharing a trace is still safe; sharing
the conversations folder is not. The off switch exists for this reason.
