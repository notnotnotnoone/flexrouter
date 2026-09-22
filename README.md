# flexrouter

[![Tests](https://github.com/notnotnotnoone/flexrouter/actions/workflows/test.yml/badge.svg)](https://github.com/notnotnotnoone/flexrouter/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

flexrouter is a small program that runs in the background on your computer. Every app you have — scripts, other tools, whatever — sends its AI requests to it at one shared address, instead of each app juggling its own list of models and keys. flexrouter picks the best available model out of a list you set up, automatically switches to another one if a model is slow, out of quota, or down, tracks how much you're spending, and shows you all of this in a live dashboard.

It speaks the same language as OpenAI's API, so anything that already knows how to talk to OpenAI can point at flexrouter instead, with no special code:

```bash
flexrouter dashboard
```

```bash
curl http://localhost:4891/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "low", "messages": [{"role": "user", "content": "classify this text..."}]}'
```

(`model` here is one of your buckets, like `low` or `high` — see [How routing works](#how-routing-works).)

If you're writing Python and would rather call it directly without going through the web address, see [Using it directly from Python](#using-it-directly-from-python) below.

## Install

```bash
pip install flexrouter
```

Requires Python 3.11+.

## Where your settings live

flexrouter keeps one shared settings file per computer, not one per project. That way every project on your machine uses the same list of models and the same saved keys, instead of you having to set it up again for each one.

The settings file is called `config.yaml` and lives in a folder flexrouter picks for you automatically:

- Windows: `%LOCALAPPDATA%\flexrouter`
- Mac/Linux: `~/.config/flexrouter`

If you want it somewhere else, set the `FLEXROUTER_HOME` environment variable to the folder you want, and flexrouter will use that instead.

The first time flexrouter runs, it creates this folder and writes a starter `config.yaml` into it, with an empty list of models. You then fill it in yourself, by editing that file.

To see exactly where things are on your machine, and which key each provider will use, run:

```bash
flexrouter doctor
```

Two things worth knowing about this file:

- **It's yours.** flexrouter reads it but never rewrites it, so any comments or notes you leave in it stay put.
- **Anything you change from the dashboard is saved separately** — in a second file next to it, layered on top when flexrouter reads your settings — so your hand-written file still doesn't get touched.

If a change you made from the dashboard ever stops flexrouter from reading your settings, `flexrouter config reset` undoes those changes and puts you back where you were. It only clears the dashboard's file — your own settings file is not involved.

If you're moving from an older, per-project settings file, see [Moving from an older setup](#moving-from-an-older-setup) below — flexrouter does not do this move for you automatically.

## Quickstart

**1. Set up your settings file:**

Open `config.yaml` in your flexrouter folder and fill it in. To find the folder,
run `flexrouter doctor` — it prints the exact path. A small one looks like this:

```yaml
settings:
  port: 4891

providers:
  groq:
    base_url: https://api.groq.com/openai/v1

  openai:
    base_url: https://api.openai.com/v1

buckets:
  low:
    - provider: groq
      model: llama-3.1-8b-instant
      score: 85
      rpm: 60
      tpm: 60000
      context_window: 131072

  high:
    - provider: openai
      model: gpt-4o
      score: 95
      rpm: 60
      tpm: 150000
      context_window: 128000
      vision: true
```

**2. Save your API keys:**

```bash
flexrouter keys add groq
flexrouter keys add openai
```

Each command asks you to paste the key in without showing it on screen, and saves it to your own user account on this machine — never into the settings file above. (You can still fall back to an environment variable, or type a key straight into the settings file, but the second one is discouraged and flexrouter will warn you if you do it.)

**3. Start it, then point your apps at it:**

```bash
flexrouter serve
```

Anything that can talk to OpenAI's API can now talk to flexrouter — just change its base address to `http://localhost:4891/v1` and use a bucket name (like `low` or `high`) wherever it asks for a model. No flexrouter-specific code needed.

If you're writing Python, you can also skip the web address entirely and call it directly — see [Using it directly from Python](#using-it-directly-from-python).

## How routing works

Your models are grouped into named lists — the quickstart above calls them `low` and `high`, but you can name them anything, e.g. `cheap`, `smart`, `nuclear`. flexrouter calls each of these a **bucket**, and each model in it has a score from 1–100 (higher means "prefer this one"). On each request:

1. Models that recently failed are skipped for a while.
2. Models that have used up today's spending cap for their provider are skipped.
3. Models that have hit their per-minute request or token limit are skipped.
4. Models too small to fit the message are skipped (you'll get a `ContextWindowWarning`).
5. Among what's left, flexrouter picks the highest-scoring model — but to avoid always hammering the single top choice, it picks randomly among any models scoring within 20% of the best one.

If nothing in a bucket is available: by default flexrouter waits until something frees up. Pass `wait=False` and it will raise `RouterBusy` immediately instead.

Buckets don't spill into each other — if everything in `low` is busy, flexrouter will not quietly reach into `high` on your behalf.

## Config reference

### Buckets

```yaml
buckets:
  <name>:
    - provider: <provider-name>   # must match a key in providers:
      model: <model-id>           # passed to the API
      score: 85                   # 1-100, higher = preferred
      rpm: 60                     # requests per minute limit
      tpm: 60000                  # tokens per minute limit
      context_window: 131072      # max tokens this model accepts
      vision: false               # set true for image-capable models
```

Bucket names are arbitrary — use `cheap`/`smart`/`nuclear` or whatever makes sense. (You may still see the older name `tiers:` in examples or old files — it still works the same way, and the `tier=` argument on `generate()` is still called that.)

### Providers

```yaml
providers:
  <name>:
    base_url: https://api.example.com/v1   # OpenAI-compatible endpoint
```

Add keys with `flexrouter keys add <name>` (see [Where your settings live](#where-your-settings-live)) rather than writing them here.

### Settings

```yaml
settings:
  port: 4891                       # the one port everything runs on

  window_seconds: 60               # sliding window duration
  penalty_base_seconds: 30         # first-failure wait time
  penalty_max_seconds: 1800        # cap (30s → 60s → 120s → ... → 1800s)
  session_ttl_minutes: 30          # sticky session expiry

  retry_policy: balanced           # conservative | balanced | aggressive
  # Or manual:
  # retries: 3
  # backoff_seconds: 2

  provider_budget:                 # optional daily USD cap per provider
    openai: 5.00
    groq: 2.00

  hooks:                           # run before routing
    - detect_vision                # auto-sets vision=True if messages contain images
    - estimate_tokens              # pre-checks context window fit
```

| Preset | Retries | Wait between tries |
|---|---|---|
| `conservative` | 2 | 5s |
| `balanced` | 3 | 2s |
| `aggressive` | 5 | 1s |

## Using it directly from Python

You don't need this if you're already sending requests to `http://localhost:4891/v1` — this is only for Python code that wants to skip the web address and call flexrouter in-process instead.

```python
from flexrouter import FlexRouter

router = FlexRouter()
response = router.generate(messages=[...], tier="low")
```

### `FlexRouter(config_path=None)`

Reads your settings from the shared flexrouter folder described above. Pass a path to use a different file instead.

```python
router = FlexRouter()                          # your shared settings file
router = FlexRouter("path/to/config.yaml")      # a specific file instead
```

### `generate(messages, tier, *, wait=True, vision=False, session_id=None, **kwargs)`

Synchronous. Blocks until a model responds (or raises if `wait=False` and none are available). `tier` is the name of the bucket to use.

```python
response = router.generate(
    messages=[{"role": "user", "content": "..."}],
    tier="low",
    wait=True,            # wait for a slot to open up (default)
    vision=False,         # only route to image-capable models
    session_id="conv-1",  # sticky session — same model for this ID
    max_tokens=512,       # passed through to the provider
    temperature=0.2,
)
text = response["choices"][0]["message"]["content"]
tokens = response["usage"]["total_tokens"]
```

### `agenerate(messages, tier, *, wait=True, vision=False, session_id=None, **kwargs)`

Async version. Same arguments.

```python
response = await router.agenerate(messages=[...], tier="medium")
```

### `reload()`

Force flexrouter to re-read your settings file right now. It also does this on its own whenever the file changes on disk, so you rarely need to call this yourself.

### Exceptions

```python
from flexrouter import RouterBusy, RouterError, ContextWindowWarning
import warnings

try:
    response = router.generate(messages=[...], tier="low", wait=False)
except RouterBusy:
    # Everything in this bucket is busy right now
    ...
except RouterError:
    # A key was rejected, or a provider kept failing — needs your attention
    ...

# Context window warning (not an error — some models were skipped, the rest still worked)
with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    response = router.generate(messages=long_messages, tier="low")
    if any(issubclass(x.category, ContextWindowWarning) for x in w):
        print("Some models skipped: message too long for their context window")
```

### Context manager

```python
with FlexRouter() as router:
    response = router.generate(messages=[...], tier="low")
# background work stops cleanly on exit
```

## Session stickiness

Pass a `session_id` to keep a conversation on the same model, so answers stay consistent:

```python
for turn in conversation:
    response = router.generate(
        messages=turn["messages"],
        tier="medium",
        session_id=turn["conversation_id"],
    )
```

The pin expires after `session_ttl_minutes` of inactivity (default 30). If the pinned model stops being available partway through, that one call moves to the next best model without losing the pin for later calls.

## Multiple keys per provider

Add more than one key for the same provider and flexrouter rotates between them automatically:

```bash
flexrouter keys add groq --label "key 1"
flexrouter keys add groq --label "key 2"
```

A key that gets rejected (rate-limited) is skipped immediately in favor of the next one.

## CLI

```bash
flexrouter serve            # start the server (API + dashboard) without opening a browser
flexrouter dashboard        # start the server and open the dashboard in your browser
flexrouter status           # print current spending/health to the terminal
flexrouter doctor           # show where your settings, keys, and data live
flexrouter keys add <name>  # save a key for a provider
flexrouter keys list        # show your saved keys (masked)
flexrouter keys rm <name> <id>  # remove a saved key
flexrouter refresh          # check available models and rate limits (see below); changes nothing
flexrouter config reset     # undo changes made from the dashboard
flexrouter config export    # print a shareable copy of your settings (keys hidden)
flexrouter config import <token>
```

## Dashboard

```bash
flexrouter dashboard
# → http://localhost:4891
```

Six tabs: **Live Telemetry** (request/token rates, countdowns for anything cooling down, search/filter), **Chat** (try any bucket live), **Request Logs** (your last 50 requests), **Account Status** (spending per provider), **Settings** (view/edit config), **Setup** (getting-started guide). Dark/light theme toggle.

The API, the dashboard, and the dashboard's own data all run on this one port (`4891` unless you set a different one under `settings: port:`).

## Request log

Every request is appended to `audit.csv`, inside flexrouter's data folder (see `flexrouter doctor` for the exact path):

```
timestamp,tier,provider,model,prompt_tokens,completion_tokens,cost_usd,latency_ms,status
2026-05-27T12:01:00,low,groq,llama-3.1-8b-instant,1204,320,0.00008,280,ok
```

A `health.json` file next to it is updated after every request with running totals.

## Picking up changes while it's running

You don't have to restart anything after a change. flexrouter checks for one the next time your code asks it for an answer, and it watches all three of the files a change can come from:

- your settings file, if you edit it by hand
- the file your dashboard changes are saved in
- your keys file, so a key you add is usable straight away

Anything already in progress carries over — how close each model is to its rate limit, and how long a struggling model is being rested for.

If a change leaves your settings unreadable, flexrouter keeps running on the last set it read successfully rather than failing whatever you asked it to do. Fix the file and it picks up the corrected version on the next request. Be aware that it won't announce this anywhere yet, so if a change seems to have had no effect, that's the first thing to check.

## Rebuilding your model list

`flexrouter refresh` checks each provider you have a key for, and finds which models are actually available right now along with their real rate limits. It does not change anything for you: what it found is written to a record in your data folder (see `flexrouter doctor` for the path), and your settings file is left exactly as it was, comments and all — the same as everything else described above. A later version will let you review what was found and accept the parts you want; for now, `refresh` only tells you what it saw.

## Moving from an older setup

If you're coming from a version of flexrouter that used a settings file per project, note that your old file is not picked up or merged in automatically — you rebuild your buckets and models fresh in the new shared file, by hand.

The one thing that is carried over for you is your keys:

```bash
flexrouter keys import path/to/old-flexrouter.yaml
```

This copies any keys that were typed directly into that old file into your new, shared key store. It leaves the old file exactly as it was, and any keys that were already environment-variable references are left alone too, since those already work without any changes.

## PyPI publish

Tag a release to publish:

```bash
git tag v0.1.0
git push origin main --tags
```

Requires a [PyPI trusted publisher](https://docs.pypi.org/trusted-publishers/) configured for this repo with environment `pypi`.

## Credits

Inspired by [modelrelay](https://github.com/ellipticmarketing/modelrelay).
