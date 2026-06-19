export interface Stats {
  totals: { requests: number; this_hour: number; peak_rpm: number }
  hourly: { hour: string; requests: number }[]
  latency: {
    per_model: { model: string; p50: number; p95: number; count: number }[]
    histogram: { bucket_ms: string; count: number }[]
  }
  distribution: {
    by_provider: { provider: string; requests: number }[]
    top_models: { model: string; requests: number }[]
    diversity: number
  }
  errors: {
    rate_by_hour: { hour: string; total: number; errors: number; rate: number }[]
    by_type: { status: string; count: number }[]
    per_model: { model: string; requests: number; errors: number; rate: number }[]
  }
  tiers: { by_tier: { tier: string; requests: number }[] }
}

export interface UptimeModel {
  model: string
  uptime_24h: number | null
  uptime_7d: number | null
  uptime_30d: number | null
  segments: { start: string; end: string; state: 'up' | 'down' | 'nodata' }[]
}
export interface Uptime {
  models: UptimeModel[]
  incidents: { start: string; end: string | null; provider: string; model: string; event_type: string; duration_seconds: number | null }[]
  system: { uptime_24h: number | null }
  providers: { provider: string; uptime_24h: number | null }[]
}
