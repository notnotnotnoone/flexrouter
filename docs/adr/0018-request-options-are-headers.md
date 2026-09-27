# 0018. Per-request options are headers, and the request log is JSON

Date: 2026-09-26
Status: Accepted

## Context

Agora, a debate app, is being rebuilt to show off flexrouter. It asks many
models the same question in one run and displays, for each vote, which models
were passed over, which failed and which answered. Three things were missing:

- **A way to find its own requests.** The Requests page is server-rendered HTML
  only, so an app that wanted the log had to rebuild it from its own call
  records. And even then it could not see failovers, because flexrouter only
  reports failed attempts on a *failed* response, never on one that succeeded
  after failing over.
- **A way to tell its requests apart** from everything else hitting the same
  flexrouter.
- **A way to say "any model in this bucket except these".** Agora gives each
  model one vote per run. Pinning every vote to a named model works, but then
  Agora has to run its own failover loop, which flexrouter then cannot show,
  since a pinned call never fails over (ADR 0009). Asking the bucket and
  leaving out the models that have already voted lets flexrouter pick and fail
  over as usual, and the whole thing lands in its own trace.

The engine already takes an `exclude` set internally (the models tried in the
current request). What was missing was a way for a caller to add to it.

## Decision

**Two request headers on `POST /v1/chat/completions`:**

- `X-Flexrouter-Client`: a tag, 1-64 characters of `[A-Za-z0-9._:-]` after
  trimming. It is stored in the trace as `asked.client`.
- `X-Flexrouter-Exclude`: comma-separated `provider/model` ids, at most 200.
  It is stored as `asked.exclude`, and each excluded bucket model is added to
  `skipped` with reason `excluded`.
- Anything else in either header is a 400 naming the header.

**Headers, not body fields.** The body stays plain OpenAI. The OpenAI `user`
field was the obvious home for a client tag, but it is in `_PASSTHROUGH`, so
providers already receive it. A header can never be forwarded by accident.
The two options also travel the same way, and every OpenAI SDK can set extra
headers.

**Explicit keywords inside.** `agenerate` and `agenerate_stream` take
`client=` and `exclude=` as named parameters. They never ride in `**kwargs`,
because that is forwarded to the provider.

**Exclude only narrows a bucket.**
- A pinned call ignores it: the caller named the model outright.
- Ids that are not in the bucket are ignored.
- If it covers every model in the bucket, the call fails at once with a
  `RouterBusy` (a 503) that says so, instead of waiting out the failover
  budget for a model that can never be picked.

**`GET /api/requests` and `GET /api/requests/{id}`.** These serve the Requests
page's own data (`requests_page.filtered`, `facts.request_journey`) as JSON,
with a new `client` filter. They live under `/api`, which ADR 0009 leaves open
on the local machine; no new guard is added.

## Consequences

- Traces now include the caller's tag, so the tag has to be safe to show
  anywhere. That is why the character set is narrow.
- A caller can see the whole request log, including other apps' requests,
  exactly as the dashboard can. This is the same exposure ADR 0009 already
  accepts for `/api`.
- If an exclude list leaves only busy models, the call waits for them within
  the failover budget like any bucket call. Only the case where nothing at all
  is left fails fast.
- The journey endpoint searches the last 5000 traces. An older request is a
  404 even if its line is still in the file.
