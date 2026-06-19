export interface DiagEntry {
  id: number
  ts: string
  endpoint: string
  status: number
  message: string
}

const MAX = 200
let entries: DiagEntry[] = []
let nextId = 1
const listeners = new Set<() => void>()

export function recordDiagnostic(e: { endpoint: string; status: number; message: string }): void {
  entries = [...entries, { id: nextId++, ts: new Date().toISOString(), ...e }].slice(-MAX)
  listeners.forEach(fn => fn())
}

export function getDiagnostics(): DiagEntry[] {
  return entries
}

export function clearDiagnostics(): void {
  entries = []
  listeners.forEach(fn => fn())
}

export function subscribeDiagnostics(fn: () => void): () => void {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}
