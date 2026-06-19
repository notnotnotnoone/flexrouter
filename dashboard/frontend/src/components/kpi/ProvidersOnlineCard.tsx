import { useEffect, useRef } from 'react'
import { KpiCard } from './KpiCard'

export function ProvidersOnlineCard({ total, online }: { total: number; online: number }) {
  const ref = useRef<SVGSVGElement>(null)
  useEffect(() => {
    const svg = ref.current; if (!svg) return
    const numNodes = Math.min(total, 50), numOnline = Math.min(online, numNodes)
    if (svg.dataset.total !== String(numNodes) || svg.children.length === 0) {
      svg.setAttribute('viewBox', '0 0 240 102'); svg.innerHTML = ''; svg.dataset.total = String(numNodes)
      let seed = 123; const rand = () => { seed = (seed * 9301 + 49297) % 233280; return seed / 233280 }
      const hubX = 120, hubY = 90
      for (let i = 0; i < numNodes; i++) {
        const x = 20 + rand() * 200, y = 10 + rand() * 55
        const line = document.createElementNS('http://www.w3.org/2000/svg', 'line')
        line.setAttribute('x1', String(x)); line.setAttribute('y1', String(y))
        line.setAttribute('x2', String(hubX)); line.setAttribute('y2', String(hubY))
        line.setAttribute('class', 'net-link'); (line as any).dataset.idx = i; svg.appendChild(line)
        const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle')
        circle.setAttribute('cx', String(x)); circle.setAttribute('cy', String(y)); circle.setAttribute('r', '2')
        circle.setAttribute('class', 'net-node-dim'); (circle as any).dataset.idx = i
        circle.style.transformOrigin = `${x}px ${y}px`; circle.style.animationDelay = `${rand() * 2}s`
        svg.appendChild(circle)
      }
      const hub = document.createElementNS('http://www.w3.org/2000/svg', 'circle')
      hub.setAttribute('cx', String(hubX)); hub.setAttribute('cy', String(hubY))
      hub.setAttribute('r', '4'); hub.setAttribute('class', 'net-hub'); svg.appendChild(hub)
    }
    const nodes = Array.from(svg.querySelectorAll('circle:not(.net-hub)'))
    const links = Array.from(svg.querySelectorAll('line'))
    nodes.forEach(node => {
      const isOnline = Number((node as any).dataset.idx) < numOnline
      node.setAttribute('class', isOnline ? 'net-node-bright' : 'net-node-dim')
      node.setAttribute('r', isOnline ? '3' : '2')
    })
    links.forEach(link => {
      const isOnline = Number((link as any).dataset.idx) < numOnline
      link.setAttribute('class', isOnline ? 'net-link-active' : 'net-link')
    })
  }, [total, online])
  return <KpiCard label="Providers Online" value={online}><svg ref={ref} className="kpi-bg-svg" preserveAspectRatio="xMidYMid slice" /></KpiCard>
}
