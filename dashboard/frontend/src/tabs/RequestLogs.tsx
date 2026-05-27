import { useEffect, useState } from 'react'
import { fetchLogs } from '../api'

export function RequestLogs() {
  const [logs, setLogs] = useState<any[]>([])
  const [paused, setPaused] = useState(false)

  useEffect(() => {
    const load = () => { if (!paused) fetchLogs(50).then(setLogs) }
    load()
    const id = setInterval(load, 2000)
    return () => clearInterval(id)
  }, [paused])

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <h2 className="font-semibold">Recent Requests</h2>
        <button onClick={() => setPaused(p => !p)} className="text-xs px-3 py-1 rounded border border-gray-200 dark:border-gray-700">
          {paused ? '▶ Resume' : '⏸ Pause'}
        </button>
      </div>
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="text-left text-gray-400 border-b border-gray-100 dark:border-gray-800">
            {['Time', 'Tier', 'Model', 'In', 'Out', 'Cost', 'ms', 'Status'].map(h => (
              <th key={h} className="pb-2 pr-3 font-medium">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {[...logs].reverse().map((row, i) => (
            <tr key={i} className="border-b border-gray-50 dark:border-gray-800">
              <td className="py-1.5 pr-3 text-gray-400">{row.timestamp?.slice(11, 19)}</td>
              <td className="py-1.5 pr-3"><span className="px-1.5 py-0.5 bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300 rounded">{row.tier}</span></td>
              <td className="py-1.5 pr-3">{row.provider}/{row.model}</td>
              <td className="py-1.5 pr-3">{row.prompt_tokens}</td>
              <td className="py-1.5 pr-3">{row.completion_tokens}</td>
              <td className="py-1.5 pr-3">${Number(row.cost_usd).toFixed(5)}</td>
              <td className="py-1.5 pr-3">{row.latency_ms}</td>
              <td className="py-1.5"><span className={row.status === 'ok' ? 'text-green-500' : 'text-red-500'}>{row.status}</span></td>
            </tr>
          ))}
          {logs.length === 0 && <tr><td colSpan={8} className="py-8 text-center text-gray-400">No requests yet.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}
