import { useEffect, useRef } from 'react'
import { KpiCard } from './KpiCard'

export function ModelsOnlineCard({ total, online }: { total: number; online: number }) {
  const ref = useRef<SVGSVGElement>(null)
  useEffect(() => {
    const svg = ref.current; if (!svg) return
    const numStars = Math.min(total, 100), numBright = Math.min(online, numStars)
    if (svg.dataset.total !== String(numStars) || svg.children.length === 0) {
      svg.setAttribute('viewBox', '0 0 240 102'); svg.innerHTML = ''; svg.dataset.total = String(numStars)
      let seed = 42; const rand = () => { seed = (seed * 9301 + 49297) % 233280; return seed / 233280 }
      const pts = Array.from({ length: numStars }, (_, i) => ({ idx: i, x: 10 + rand() * 220, y: 10 + rand() * 82 }))
      pts.forEach((p, i) => {
        const d = pts.map((q, j) => ({ j, d: Math.hypot(q.x - p.x, q.y - p.y) })).sort((a, b) => a.d - b.d)
        for (let k = 1; k <= 2 && k < d.length; k++) if (d[k].d < 60) {
          const line = document.createElementNS('http://www.w3.org/2000/svg', 'line')
          line.setAttribute('x1', String(p.x)); line.setAttribute('y1', String(p.y))
          line.setAttribute('x2', String(pts[d[k].j].x)); line.setAttribute('y2', String(pts[d[k].j].y))
          line.setAttribute('class', 'constellation-line-hidden'); line.dataset.from = String(i); line.dataset.to = String(d[k].j)
          svg.appendChild(line)
        }
      })
      pts.forEach(p => {
        const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle')
        c.setAttribute('cx', String(p.x)); c.setAttribute('cy', String(p.y)); c.setAttribute('r', '1')
        c.dataset.idx = String(p.idx); c.setAttribute('class', 'star-dim'); svg.appendChild(c)
      })
    }
    const bright = new Set<number>()
    svg.querySelectorAll('circle').forEach(c => {
      const on = Number(c.getAttribute('data-idx')) < numBright
      if (on) bright.add(Number(c.getAttribute('data-idx')))
      c.setAttribute('class', on ? 'star-bright' : 'star-dim'); c.setAttribute('r', on ? '1.5' : '1')
    })
    svg.querySelectorAll('line').forEach(l => {
      const vis = bright.has(Number(l.getAttribute('data-from'))) && bright.has(Number(l.getAttribute('data-to')))
      l.setAttribute('class', vis ? 'constellation-line' : 'constellation-line-hidden')
    })
  }, [total, online])
  return <KpiCard label="Models Online" value={online}><svg ref={ref} className="kpi-bg-svg" preserveAspectRatio="xMidYMid slice" /></KpiCard>
}
