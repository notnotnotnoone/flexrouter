import { useEffect, useRef } from 'react'
import { KpiCard } from './KpiCard'

export function TopModelCard({ label, active }: { label: string; active: boolean }) {
  const ref = useRef<SVGSVGElement>(null)
  useEffect(() => {
    const svg = ref.current; if (!svg) return
    if (svg.children.length === 0) {
      svg.setAttribute('viewBox', '0 0 240 102')
      const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polygon')
      const pts: string[] = []
      for (let i = 0; i < 6; i++) {
        const angle = (Math.PI / 3) * i
        pts.push(`${120 + 40 * Math.cos(angle)},${50 + 40 * Math.sin(angle)}`)
      }
      poly.setAttribute('points', pts.join(' ')); poly.setAttribute('class', 'current-core')
      poly.style.transformOrigin = '120px 50px'; svg.appendChild(poly)
      const core = document.createElementNS('http://www.w3.org/2000/svg', 'circle')
      core.setAttribute('cx', '120'); core.setAttribute('cy', '50'); core.setAttribute('r', '16')
      core.setAttribute('class', 'current-pulse'); svg.appendChild(core)
    }
    Array.from(svg.children).forEach(el => {
      (el as HTMLElement).style.opacity = active ? '' : '0'
    })
  }, [active])
  return <KpiCard label="Top Model" value={label}><svg ref={ref} className="kpi-bg-svg" preserveAspectRatio="xMidYMid slice" /></KpiCard>
}
