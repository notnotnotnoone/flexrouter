# Issue 01: Give `default`-tier models real, differentiated scores

Status: ready-for-agent

## What

Edit `flexrouter.yaml`'s `default` tier: replace every model's `score: 50`
with a score reflecting a defensible reliability/speed tier. This is a
config-only change — no code in `engine.py` needs to change, its scoring
math already does the right thing once the input scores actually differ.

## Scoring scheme

Use provider identity and the `:free` model-ID suffix as the (mechanical,
checkable) basis — this is a heuristic starting point based on known
provider characteristics (dedicated vs. shared/free capacity), not measured
data. A proper follow-up PRD for dynamic, measured scoring is out of scope
here (see the PRD's non-goals).

| Provider | Model-ID pattern | Score | Reasoning |
|---|---|---|---|
| `cerebras` | any | 90 | Dedicated inference hardware, highest configured tpm (60000) of any provider in this tier |
| `groq` | any | 80 | Fast inference; tpm is tighter (6000) but rpm/reliability is solid |
| `googleai` | any | 75 | Major-provider reliability, generous configured limits |
| `openrouter` | not `:free`-suffixed | 55 | Paid/normal routing through an aggregator — real capacity, one layer removed from the origin provider |
| `openrouter` | `:free`-suffixed | 20 | Shared free-tier pool — most likely to be slow, rate-limited, or flaky under contention |
| `ollama` | any | **verify first, see below** | |

**Before scoring the 8 `ollama` entries:** confirm whether Ollama is
actually expected to be running locally in this environment (check
`flexrouter.yaml`'s `providers.ollama` section for its configured
`base_url` — if it points at `localhost`/`127.0.0.1`, try reaching it,
e.g. `curl <base_url>/api/tags` or equivalent). If it's not reachable, these
8 entries are dead weight in the `default` tier's random pool right now (any
selection lands on a connection failure) — either score them very low
(e.g. `10`) so they're rarely if ever in the "top" window once other models
have real scores, or ask whether they should be removed from `default`
entirely / moved to their own tier, rather than silently guessing which is
wanted. If Ollama IS reachable and serving real local models, score based on
observed responsiveness (a quick manual timing check is enough — this
doesn't need to be a full benchmark) — local inference can legitimately be
very fast or noticeably slow depending on hardware, don't assume either way.

With this scheme: `best_score` becomes `90` (cerebras), `threshold =
90*0.8 = 72`, so the "top" pool for a typical call becomes just cerebras +
groq + googleai (approximately 24 of the 55, versus all 55 today) — a real
narrowing toward the fastest tiers, while still leaving room for `_pick`'s
existing random-choice-among-top behavior to spread load rather than
hammering one single model.

## Also verify: are any of these 55 actually dead?

The investigation that surfaced this issue observed real 404s from at least
one openrouter or groq model during testing (stale/deprecated model ID) —
not exhaustively identified which ones. Before or after re-scoring, do a
quick reachability pass: a minimal request to each configured model (or at
least a sample across providers) and confirm none of them hard-fail on
every call regardless of score. Remove or fix any confirmed-dead entries
(wrong model ID, deprecated/retired model) — a dead entry with even a low
score still occasionally gets selected and wastes a full retry round-trip
plus the 1s backoff before rotating to the next attempt.

## Test coverage

If this repo has any existing test that asserts on `flexrouter.yaml`'s
parsed config (e.g. "all models have a score field" or similar schema
checks), extend it to also assert scores aren't all identical within a tier
— a regression test for the exact bug this issue fixes. If no such test
exists, a small one is worth adding: parse the shipped `flexrouter.yaml`'s
`default` tier and assert `len(set(scores)) > 1`.

## Comments
