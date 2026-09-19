export function Setup() {
  return (
    <div className="max-w-lg space-y-6">
      <h2 className="font-semibold text-lg">Setup</h2>
      <p className="text-gray-500 text-sm">
        flexrouter keeps your settings and your keys together in one place on this
        computer. You do not have to create anything in the folder you happen to be
        working in — there is only ever the one place, and every project you run uses
        it. To see where it is, run:
      </p>
      <pre className="bg-gray-900 text-green-300 p-4 rounded-xl text-xs">flexrouter doctor</pre>
      <p className="text-sm text-gray-500">
        That also tells you which key each provider will use, and points out anything
        that looks wrong.
      </p>

      <h3 className="font-semibold text-sm">Adding a key</h3>
      <p className="text-sm text-gray-500">
        Keys are never typed into the settings file. Add one per provider and
        flexrouter stores it for you:
      </p>
      <pre className="bg-gray-900 text-green-300 p-4 rounded-xl text-xs">flexrouter keys add groq</pre>
      <p className="text-sm text-gray-500">
        You will be asked for the key itself, and it will not appear on screen as you
        type. Afterwards, only the last four characters of it are ever shown again —
        here, on the command line, or in a copy of your settings you share with
        someone.
      </p>

      <h3 className="font-semibold text-sm">Choosing which models to use</h3>
      <p className="text-sm text-gray-500">
        The settings file groups models into <strong>buckets</strong>. A bucket is
        simply a name you ask for — flexrouter then picks the best model in that
        bucket that is free to answer right now. Run{" "}
        <code className="bg-gray-100 dark:bg-gray-800 px-1 rounded">flexrouter doctor</code>{" "}
        to find the file, then open it and add to it:
      </p>
      <pre className="bg-gray-900 text-green-300 p-4 rounded-xl text-xs overflow-x-auto">{`buckets:
  fast:
    - provider: groq
      model: llama-3.1-8b-instant
      score: 85
      rpm: 60
      tpm: 60000
      context_window: 131072

providers:
  groq:
    base_url: https://api.groq.com/openai/v1

settings:
  retry_policy: balanced`}</pre>
      <p className="text-sm text-gray-500">
        Notice that the provider has no key in it. That is on purpose, and it is what
        makes this file safe to send to someone else.
      </p>

      <h3 className="font-semibold text-sm">Using it</h3>
      <pre className="bg-gray-900 text-green-300 p-4 rounded-xl text-xs">{`from flexrouter import FlexRouter
router = FlexRouter()
response = router.generate(
    messages=[{"role": "user", "content": "hello"}],
    tier="fast",
)
print(response["choices"][0]["message"]["content"])`}</pre>
      <p className="text-sm text-gray-500">
        Changes to the settings file are picked up while flexrouter is running, so
        there is nothing to restart.
      </p>
    </div>
  )
}
