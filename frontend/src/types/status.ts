/** Mirrors app/schemas/status.py — the public service-status response. */

/** `idle` = healthy with nothing to report; `unknown` = could not be checked. */
export type ComponentState = 'operational' | 'degraded' | 'down' | 'idle' | 'unknown'

export interface StatusComponent {
  key: string
  name: string
  state: ComponentState
  summary: string
  latency_ms?: number | null
}

export interface ServiceStatus {
  overall: 'operational' | 'degraded' | 'down' | 'unknown'
  checked_at: string
  version: string
  server: string
  components: StatusComponent[]
}

/**
 * What the status page learned. `unreachable` is its own outcome, distinct from
 * a report saying things are down: when the API cannot answer at all there is
 * no report to read, and the page must say THAT rather than show stale green.
 */
export type StatusResult =
  | { kind: 'report'; status: ServiceStatus }
  | { kind: 'unreachable'; httpStatus: number | null; offline: boolean }
