# Getting Started with flexrouter

**Goal:** Install flexrouter, set up your first provider from the dashboard, and make your first routing call. No settings file to write.

**Time:** 10 minutes

---

## What You'll Build

By the end of this tutorial, you'll have:
- ✅ flexrouter installed
- ✅ The flexrouter service running on your machine — the one process that does the routing for everything on it
- ✅ One provider set up from the dashboard, with its key saved safely
- ✅ Its models added with AI, every ID checked against the provider's real list
- ✅ Every model tested with one "hi"
- ✅ The **terminal UI** open, watching the same service from your terminal
- ✅ A working Python script that asks that service to route a request to the cheapest available model

---

## Step 1: Install flexrouter

```bash
pip install flexrouter
```

Verify the installation (it also prints where your settings and keys will live):

```bash
flexrouter doctor
```

---

## Step 2: Start the Service and Open the Dashboard

flexrouter does all of its routing in **one background service** on your
computer, so every project and every app on the machine shares the same
settings, the same keys, and the same running total of what each provider has
left. Nothing routes until it is running. Start it and open your browser with:

```bash
flexrouter dashboard
```

This opens `http://localhost:4891`. Leave that terminal running.

On the first start the Overview has a **Get started** card on top. It lists
five steps and ticks each one itself as soon as it has really happened, so you
never press Next. The rest of this page walks through the same five steps.

> Prefer no browser? `flexrouter serve` starts the exact same service without
> opening one. If something later fails with *"flexrouter isn't running.
> Start it with: flexrouter serve"*, that message is telling you the truth.

