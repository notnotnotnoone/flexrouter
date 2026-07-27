# Getting Started with flexrouter

**Goal:** Install flexrouter, configure it for your first LLM provider, and make your first routing call.

**Time:** 10 minutes

---

## What You'll Build

By the end of this tutorial, you'll have:
- ✅ flexrouter installed
- ✅ A `flexrouter.yaml` config file with one provider
- ✅ A working Python script that routes a request to the cheapest available model

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
4. Store it in an environment variable:

```bash
# macOS / Linux
export GROQ_API_KEY="gsk_..."

# Windows PowerShell
$env:GROQ_API_KEY = "gsk_..."
```

---

## Step 3: Create Your Config File

Create a file called `flexrouter.yaml` in your project root:

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
    api_keys:
      - env: GROQ_API_KEY

settings:
  state_dir: .flexrouter/
  window_seconds: 60
  penalty_base_seconds: 30
  dashboard_port: 7352
```

**What this means:**
- **tiers**: Define routing tiers. Here we have one tier called `cheap`.
- **models**: List models available in this tier. Score (1–100) determines preference.
- **providers**: Define how to reach each provider (base URL and auth).
- **settings**: Store state, configure windows, and port for the dashboard.

---

## Step 4: Make Your First Call

Create a file `hello_flexrouter.py`:

```python
from flexrouter import FlexRouter

# Initialize the router
# flexrouter.yaml will be auto-discovered in the current directory
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
```

Run it:

```bash
python hello_flexrouter.py
```

You should see the model's response and token count.

---

## Step 5: Explore the Dashboard

While your app is running, start the dashboard in a new terminal:

```bash
flexrouter dashboard
```

This opens `http://localhost:7352` in your browser. You'll see:
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

### "flexrouter.yaml not found"
Make sure the file is in your current working directory. Or pass an explicit path:
```python
router = FlexRouter("path/to/flexrouter.yaml")
```

### "API key rejected"
Double-check that your environment variable is set:
```bash
# macOS / Linux
echo $GROQ_API_KEY

# Windows PowerShell
$env:GROQ_API_KEY
```

### "No models available in tier"
Check your `flexrouter.yaml` syntax using the setup wizard:
```bash
flexrouter init
```

This will validate your config and help you fix issues.

---

## Quick Reference: Key Concepts

| Term | Meaning |
|------|---------|
| **Tier** | A named group of models (e.g., `cheap`, `medium`, `high`) |
| **Score** | Priority (1–100) within a tier; higher wins when available |
| **RPM** | Requests per minute limit for a model |
| **TPM** | Tokens per minute limit for a model |
| **state_dir** | Folder where audit logs and health data are stored |

---

You're ready to route! 🚀
