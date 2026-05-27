import { useState } from 'react'
import { chatCompletion } from '../api'

export function Chat() {
  const [messages, setMessages] = useState<{ role: string; content: string }[]>([])
  const [input, setInput] = useState('')
  const [tier, setTier] = useState('low')
  const [loading, setLoading] = useState(false)

  const send = async () => {
    if (!input.trim() || loading) return
    const userMsg = { role: 'user', content: input }
    setMessages(m => [...m, userMsg])
    setInput('')
    setLoading(true)
    try {
      const result = await chatCompletion([...messages, userMsg], `auto-${tier}`)
      const reply = result?.choices?.[0]?.message?.content ?? '(no response)'
      setMessages(m => [...m, { role: 'assistant', content: reply }])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col h-[60vh]">
      <div className="flex gap-2 mb-3 items-center">
        <span className="text-sm font-medium">Tier:</span>
        {['low', 'medium', 'high'].map(t => (
          <button
            key={t}
            onClick={() => setTier(t)}
            className={`px-3 py-1 rounded-lg text-sm font-medium border ${tier === t ? 'bg-blue-500 text-white border-blue-500' : 'border-gray-200 dark:border-gray-700'}`}
          >
            {t}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto border border-gray-100 dark:border-gray-800 rounded-xl p-3 space-y-3 bg-gray-50 dark:bg-gray-900">
        {messages.length === 0 && <p className="text-gray-400 text-sm text-center mt-8">Start a conversation...</p>}
        {messages.map((m, i) => (
          <div key={i} className={`p-3 rounded-lg text-sm font-mono whitespace-pre-wrap border-l-2 ${m.role === 'user' ? 'border-green-400 bg-white dark:bg-gray-800' : 'border-blue-400 bg-white dark:bg-gray-800'}`}>
            <span className="text-xs font-sans text-gray-400 block mb-1">{m.role}</span>
            {m.content}
          </div>
        ))}
        {loading && <div className="text-xs text-gray-400 animate-pulse">Thinking...</div>}
      </div>
      <div className="flex gap-2 mt-3">
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
          placeholder="Type a message (Enter to send)"
          className="flex-1 border border-gray-200 dark:border-gray-700 rounded-xl px-3 py-2 text-sm resize-none bg-white dark:bg-gray-800 min-h-[60px]"
        />
        <button onClick={send} disabled={loading} className="px-4 bg-blue-500 text-white rounded-xl font-semibold text-sm disabled:opacity-50">Send</button>
      </div>
    </div>
  )
}