> Want the server's own diary? `flexrouter dashboard --log` also writes an
> activity log and adds a **Logs** page under *System*. Without `--log` that
> page just says "Logging is off" — see
> [Logging and the Logs page](4-Advanced-Usage.md#logging-and-the-logs-page).

---

## Step 3: Add a Provider and Paste Its Key

The card's first step lists every provider flexrouter knows, marked **Free**
or **Paid**, each with a **Get a key ↗** link. Hover one for its free-tier
note. We'll use **Groq** as an example (free and fast).

1. Press **Get a key ↗** next to Groq, sign in, and create an API key.
2. Press **Groq** (or go to **Providers & keys**) and paste the key.
3. Press **Test** next to the key. It sends one small "hi" to a model. When it
   answers, step 2 on the card ticks.

The key is saved to your own user account on this machine, never into a
settings file that might get shared. From a terminal, `flexrouter keys add
groq` does the same thing.

---

## Step 4: Add Models With AI

Go to **Models → Add models with AI** (the card's step 3 has an **Open**
button). flexrouter builds a prompt that already contains Groq's **real list
of model IDs**. Paste it into any chatbot, paste the answer back, and review
every row before anything is saved.

- A row whose ID isn't on Groq's real list shows **"not a real ID, did you
  mean X? [use]"** and can't be saved until you fix it.
- A rate limit the AI doesn't know stays empty: flexrouter learns it from
  the provider's own headers instead of guessing.
- Speech, image and embedding models are kept in a folded **"Not chat models
  yet"** list at the bottom of Models, because flexrouter only routes chat
  models today.

---

## Step 5: Test All

Press **Test all** on the card (it's also on the **Status** page). It says
"hi" to every model once, two at a time, with room for up to 512 tokens so
a model that thinks first still gets to answer. Each model shows how long it
took, or the provider's reason for not answering. It only runs when you
press it; flexrouter never pings models in the background.

Anything that didn't answer shows up on **Status** with one sentence and at
most one button, for example "Groq doesn't know this name. Did you mean
llama-3.1-8b? **[Use llama-3.1-8b]**".

---

## Step 6: Point Your App at flexrouter

The card's last step has **Copy Python** and **Copy curl** buttons. The
address is `http://localhost:4891/v1`, and the "model" is the name of a
bucket, such as `fast`. The step ticks when your app's first request
arrives. Requests you send from the dashboard's own Playground don't count.

---

## Step 7: Watch It From the Terminal — the TUI

If you live in your terminal, `flexrouter tui` gives you the same picture
without a browser:

```bash
flexrouter tui
```

Open it in a **second** terminal (leave the service running in the first). It
has four tabs — **Overview** (totals and today's spend), **Keys** (add,
remove, or disable keys without leaving the screen), **Requests** (your recent
calls), and **Doctor** (where everything lives) — and refreshes on its own
every couple of seconds. Press `a` to add a key, `d` to remove the selected
one, `r` to refresh, `q` to quit.

It reads your flexrouter home directly, so it works whether or not the service
is currently running.

---

## Step 8: Use Any OpenAI-Compatible App

Because the service speaks the OpenAI shape, any OpenAI SDK — or any chat app
that lets you change the base URL — works by pointing at it:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:4891/v1")

# Use "auto" to let flexrouter pick the best available model
response = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.choices[0].message.content)
```

**Model naming:**
- `"auto"` — flexrouter picks the tier with the highest-scoring model
- `"cheap"`, `"premium"` — the plain name of a tier routes through that tier
- `"all"` — every model in every bucket, once each (built-in; respects exclude and rate limits like any bucket)
- `"groq/llama-3.1-8b-instant"` — a name with a `/` in it pins one exact
  model, with no failing over to another
- The older `"auto-cheap"` spelling still works, so an app that already has it
  saved keeps working

List available models: `GET http://localhost:4891/v1/models`

---

## Step 9: Call It From Python

Last, the library itself — for Python code that wants to skip the web address
and call flexrouter in-process. Create a file `hello_flexrouter.py`:

```python
from flexrouter import FlexRouter

# Connect to the service running on this machine.
# This reads your shared settings only to find the port -- all the routing
# happens in the service, not here.
router = FlexRouter()

# Make a request
response = router.generate(
    messages=[
        {"role": "user", "content": "Say hello and tell me a one-sentence joke."}
    ],
    tier="cheap",
)

# Print the response
text = response["choices"][0]["message"]["content"]
print("Model response:")
print(text)

# Check usage
usage = response["usage"]
print(f"\nTokens used: {usage['total_tokens']}")

router.close()
```

Run it:

```bash
python hello_flexrouter.py
```

You should see the model's response and token count — and the same call
appears in the dashboard's Request Logs and the TUI's Requests tab.

---

## Next Steps

You've got the basics! Here's where to go next:

- **Rather write the settings file by hand?** → Read [Configuration Guide](2-Configuration-Guide.md)
- **Understand how routing works?** → Read [Concepts](3-Concepts.md)
- **Handle multiple tiers or advanced scenarios?** → Read [Advanced Usage](4-Advanced-Usage.md)
- **Look up a specific method or class?** → Read [API Reference](5-API-Reference.md)

---

## Troubleshooting

### "Can't find my settings" or you're not sure where things are
Run `flexrouter doctor`. It prints exactly where your settings and keys live, which key each provider will actually use, and flags anything it can't read. If you want the shared place to live somewhere else, set `FLEXROUTER_HOME` to that folder before running flexrouter.

### "API key rejected"
Check that flexrouter is actually seeing your key:
```bash
flexrouter keys list
```
If it's missing, save it again with `flexrouter keys add groq`.

### "flexrouter isn't running"
The service isn't up. Start it with `flexrouter dashboard` (or `flexrouter serve` if you don't want a browser) and leave it running in its own terminal. The message names the address it tried, so if that address looks wrong, check the `port` in your settings file.

### "No models available" or a model keeps failing
Open **Status** in the dashboard. Every model that isn't Ready is listed with one plain sentence and at most one button. Press a row to see exactly what the provider said. `flexrouter doctor` also checks your settings file for problems.

---

## Quick Reference: Key Concepts

| Term | Meaning |
|------|---------|
| **Bucket** | A named group of models (e.g. `fast`, `smart`); your app uses its name as the "model". The Python API still calls it `tier`. |
| **Score** | Priority (1–100) within a bucket; higher wins when available |
| **RPM / TPM** | A model's requests / tokens per minute. Leave them empty and flexrouter learns them from the provider |
| **Status** | Every model is Ready, Busy, Struggling, Needs you or Off — see the Status page |
| **state_dir** | Folder where audit logs and health data are stored |
| `flexrouter doctor` | Shows where your settings and keys live, and which key each provider will use |
| `flexrouter keys add/list/rm` | Save, view, or remove a provider's key |
| **The service** | The one background process that does all the routing for your machine |
| `flexrouter dashboard` | Starts the service and opens the dashboard in your browser — the main way to watch and drive flexrouter |
| `flexrouter tui` | The same picture in your terminal — overview, keys, requests, doctor |
| `flexrouter serve` | Starts the service (API + dashboard) on port 4891 without opening a browser — nothing routes until it's running |

---

You're ready to route! 🚀
