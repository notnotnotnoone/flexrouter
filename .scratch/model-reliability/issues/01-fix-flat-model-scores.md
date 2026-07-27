# Issue 01: Give `default`-tier models real, differentiated scores

Status: ready-for-human

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

Applied the suggested scoring scheme mechanically to all 55 `default`-tier
models in `flexrouter.yaml`:

| Provider | Score | Count | Rule applied |
|---|---|---|---|
| cerebras | 90 | 2 | matches table exactly |
| groq | 80 | 10 | matches table exactly |
| googleai | 75 | 12 | matches table exactly |
| openrouter (no `:free` suffix) | 55 | 2 | `openrouter/owl-alpha`, `openrouter/free` |
| openrouter (`:free` suffix) | 20 | 21 | matches table exactly |
| ollama | 15 | 8 | see below — deviates from the table, which left this TBD |

Config loads cleanly (`load_config('flexrouter.yaml')`), all 55 models get
distinct-enough scores (6 distinct values), `best_score` is now 90 so
`_pick`'s threshold is 72, narrowing the "top" pool to cerebras+groq+googleai
as the PRD predicted.

**Ollama reachability — verified, but the result is ambiguous, hence
`ready-for-human`:**

`providers.ollama.base_url` is `http://localhost:11434/v1`. `curl
http://localhost:11434/api/tags` succeeded and listed all 8 configured
models by exact name/tag (`evalengine/unbound-e2b:latest`,
`dolphin-phi:latest`, `phi4:14b`, `deepseek-r1:8b`, `qwen2.5-coder:7b`,
`qwen3:0.6b`, `llama3.2:3b`, `qwen2.5:0.5b`) — so Ollama is up and these are
not stale/wrong model IDs.

But a manual timing check (`/v1/chat/completions`, `max_tokens: 5`) showed
very inconsistent responsiveness, plausibly explaining the PRD's "669ms to
134 seconds" observation:
- `qwen2.5:0.5b`: 3.3s cold, 1.2s on immediate re-request (warm)
- `llama3.2:3b`: 29s (requested right after a different model, i.e. cold)
- `phi4:14b`: 47s
- `deepseek-r1:8b`: **timed out at 60s with no response at all**

The pattern looks like this Ollama instance only keeps one model resident in
memory at a time, so any request for a model other than the currently-loaded
one pays a full (multi-second-to-a-minute-plus) load penalty — and the
router's random-choice-among-top-scored-candidates selection pattern means
that if multiple ollama models land in the same "top" band, most calls will
hit a model-swap tax or worse. That's a real, verified finding, not a guess
— but it's a judgment call how to translate it into a single score. I scored
all 8 uniformly at **15** (reachable and functional, so above the issue's
suggested "unreachable → 10", but low enough to stay well clear of the
cerebras/groq/googleai top band at threshold 72) rather than differentiating
by model size, since the load-time penalty dominated even for a small model
(3B) once it wasn't the resident one, so size-based differentiation didn't
look justified by the data I collected. A human who knows whether this
Ollama box is expected to run with more headroom (e.g.
`OLLAMA_MAX_LOADED_MODELS` > 1, or dedicated per-model instances) should
sanity-check this score / consider whether `ollama` belongs in `default` at
all vs. its own tier — flagging per the issue's guidance rather than
guessing.

**Dead-model reachability sweep — could NOT be completed for
cerebras/groq/googleai/openrouter:**

Attempted live requests (minimal `max_tokens: 1` completions) against a
sample from each remote provider (all 10 groq models, plus one openrouter
and one googleai and one cerebras model). Every single request came back
`401`/`400` "invalid/missing API key" — the keys committed in
`flexrouter.yaml` are rejected uniformly by all four providers, which means
they're placeholder/non-live credentials in this environment, not a
per-model problem. There's no environment-variable override in place either
(checked `config.py`'s env-key resolution — flexrouter.yaml uses literal
`key:` values here, and no matching env vars are set). Result: I could not
distinguish a genuinely dead/stale model ID (404) from this blanket
auth failure for any of the 47 non-ollama models, so **I did not remove or
change any of them** — per the instruction to only act on confirmed-dead
entries, not guessed ones. Only `ollama` (no auth required) was actually
testable, and all 8 of its entries resolved to real, present models (see
above) — no dead ollama entries found.

**One naming judgment call:** `openrouter/free` does not end in the literal
`:free` suffix (it's `/free`, a different model namespace), so by the
mechanical rule in the table above it scored 55 (paid/normal), same as
`openrouter/owl-alpha`, even though the name itself suggests a free/shared
routing pool. Flagging this explicitly rather than silently guessing either
way — a human who knows what `openrouter/free` actually resolves to should
confirm whether 55 or 20 is correct here.

**Test coverage:** no existing test parsed the shipped `flexrouter.yaml`
directly (the `config_file` fixture in `tests/conftest.py` builds a separate
minimal synthetic config). Added `tests/test_shipped_config_scores.py` with
two tests: `default`-tier scores aren't all identical (the exact regression
this issue fixes), and every model in every tier has a positive score.
Verified both pass against the fixed `flexrouter.yaml`, and separately
verified (via a throwaway copy with all scores forced back to 50) that the
"not all identical" assertion fails against the original flat-50 shape, i.e.
it would have caught this exact bug. Full suite: 183 passed, 0 failed.
