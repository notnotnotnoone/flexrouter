# PRD: Fix flat model scoring and unvalidated responses in the `default` tier

Status: ready-for-agent

## Origin

Found while investigating why Stash (a consumer of this library) sees wild
`reason`-stage latency variance on `/chat` — 669ms to 134 seconds on the same
`default` tier for equivalent queries. Root cause, confirmed by reading
`engine.py` directly rather than guessing:

`_score_candidates()` (`engine.py:167`) reads each model's `score` straight
from `flexrouter.yaml` with zero dynamic/runtime adjustment — no
latency-based weighting, no historical-success-rate weighting, nothing.
`_pick()` (`engine.py:198`) sorts by that score, takes everything within 80%
of the best (`threshold = best_score * 0.8`), and calls `random.choice()`
over the survivors.

**Every one of the 55 models in the `default` tier has the identical
`score: 50`.** So `threshold` is always `40`, every surviving candidate is
always `>= 40`, and `_pick` is choosing uniformly at random across the
*entire* tier every single call — there is no preference for fast or
reliable models at all. Health/rate-limit state (`_penalties`,
`_rate_limit_store`, `_budget`, `SlidingWindow`) only *excludes* models from
the candidate list; none of it feeds back into ranking the survivors.

Provider breakdown of the 55 (`grep -c` against `flexrouter.yaml`):

| Provider | Count | Notes |
|---|---|---|
| `cerebras` | 2 | Dedicated inference hardware, high tpm (60000) |
| `groq` | 10 | Fast inference, tighter tpm (6000) |
| `googleai` | 12 | Google's Gemini API |
| `openrouter` | 23 | Aggregator; 21 of the 55 total models repo-wide carry a `:free` model-ID suffix — mostly these |
| `ollama` | 8 | **Local** model server — see Issue 01, this needs verifying before scoring, not assuming |

Also observed live during investigation (not exhaustively enumerated — see
Issue 02 for how to find the actual offenders): some configured models
returned 404 (stale/wrong model IDs on at least one of openrouter/groq), and
at least one 200-OK response was missing the expected `choices` key
entirely, which crashes with an uncaught `KeyError` wherever the caller
indexes into it (`engine.py`/`client.py` don't validate response shape
today — only HTTP status codes).

## Requirement

1. Replace the flat `score: 50` on every `default`-tier model with real,
   differentiated scores so `_pick`'s 80%-of-best threshold actually
   narrows the field toward faster/more-reliable providers instead of
   picking uniformly across all 55. See Issue 01.
2. Make response parsing defensive: a 200 response missing the expected
   shape should raise `ProviderError` (which the existing retry loop
   already knows how to handle — rotate to the next model) instead of
   letting a raw `KeyError`/`IndexError` escape uncaught. See Issue 02.

## Non-goals

- A fully dynamic, measured-latency/success-rate scoring system (EWMA
  health scores feeding back into `_score_candidates`, etc.) — real
  improvement, but bigger scope than this PRD. The static re-scoring in
  Issue 01 is the small, high-value first step; a dynamic system is a
  reasonable future PRD if the static fix isn't enough on its own.
- Removing `ollama` from the tier outright — verify first (Issue 01),
  don't assume it's dead.
- Any change to `_pick`'s 80%-threshold/`random.choice` mechanism itself —
  that logic is fine; it just needs scores worth ranking by.

## Comments
