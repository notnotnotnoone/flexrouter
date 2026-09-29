# 0025. A built-in `all` bucket holds every model

Date: 2026-09-26
Status: Accepted

## Context

Agora gives each of the owner's models one vote per run. It asks a bucket and
excludes the models that have already voted (ADR 0018), so flexrouter picks
and fails over. But a bucket call only ever reaches that bucket's models, and
`auto` is just the bucket holding the best model. On the owner's setup that is
`smart`, with 6 of his 23 models. In a live run, 4 votes came back and the
rest failed after trying every model in `smart`, which were all busy.

To reach every model, a caller either has to spread its calls over the
buckets itself, or the owner has to keep a bucket with every model in it by
hand, and keep it up to date as models come and go.

## Decision

**Every flexrouter has a bucket named `all`,** holding every model in every
bucket, once each. It is worked out from the buckets whenever the settings
load, so it is always current, and nothing is written to the settings file.

- **It routes like any bucket.** It uses the default strategy (smartest), the
  failover budget and `X-Flexrouter-Exclude`.
- **A bucket the owner names `all` wins.** The built-in one is only used when
  no such bucket exists.
- **It lives beside the pins.** Like a pinned model (ADR 0009), `all` is a
  bucket the owner never wrote, so it goes in the pin engine's shadow config,
  not the real one. The dashboard's bucket pages and editing never see it.
- **`GET /v1/models` lists it** right after `auto`, with `"builtin": true`, so
  a client can find it.

## Consequences

- A model in several buckets appears once in `all`, with the settings (score,
  limits) from the first bucket it is in.
- The dashboard does not show `all` on the Buckets page, because there is
  nothing there the owner can change.
- The pin engine used to keep request windows of its own that nothing ever
  recorded into, so pinned calls ignored a model's configured `rpm`/`tpm`. It
  now reads the main engine's windows, so pinned and `all` calls respect those
  limits the same as a bucket call does.
