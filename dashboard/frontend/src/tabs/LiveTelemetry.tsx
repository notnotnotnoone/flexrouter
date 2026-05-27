import { useEffect, useState } from 'react'
import { fetchStatus } from '../api'
import { RateBar } from '../components/RateBar'
import { StatusDot } from '../components/StatusDot'

export function LiveTelemetry() {
  const [status, setStatus] = useState<any>(null)
  const [paused, setPaused] = useState(false)
  const [search, setSearch] = useState('')

  useEffect(() => {
    const load = () => { if (!paused) fetchStatus().then(setStatus) }
    load()
    const id = setInterval(load, 2000)
    return () => clearInterval(id)
  }, [paused])

  const models: [string, any][] = Object.entries(status?.models ?? {})
  const filtered = models.filter(([key]) =>
    key.toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <input
          type="text"
          placeholder="Search models..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-1.5 text-sm bg-white dark:bg-gray-800 w-64"
        />
        <button
          onClick={() => setPaused(p => !p)}
          className="text-xs px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-700"
        >
          {paused ? '▶ Resume' : '⏸ Pause'}
        </button>
        <span className="text-sm text-gray-500">
          Total cost: <b>${(status?.total_cost_usd ?? 0).toFixed(4)}</b>
        </span>
      </div>

      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-gray-500 uppercase border-b border-gray-100 dark:border-gray-800">
            <th className="pb-2 pr-4">Model</th>
            <th className="pb-2 pr-4">RPM</th>
            <th className="pb-2 pr-4">TPM</th>
            <th className="pb-2">Status</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map(([key, info]) => {
            const penalized = !!info.penalty_until
            const dotStatus = penalized ? 'penalized' : 'up'
            return (
              <tr key={key} className="border-b border-gray-50 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800/50">
                <td className="py-2 pr-4 font-mono font-medium">{key}</td>
                <td className="py-2 pr-4">
                  <RateBar current={info.rpm_current ?? 0} limit={60} label={`${info.rpm_current ?? 0}`} />
                </td>
                <td className="py-2 pr-4">
                  <RateBar current={info.tpm_current ?? 0} limit={60000} label={`${((info.tpm_current ?? 0) / 1000).toFixed(0)}k`} />
                </td>
                <td className="py-2">
                  <div className="flex items-center gap-1.5">
                    <StatusDot status={dotStatus} />
                    <span className="text-xs capitalize">{dotStatus}</span>
                  </div>
                </td>
              </tr>
            )
          })}
          {filtered.length === 0 && (
            <tr><td colSpan={4} className="py-8 text-center text-gray-400 text-sm">No data yet. Run router.generate() first.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
