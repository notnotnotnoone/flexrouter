<div align="center">

<img src="docs/assets/banner.svg" alt="flexrouter — stack every provider's free tier behind one address" width="100%">

[![Tests](https://github.com/notnotnotnoone/flexrouter/actions/workflows/test.yml/badge.svg)](https://github.com/notnotnotnoone/flexrouter/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

**Run entirely on free tiers.** Groq, Cerebras, Google AI Studio, OpenRouter's free models, and others each give you a real (if small) free allowance. flexrouter pools them into one address, and when one model's free quota runs dry for the minute, it fails over to the next one instead of stopping or billing you — so your app keeps answering, and you keep paying $0.

[Quickstart](#quickstart) · [Free tier stacking](#free-tier-stacking) · [How routing works](#how-routing-works) · [Dashboard](#dashboard) · [Contributing](#contributing)

</div>

---

flexrouter is a small program that runs in the background on your computer. Every app you have — scripts, other tools, whatever — sends its AI requests to it at one shared address, instead of each app juggling its own list of free-tier providers and keys. flexrouter picks the best available model out of a list you set up, automatically switches to another one the moment one runs out of free quota for the minute, tracks how much (if anything) you're spending, and shows you all of this in a live dashboard.

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

### Why flexrouter

| | |
|---|---|
| 🆓 **Stack free tiers** | Groq, Cerebras, Google AI Studio, OpenRouter's free models, and more — pool their free rate limits into one bucket instead of hand-rolling your own fallback chain. |
| 🔁 **Automatic failover** | A model that's slow, rate-limited, or down is skipped and retried on the next best one — mid-outage, not after your app crashes. |
| 🔑 **Multi-key rotation** | Add several keys per provider (e.g. two free Groq accounts); a rejected key is skipped for the next one automatically. |
| 🔌 **Drop-in** | Same API shape as OpenAI. Point existing tools at it — no SDK, no code changes. |
| 🧭 **Named buckets, not model names** | Your app asks for `low` or `high`; flexrouter decides which model actually serves it. |
| 💸 **Spend aware** | Per-provider daily budget caps, enforced before a request goes out — cap a paid fallback at $0 and it's never actually billed. |
| 📊 **Live dashboard** | Telemetry, chat, request logs, account status, and settings in one page. |

### Free tier stacking

Most providers' free tiers are narrow on their own — a few dozen requests a minute, sometimes less. flexrouter's routing was built around this: put several free-tier models from different providers in one bucket, and when one hits its per-minute limit, [the next one takes over automatically](#how-routing-works), not after an error bubbles up to your app.

```yaml
providers:
  groq:
    base_url: https://api.groq.com/openai/v1
  cerebras:
    base_url: https://api.cerebras.ai/v1
  googleai:
    base_url: https://generativelanguage.googleapis.com/v1beta/openai/

buckets:
  free:
    - provider: groq
      model: llama-3.1-8b-instant
      score: 90
      rpm: 30
      tpm: 6000
      context_window: 131072
    - provider: cerebras
      model: gpt-oss-120b
      score: 85
      rpm: 30
      tpm: 60000
      context_window: 131072
    - provider: googleai
      model: models/gemini-2.0-flash-lite-001
      score: 80
      rpm: 15
      tpm: 1000000
      context_window: 1048576
```

Two ways to stretch it further:

- **[Multiple keys per provider](#multiple-keys-per-provider)** — add a second free account for a provider and flexrouter rotates to it once the first is rate-limited, instead of waiting it out.
- **Zero-out anything you don't want to pay for** — set `provider_budget: { openai: 0 }` and flexrouter never sends a request to it, even if it's sitting in the same bucket as a fallback.

## Contents

- [Install](#install)
- [Where your settings live](#where-your-settings-live)
- [Quickstart](#quickstart)
- [Free tier stacking](#free-tier-stacking)
- [How routing works](#how-routing-works)
- [Config reference](#config-reference)
- [Using it directly from Python](#using-it-directly-from-python)
- [Session stickiness](#session-stickiness)
- [Multiple keys per provider](#multiple-keys-per-provider)
- [CLI](#cli)
- [Terminal UI](#terminal-ui)
- [Dashboard](#dashboard)
- [Request log](#request-log)
- [Picking up changes while it's running](#picking-up-changes-while-its-running)
- [Rebuilding your model list](#rebuilding-your-model-list)
- [Moving from an older setup](#moving-from-an-older-setup)
- [Contributing](#contributing)
- [Credits](#credits)

## Install

Not on PyPI yet — install from GitHub for now:

```bash
pip install git+https://github.com/notnotnotnoone/flexrouter.git
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

**1. Start it:**

```bash
flexrouter dashboard
```

This starts the service and opens `http://localhost:4891`. The Overview has a
**Get started** card whose five steps tick themselves as you go:

1. **Add a provider.** Every preset is listed Free or Paid, with a **Get a key ↗** link.
2. **Paste its key**, then press **Test**. (`flexrouter keys add groq` works too.) Keys are saved to your user account, never into a settings file.
3. **Add models with AI.** The prompt carries the provider's real list of model IDs, and every row is checked against it before anything is saved.
4. **Test all.** It says "hi" to every model once and shows what each one answered, or why it didn't.
5. **Point your app at it.** **Copy Python** and **Copy curl** buttons give you the snippet.

**2. Point your apps at it:**

Anything that can talk to OpenAI's API can now talk to flexrouter: change its
base address to `http://localhost:4891/v1` and use a bucket name (like `fast`)
wherever it asks for a model. No flexrouter-specific code needed.

Prefer to write the settings file by hand? The
[Configuration Guide](docs/2-Configuration-Guide.md) has the YAML, and the
[Config reference](#config-reference) below lists every field. If you're
writing Python, you can also call flexrouter directly — see
[Using it directly from Python](#using-it-directly-from-python).

## How routing works

Your models are grouped into named lists — for example `fast` and `smart`, but you can name them anything, e.g. `cheap`, `smart`, `nuclear`. flexrouter calls each of these a **bucket**, and each model in it has a score from 1–100 (higher means "prefer this one"). On each request:

1. Models that aren't Ready are skipped: Busy ones until their countdown ends, Needs-you ones until you fix them on the **Status** page.
2. Models that have used up today's spending cap for their provider are skipped.
3. Models that have hit their per-minute request or token limit are skipped.
4. Models too small to fit the message are skipped (you'll get a `ContextWindowWarning`).
5. Among what's left, flexrouter picks the highest-scoring model — but to avoid always hammering the single top choice, it picks randomly among any models scoring within 20% of the best one.

If nothing in a bucket is available: by default flexrouter waits until something frees up. Pass `wait=False` and it will raise `RouterBusy` immediately instead.

Buckets don't spill into each other — if everything in `low` is busy, flexrouter will not quietly reach into `high` on your behalf.

**When a model fails**, flexrouter moves to the next one in the bucket straight away. It never sleeps between tries, tries every model in the bucket, and gives up after 30 seconds ("Give up after" in Settings). A request that names one exact model (`groq/llama-3.1-8b-instant`, with a `/`) is **pinned**: if that model is busy or broken you get the error at once, with no fallback.

**Errors are honest.** A failed request returns the provider's own status and exact words in the usual OpenAI `error.message`, plus `error.flexrouter.request_id` (a `req_…` ID, also sent as a header on every response) and `error.flexrouter.attempts[]`, with one entry per model tried: its status, the provider's message, and how long it took. See [the API reference](docs/5-API-Reference.md).

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
  session_ttl_minutes: 30          # sticky session expiry

  failover_budget_seconds: 30      # give up after this long trying other models

  provider_budget:                 # optional daily USD cap per provider
    openai: 5.00
    groq: 2.00

  hooks:                           # run before routing
    - detect_vision                # auto-sets vision=True if messages contain images
    - estimate_tokens              # pre-checks context window fit
```

A model whose `rpm`/`tpm` you don't know yet can take `null` instead of a number — flexrouter leaves it unenforced until it learns the real limit from the provider's own headers or a 429.

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

| Command | Does |
|---|---|
| `flexrouter tui` | Open the live terminal UI — overview, keys, requests, paths (see [Terminal UI](#terminal-ui)) |
| `flexrouter serve` | Start the server (API + dashboard) without opening a browser |
| `flexrouter dashboard` | Start the server and open the dashboard in your browser |
| `flexrouter dashboard --log` | As above, plus a server activity log and a live **Logs** page in the dashboard (also works with `serve`) |
| `flexrouter status` | Print current spending/health to the terminal |
| `flexrouter doctor` | Show where your settings, keys, and data live |
| `flexrouter keys add <name>` | Save a key for a provider |
| `flexrouter keys add --list` | Show every provider, with and without a key saved (adds nothing) |
| `flexrouter keys list` | Show your saved keys (masked) |
| `flexrouter keys rm <name> <id>` | Remove a saved key |
| `flexrouter refresh` | Check available models and rate limits ([see below](#rebuilding-your-model-list)); changes nothing |
| `flexrouter config reset` | Undo changes made from the dashboard |
| `flexrouter config export` | Print a shareable copy of your settings (keys hidden) |
| `flexrouter config import <token>` | Apply a config exported elsewhere |

## Terminal UI

```bash
flexrouter tui
```

The same router, in your terminal, split into four tabs — and it works whether or not the service is running, because it reads your flexrouter home directly rather than talking to a live service:

| Page | Shows |
|---|---|
| **Overview** | The Get started checklist, whether things are fine, who answered hour by hour, buckets, providers, recent requests |
| **Providers & keys** | Add a provider, paste and Test its keys |
| **Models** | Every model's status, score and answer rate; Add / Rank / Find rate limits with AI |
| **Buckets** | Each bucket's order; drag a model onto a bucket; Try it |
| **Requests** | Every request; press one for the prompt, reply, thinking and every model tried |
| **Playground** | Chat with any bucket or model |
| **Status** | Every model's one status, one fix button each, and Test all |
| **Allowance** | Free-tier headroom per provider, on each provider's own clock |
| **Settings** | Every setting in plain words, backup and restore, the Danger zone |

Dark or light follows your system, and it works at phone width. The API, the dashboard, and the dashboard's own data all run on this one port (`4891` unless you set a different one under `settings: port:`).

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

## Contributing

Bug reports, feature requests, and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup and how to submit changes. See [CHANGELOG.md](CHANGELOG.md) for release history.

## Credits

Inspired by [modelrelay](https://github.com/ellipticmarketing/modelrelay).

---

<div align="center">

[MIT License](LICENSE) · [Report a bug](https://github.com/notnotnotnoone/flexrouter/issues/new?template=bug_report.md) · [Request a feature](https://github.com/notnotnotnoone/flexrouter/issues/new?template=feature_request.md)

</div>
