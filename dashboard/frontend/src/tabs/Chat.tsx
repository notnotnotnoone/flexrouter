import { useState, useEffect } from 'react'
import { chatCompletion, fetchConfig } from '../api'

export function Chat() {
  const [messages, setMessages] = useState<{ role: string; content: string }[]>([])
  const [input, setInput] = useState('')
  const [tiers, setTiers] = useState<string[]>(['default'])
  const [tier, setTier] = useState('default')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchConfig().then((cfg: any) => {
      const t = Object.keys(cfg?.tiers ?? {})
      if (t.length) { setTiers(t); setTier(t[0]) }
    }).catch(() => {})
  }, [])

  const send = async () => {
    if (!input.trim() || loading) return
    const userMsg = { role: 'user', content: input }
    setMessages(m => [...m, userMsg])
    setInput('')
    setLoading(true)
    setError(null)
    try {
      const result = await chatCompletion([...messages, userMsg], `auto-${tier}`)
      const reply = result?.choices?.[0]?.message?.content ?? result?.error ?? '(no response)'
      setMessages(m => [...m, { role: 'assistant', content: reply }])
    } catch (e: any) {
      setError(e?.message ?? 'Request failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col h-[60vh]">
      <div className="flex gap-2 mb-3 items-center">
        <span className="text-sm font-medium">Tier:</span>
        {tiers.map(t => (
          <button key={t} onClick={() => setTier(t)}
            className={`px-3 py-1 rounded-lg text-sm font-medium border transition-colors ${
              tier === t ? 'bg-[var(--color-brand)] text-white border-[var(--color-brand)]'
                        : 'border-border text-muted-foreground hover:text-foreground'}`}>
            {t}
          </button>
        ))}
      </div>
      {error && <div className="text-xs text-[var(--color-destructive)] mb-2 px-1">{error}</div>}
      <div className="flex-1 overflow-y-auto border border-border rounded-xl p-3 space-y-3 bg-muted/20">
        {messages.length === 0 && <p className="text-muted-foreground text-sm text-center mt-8">Start a conversation…</p>}
        {messages.map((m, i) => (
          <div key={i} className={`p-3 rounded-lg text-sm font-mono whitespace-pre-wrap border-l-2 bg-card ${
            m.role === 'user' ? 'border-[var(--color-success)]' : 'border-[var(--color-brand)]'}`}>
            <span className="text-xs font-sans text-muted-foreground block mb-1">{m.role}</span>
            {m.content}
          </div>
        ))}
        {loading && <div className="text-xs text-muted-foreground animate-pulse">Thinking…</div>}
      </div>
      <div className="flex gap-2 mt-3">
        <textarea value={input} onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
          placeholder="Type a message (Enter to send)"
          className="flex-1 border border-border rounded-xl px-3 py-2 text-sm resize-none bg-card min-h-[60px]" />
        <button onClick={send} disabled={loading}
          className="px-4 bg-[var(--color-brand)] text-white rounded-xl font-semibold text-sm disabled:opacity-50">
          Send
        </button>
      </div>
    </div>
  )
}
