# 0009. The wire vocabulary, and the importable class as a client

Date: 2026-09-19
Status: Accepted

## Context

Stage 2 gave flexrouter an OpenAI-shaped surface: a chat client, an agent
framework or a piece of someone's own code points at `http://127.0.0.1:4891/v1`
and talks to it the way it would talk to OpenAI. That surface only has two
fields to carry the owner's whole routing vocabulary — `model`, a single
string, and an `Authorization` header — so what goes in `model` had to be
decided once, in one place, and then never moved, because a chat client saves
that string in its own settings and nobody can migrate it for them.

At the same time the importable `FlexRouter` class had to stop routing. A class
that routes in-process gives every project that imports it its own settings,
its own keys and its own private idea of how much of each provider's allowance
is left — the fault the whole of v2 exists to fix. The routing code did not go
away; it moved behind a local socket, into one process with one set of books.

These are the rulings made along the way, each with what it costs.

## Decision

### The wire vocabulary

**Buckets are advertised by their plain name; the old ids are accepted
forever.** `/v1/models` lists `smart`, not `auto-smart`. `parse_model` still
understands `auto-smart` and `smart::provider/model` on input
(`flexrouter/wire.py`).

*Cost:* the parsing helper has legacy branches that can never be deleted,
because a chat client's saved model name is not something we can migrate.

**A `/` in `model` means one specific model.** `groq/llama-3.3-70b` goes to
exactly that model. Anything without a slash names a bucket.

*Cost:* a bucket may never be named with a slash in it, and nothing enforces
that yet.

**Asking for a bucket that does not exist is an error.** `resolve()` raises,
and the message names the buckets that do exist. It used to fall through to the
first bucket in the file.

*Cost:* an app with a typo in its saved model name stops working, rather than
limping along answering from a model nobody asked for. We think a clear failure
the owner can read beats a silent substitution he cannot see, especially when
the substitute may be a paid model.

**Pinning uses a shadow engine rather than a change to `engine.py`.**
`LocalRouter._build_pin_engine` builds a second `RoutingEngine` over a
one-model-per-bucket shadow config, sharing the live rate-limit, penalty and
quota stores, so a pinned call consumes and respects the same allowances.

*Cost:* one extra `RoutingEngine` per router and a second config object, rebuilt
on every reload; the two engines' configs can drift if a future change updates
one and forgets the other.

### The local key

**The optional local key guards the chat surface only.** When `auth_token` is
set in settings, every `/v1` request must carry it. The dashboard and its
`/api` data stay open on the local machine.

*Cost:* anything that can reach the dashboard on this machine can read the
settings — masked — and change dashboard overrides without the key. This is
accepted because the service binds to `127.0.0.1`, the key's job is to stop a
local program helping itself to the owner's provider credit, and a key that
also locked the dashboard would lock the owner out of the one screen that shows
him what is wrong.

### Forwarded provider errors

**Provider error text is scrubbed by a deliberately blunt rule.** Several
providers echo the rejected credential back in their error message, and that
message is forwarded to whoever sent the request because flattening it to
"server_error" throws away the only explanation anyone will get. So: any run of
16 or more key-ish characters is shortened to an ellipsis and its last four
characters, and near the words `key`, `token`, `bearer`, `secret`,
`credential` or `authorization` any run of 6 or more is too — unless the run is
made entirely of lowercase letters (`flexrouter/redact.py`).

*Cost:* web addresses and long words in error messages come back shortened, so
some messages read worse. Two residuals remain and are knowingly accepted: a
credential sitting more than 48 characters after its cue word is outside the
window and is caught only if it is long enough for the blunt rule, and a
credential of 15 characters or fewer made only of lowercase letters near a cue
word is not caught at all, because nothing can tell it from an ordinary word.

*Why anyway:* a leaked key is unrecoverable and a shortened model name is not.
No carve-out — not for URLs, not for model names — will be added, because every
carve-out is a hole a credential fits through.

### Streaming

**Tool-call fragments are forwarded as the provider's own dictionary** rather
than rebuilt from four known fields (`id`, `index`, `name`, `arguments`). The
whole fragment is carried through and merged on reassembly, so a
provider-specific key survives.

*Cost:* none known. It is the fix for a known data-loss bug in a competitor,
which dropped anything outside those four fields.

**Once anything at all has reached the caller, the service is committed to that
model.** As soon as one token of text, one reasoning fragment or one tool-call
fragment has gone out, an empty or failed completion fails the request rather
than starting again on another provider — half an answer from one model spliced
onto a whole answer from another is not an answer. The model still gets the
same black mark it would have got on the retry path: the same penalty and the
same recorded failure.

*Cost:* a rare request that used to succeed on a second provider now fails, and
the caller has to ask again.

### The client library

**`FlexRouter` no longer routes; it sends requests to the service.** The
signatures are unchanged on purpose — `generate`, `agenerate`,
`agenerate_stream`, `reload`, `close` — so existing code keeps working without
an edit. The routing code is now `LocalRouter` in `flexrouter/_router.py`, and
it is what the service runs.

**The client turns the whole family of transport failures into one error.**
Nothing listening, listening but never answering, a connect timeout, a read
timeout, a reset: all become `ServiceNotRunning`, whose message names the
address that was tried and says to run `flexrouter serve`. A connection that
dies part-way through an answer is different — the service did answer — and
becomes a plain `RouterError` after everything already received has been
delivered.

*Cost:* a genuine network fault inside the machine reads as "flexrouter isn't
running", which is the wrong diagnosis for that one case. It is the right
diagnosis for almost every other case, and the wrong one still points at the
right address.

**The client emits no routing-progress events.** `AttemptEvent` and
`AttemptFailedEvent` are not produced over the wire; only text, reasoning,
tool-call and done events are.

*Cost:* a caller that showed "trying provider 2 of 3" loses it, and nothing
replaces it until the request trace lands in Stage 3.

**`reload()` is a no-op on the client.** The service watches its own files and
picks up changes itself (ADR 0008).

*Cost:* it silently does nothing; a caller depending on it is not told.

## Consequences

- One string in `model` carries the whole vocabulary, and the old spellings
  keep working, so nobody's saved chat-client setting breaks on upgrade.
- There is one process, with one set of books, doing the routing for the
  machine. Importing `LocalRouter` and driving it directly still works and is
  still what the service does, but it reintroduces the per-project state v2
  exists to remove, so it is not what anyone should import.
- The three behaviours that trade a working request for an honest failure — the
  unknown-bucket error, the commit-on-first-token rule, and blunt scrubbing —
  all lean the same way on purpose: it is easier for the owner to fix something
  that says what is wrong than something that quietly does the wrong thing.
- `tests/test_stage2_e2e.py` holds this end to end: the library, over a real
  socket, against the real service, and the "nothing listening" message.
