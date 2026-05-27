import { useEffect, useState } from 'react'
import { fetchConfig, postConfig } from '../api'

export function Settings() {
  const [cfg, setCfg] = useState<string>('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    fetchConfig().then(c => setCfg(JSON.stringify(c, null, 2)))
  }, [])

  const save = async () => {
    try {
      await postConfig(JSON.parse(cfg))
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch {
      alert('Invalid JSON')
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h2 className="font-semibold">Configuration</h2>
        <button onClick={save} className="px-4 py-1.5 bg-blue-500 text-white rounded-lg text-sm font-medium">
          {saved ? '✓ Saved' : 'Save'}
        </button>
      </div>
      <textarea
        value={cfg}
        onChange={e => setCfg(e.target.value)}
        className="w-full h-96 font-mono text-xs border border-gray-200 dark:border-gray-700 rounded-xl p-3 bg-white dark:bg-gray-900"
      />
    </div>
  )
}
