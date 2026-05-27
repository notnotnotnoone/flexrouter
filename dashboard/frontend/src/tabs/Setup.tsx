export function Setup() {
  return (
    <div className="max-w-lg space-y-6">
      <h2 className="font-semibold text-lg">Setup</h2>
      <p className="text-gray-500 text-sm">Create a <code className="bg-gray-100 dark:bg-gray-800 px-1 rounded">flexrouter.yaml</code> in your project directory:</p>
      <pre className="bg-gray-900 text-green-300 p-4 rounded-xl text-xs overflow-x-auto">{`tiers:
  low:
    - provider: groq
      model: llama-3.1-8b-instant
      score: 85
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
  retry_policy: balanced`}</pre>
      <p className="text-sm text-gray-500">Then set your environment variables and run:</p>
      <pre className="bg-gray-900 text-green-300 p-4 rounded-xl text-xs">{`from flexrouter import FlexRouter
router = FlexRouter()
response = router.generate(
    messages=[{"role": "user", "content": "hello"}],
    tier="low",
)
print(response["choices"][0]["message"]["content"])`}</pre>
    </div>
  )
}
