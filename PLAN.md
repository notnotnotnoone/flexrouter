# flexrouter: from library to server

Plain-language plan. Decided 2026-09-18.

## What we're changing and why

flexrouter is a Python library today. It becomes a **server you start when you
want it**, like Ollama: one program, one address, a dashboard you open in a
browser, and an API that any chat app can point at.

## The four real problems found in the current code

1. **It can only do one thing at a time — and crashes if asked to do two.**
   The router keeps a single internal "worker" that isn't safe to use from two
   requests at once. The second overlapping request jams or errors out.
   This is the reported hanging and crashing. Any chat app triggers it
   immediately, because chat apps send overlapping requests constantly.

2. **It throws away every error message.** When a provider rejects a request,
   flexrouter records that something failed but discards *what* the provider
   said. The real log has 239 failures and zero explanations. This is why
   "it's broken" was the most that could be said about it.

3. **It never gives up on dead models.** Every failure is treated as
   temporary: wait 30 seconds, try again, forever. But a model that a provider
   has deleted is never coming back. The log shows 239 penalties and 235
   recoveries — an endless loop. The config lists 96 models written months ago
   against providers that rotate models constantly.

4. **Entering API keys is painful, so it got worked around.** The config now
   has zero environment-variable references and four keys pasted in directly.
   Two knock-on effects: `config export` hands out live keys, and the
   dashboard's Save button rewrites the whole 13.5KB config file, deleting
   every comment in it.

## Decisions

| Decision | Choice |
|---|---|
| Shape | Background server, started manually (not at boot, for now) |
| Python API | Becomes a thin client that talks to the server; the server is the only brain |
| HTTP layer | Rebuilt on FastAPI + uvicorn — handles many requests at once |
| Ports | One. Dashboard, OpenAI API, and dashboard data all on the same address |
| Config location | One global location, not "whatever folder you're standing in" |
| API keys | Plaintext file in the flexrouter folder, separate from the config |
| Key entry | In the dashboard, and **tested on save** — green/red per provider |
| Dashboard | Much more debugging detail (depends on #2 being fixed first) |

### Why one brain matters

Rate limits and penalties are facts about a provider, not about your program.
Today every Python process that uses flexrouter keeps its *own* private tally,
so two of them both believe they have the full budget, both spend it, and both
get rate-limited. One server owning that tally is the actual fix.

## Status

Steps 1-3 are done. 300 tests pass. The server was also run for real against
the live config, which is how the diagnosis below was obtained.

### What was actually wrong, once errors stopped being discarded

- **groq** - the API key is rejected (401). Dead or revoked.
- **cerebras** - the account returns 402 "Payment required. Visit your billing tab."
- **googleai** - `gemini-2.0-flash`, `gemini-2.0-flash-001` and `gemini-2.5-flash`
  all answer 404: "no longer available, use models/gemini-3.6-flash".
- **cerebras models** - `zai-glm-4.7` is archived; `llama-3.1-8b-instant` does
  not exist on that provider.

And the reason it looked like *total* breakage: an auth failure aborted the
whole request instead of trying the next provider, so one dead groq key took
down every tier that mentioned groq - even when other providers in that tier
were healthy.

## Order of work

1. ~~**Stop discarding errors.**~~ **DONE** - provider messages are now kept.
2. ~~**Tell "temporarily down" apart from "permanently gone."**~~ **DONE** -
   deleted models (404/410) are quarantined for 24h with the reason recorded;
   rejected keys (401/403) and billing failures (402) sideline the whole
   provider and routing moves on to the next one.
3. ~~**Rebuild the HTTP layer**~~ **DONE** - FastAPI/uvicorn, one port for
   dashboard + API + data. Verified with 5 concurrent live requests (1.9s wall
   clock, no crash). A saturated tier now returns a 504 explaining itself
   instead of hanging forever.
4. **Keys in the dashboard**, with a Save button that tests each key and lists
   which models are actually reachable.
5. **Debugging views in the dashboard**, now that there's real data to show.

## Explicitly not doing yet

- Auto-start at login / installer / tray icon
- The OpenWebUI toolkit project (parked)
- Encrypting keys (accepted risk; local machine only)


## What you need to do (not code problems)

1. Replace the groq API key - the current one is rejected.
2. Check the cerebras billing tab - that account returns "Payment required".
3. Refresh the model list - a chunk of the 96 configured models no longer
   exist at their providers. `flexrouter refresh` rediscovers them.
