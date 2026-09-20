# Getting Started with flexrouter

**Goal:** Install flexrouter, configure it for your first LLM provider, and make your first routing call.

**Time:** 10 minutes

---

## What You'll Build

By the end of this tutorial, you'll have:
- ✅ flexrouter installed
- ✅ One provider set up, with its key saved safely
- ✅ The flexrouter service running on your machine — the one process that does the routing for everything on it
- ✅ A working Python script that asks that service to route a request to the cheapest available model

---

## Step 1: Install flexrouter

```bash
pip install flexrouter
```

Verify the installation:

```bash
flexrouter --version
```

---

## Step 2: Set Up Your First Provider

Choose one provider to start with. We'll use **Groq** as an example (free, fast, generous rate limits).

### Get an API Key

1. Go to [https://console.groq.com](https://console.groq.com)
2. Sign up or log in
3. Create an API key

### Save the Key

flexrouter keeps every key in one safe, shared place on your computer — never inside a settings file that might get shared or copied around. Save your new key with:

```bash
flexrouter keys add groq
```

It will ask you to paste the key without showing it on screen. You can check it saved correctly (without ever printing the full key) with `flexrouter keys list`.

---

## Step 3: Point flexrouter at the Model

flexrouter keeps one settings file for your whole computer, not one per project. Run this to see where it lives:

```bash
flexrouter doctor
```

It prints the folder your settings live in, and confirms whether it can already see a usable key for each provider you've set up. Open the settings file it shows you and add:

```yaml
tiers:
  cheap:
    - provider: groq
      model: llama-3.1-8b-instant
      score: 100
      rpm: 60
      tpm: 60000
      context_window: 131072

providers:
  groq:
    base_url: https://api.groq.com/openai/v1
```

**What this means:**
- **tiers**: Define routing tiers. Here we have one tier called `cheap`.
- **models**: List models available in this tier. Score (1–100) determines preference.
- **providers**: Define how to reach each provider (base URL). The key you saved in Step 2 is picked up automatically — it doesn't need to be written here.

You never need to create or find this file yourself for a fresh setup — `flexrouter doctor` always tells you exactly where it is. It's a plain text file you write by hand, and flexrouter never rewrites it behind your back, so any notes or comments you leave in it stay put. Changes you make from the dashboard are kept separately and layered on top when flexrouter starts.

---

## Step 4: Start the Service

flexrouter does all of its routing in **one background service** on your
computer, so every project and every app on the machine shares the same
settings, the same keys, and the same running total of what each provider has
left. Nothing routes until it is running. Start it:

```bash
flexrouter serve
```

Leave that running in its own terminal. It listens on
`http://127.0.0.1:4891`.

> If you skip this step, the next one fails with
> *"flexrouter isn't running. Start it with: flexrouter serve"*. That message
> is telling you the truth — come back here and run it.

---

## Step 5: Make Your First Call

Open a **second** terminal (leave the service running in the first one) and
create a file `hello_flexrouter.py`:

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
print(f"
Tokens used: {usage['total_tokens']}")

router.close()
```

Run it:

```bash
python hello_flexrouter.py
```

You should see the model's response and token count.

---

## Step 6: Use Any OpenAI-Compatible App

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
- `"groq/llama-3.1-8b-instant"` — a name with a `/` in it pins one exact
  model, with no failing over to another
- The older `"auto-cheap"` spelling still works, so an app that already has it
  saved keeps working

List available models: `GET http://localhost:4891/v1/models`

---

## Step 7: Explore the Dashboard

If you'd rather watch things happen in a browser, use this instead of `flexrouter serve` in Step 4 — it starts the same service and opens the dashboard for you:

```bash
flexrouter dashboard
```

This opens `http://localhost:4891` in your browser. You'll see:
- **Live Telemetry**: Your model's RPM and TPM usage
- **Chat**: Test the router interactively
- **Request Logs**: Every call you just made
- **Account Status**: API key usage and budget tracking

---

## Next Steps

You've got the basics! Here's where to go next:

- **Add more providers?** → Read [Configuration Guide](2-Configuration-Guide.md)
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
The service isn't up. Start it with `flexrouter serve` and leave it running in
its own terminal. The message names the address it tried, so if that address
looks wrong, check the `port` in your settings file.

### "No models available in tier"
Run `flexrouter doctor` — it checks your settings file for problems and tells you plainly what's wrong, rather than a raw error.

---

## Quick Reference: Key Concepts

| Term | Meaning |
|------|---------|
| **Tier** | A named group of models (e.g., `cheap`, `medium`, `high`) |
| **Score** | Priority (1–100) within a tier; higher wins when available |
| **RPM** | Requests per minute limit for a model |
| **TPM** | Tokens per minute limit for a model |
| **state_dir** | Folder where audit logs and health data are stored |
| `flexrouter doctor` | Shows where your settings and keys live, and which key each provider will use |
| `flexrouter keys add/list/rm` | Save, view, or remove a provider's key |
| **The service** | The one background process that does all the routing for your machine |
| `flexrouter serve` | Starts the service (API + dashboard) on port 4891 — nothing routes until it's running |

---

You're ready to route! 🚀
