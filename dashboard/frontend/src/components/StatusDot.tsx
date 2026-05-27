type Status = 'up' | 'limited' | 'penalized' | 'down'
const COLORS: Record<Status, string> = {
  up: 'bg-green-500',
  limited: 'bg-amber-400',
  penalized: 'bg-red-500',
  down: 'bg-gray-400',
}
export function StatusDot({ status }: { status: Status }) {
  return <span className={`inline-block w-2 h-2 rounded-full ${COLORS[status]}`} />
}
