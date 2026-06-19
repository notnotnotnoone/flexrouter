export function colorFor(value: number, max: number): string {
  const intensity = max > 0 ? value / max : 0
  return `rgba(59, 130, 246, ${intensity.toFixed(2)})`
}
