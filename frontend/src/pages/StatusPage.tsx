import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { getServiceStatus } from '../lib/api'
import type { ComponentState, ServiceStatus, StatusResult } from '../types'

/**
 * The public service-status page (/status).
 *
 * It replaced a footer link to /api/health, which answered `{"message":"ok"}`
 * whenever the API process was running — with the database down or the worker
 * dead, it still said ok. The checks behind this page are in
 * app/services/status_service.py; this page only presents them, with three
 * rules of its own:
 *
 * - **Nothing answering IS the status.** When /api/status cannot be reached
 *   the page says the servers are not answering, in red, rather than showing an
 *   error or the last green it saw. The last report is kept below, greyed and
 *   dated, because "what was it doing before it stopped?" is the next question.
 * - **Your connection is not our outage.** If the browser reports itself
 *   offline, the page says so instead of blaming Cyclone.
 * - **Plain sentences, from the server.** Each row's wording says what the
 *   state means for the person reading — "uploads are kept and wait in line" —
 *   so the page is useful to a lawyer at 7 a.m., not only to an engineer.
 */

const REFRESH_MS = 30_000

const STATE: Record<ComponentState, { label: string; dot: string; text: string }> = {
  operational: { label: 'Operational', dot: 'bg-emerald-500', text: 'text-emerald-800' },
  idle:        { label: 'Idle',        dot: 'bg-slate-300',   text: 'text-slate-600' },
  degraded:    { label: 'Degraded',    dot: 'bg-amber-500',   text: 'text-amber-800' },
  down:        { label: 'Down',        dot: 'bg-red-600',     text: 'text-red-700' },
  unknown:     { label: 'Unconfirmed', dot: 'bg-slate-400',   text: 'text-slate-700' },
}

type Banner = { tone: 'ok' | 'warn' | 'bad' | 'offline'; title: string; body: string }

function bannerFor(result: StatusResult | null): Banner | null {
  if (!result) return null
  if (result.kind === 'unreachable') {
    if (result.offline) {
      return {
        tone: 'offline',
        title: 'Your device appears to be offline.',
        body: 'This page cannot reach the internet, so it cannot tell how Cyclone is doing. '
          + 'Check your connection; the page will try again on its own.',
      }
    }
    const gateway = result.httpStatus !== null && [502, 503, 504].includes(result.httpStatus)
    return {
      tone: 'bad',
      title: 'Cyclone’s servers are not answering.',
      body: gateway
        ? 'The website loaded, but the service behind it is not responding. This is usually brief — '
          + 'during an update it lasts a minute or two. This page will check again automatically.'
        : 'Nothing answered when this page asked. If it does not clear within a few minutes, '
          + 'it is not just you — please let the office know.',
    }
  }
  const { status } = result
  const named = (states: ComponentState[]) =>
    status.components.filter(c => states.includes(c.state)).map(c => c.name.toLowerCase())
  const list = (items: string[]) =>
    items.length <= 1 ? items.join('') : `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`

  switch (status.overall) {
    case 'operational':
      return {
        tone: 'ok',
        title: 'Everything is running normally.',
        body: 'Cyclone is up, your case records are reachable, and uploads are being read.',
      }
    case 'degraded':
      return {
        tone: 'warn',
        title: 'Cyclone is working, with some delays.',
        body: `Affected: ${list(named(['degraded']))}. Details are below.`,
      }
    case 'down':
      return {
        tone: 'bad',
        title: 'Part of Cyclone is not working.',
        body: `Not working: ${list(named(['down']))}. What that means for you is below.`,
      }
    default:
      return {
        tone: 'warn',
        title: 'Cyclone is running, but not everything could be confirmed.',
        body: `Could not check: ${list(named(['unknown']))}. That is a blind spot, not necessarily `
          + 'a problem — the rest is below.',
      }
  }
}

const BANNER_STYLE: Record<Banner['tone'], { box: string; icon: string; title: string }> = {
  ok:      { box: 'border-emerald-200 bg-emerald-50', icon: 'bg-emerald-500', title: 'text-emerald-900' },
  warn:    { box: 'border-amber-200 bg-amber-50',     icon: 'bg-amber-500',   title: 'text-amber-900' },
  bad:     { box: 'border-red-200 bg-red-50',         icon: 'bg-red-600',     title: 'text-red-900' },
  offline: { box: 'border-slate-200 bg-slate-50',     icon: 'bg-slate-500',   title: 'text-slate-900' },
}

function BannerIcon({ tone }: { tone: Banner['tone'] }) {
  const d = tone === 'ok'
    ? 'M5 12.5l4.5 4.5L19 7.5'
    : tone === 'bad'
      ? 'M7 7l10 10M17 7L7 17'
      : 'M12 7.5v5.5M12 16.5v.01'
  return (
    <span className={`shrink-0 w-11 h-11 rounded-full flex items-center justify-center text-white
      ${BANNER_STYLE[tone].icon}`} aria-hidden="true">
      <svg viewBox="0 0 24 24" className="w-6 h-6" fill="none" stroke="currentColor"
        strokeWidth={2.6} strokeLinecap="round" strokeLinejoin="round"><path d={d} /></svg>
    </span>
  )
}

function ago(iso: string, now: number): string {
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000))
  if (seconds < 10) return 'just now'
  if (seconds < 60) return `${seconds} seconds ago`
  const minutes = Math.round(seconds / 60)
  return minutes === 1 ? 'a minute ago' : `${minutes} minutes ago`
}

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

export default function StatusPage() {
  const [result, setResult] = useState<StatusResult | null>(null)
  const [lastReport, setLastReport] = useState<ServiceStatus | null>(null)
  const [checking, setChecking] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const inFlight = useRef(false)

  const check = useCallback(async () => {
    if (inFlight.current) return
    inFlight.current = true
    setChecking(true)
    try {
      const next = await getServiceStatus()
      setResult(next)
      if (next.kind === 'report') setLastReport(next.status)
    } finally {
      inFlight.current = false
      setChecking(false)
    }
  }, [])

  useEffect(() => {
    void check()
    const poll = window.setInterval(() => void check(), REFRESH_MS)
    const tick = window.setInterval(() => setNow(Date.now()), 5_000)
    // Coming back to a tab left open overnight should not show last night.
    const onFocus = () => void check()
    window.addEventListener('focus', onFocus)
    window.addEventListener('online', onFocus)
    return () => {
      window.clearInterval(poll); window.clearInterval(tick)
      window.removeEventListener('focus', onFocus); window.removeEventListener('online', onFocus)
    }
  }, [check])

  const banner = bannerFor(result)
  const unreachable = result?.kind === 'unreachable'
  const shown = result?.kind === 'report' ? result.status : lastReport

  return (
    <div className="min-h-screen bg-off-white font-sans text-text-primary">
      <header className="bg-white border-b border-border">
        <div className="max-w-3xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between gap-4">
          <Link to="/" className="flex items-center gap-2.5" aria-label="Cyclone home">
            <img src="/brand/cyclone-mark.png" alt="" className="h-8 w-auto" />
            <span className="font-display text-2xl text-brand-ink tracking-tight">Cyclone</span>
            <span className="hidden sm:inline text-sm text-text-secondary mt-1">Service status</span>
          </Link>
          <Link to="/login" className="btn-primary bg-brand-ink hover:bg-navy">Sign in</Link>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-4 sm:px-6 py-10 sm:py-14">
        <h1 className="sm:hidden font-display text-3xl text-brand-ink mb-6">Service status</h1>

        {/* The headline. aria-live so a screen reader hears a change of state. */}
        <div role="status" aria-live="polite">
          {banner ? (
            <div className={`rounded-2xl border p-5 sm:p-6 flex gap-4 ${BANNER_STYLE[banner.tone].box}`}>
              <BannerIcon tone={banner.tone} />
              <div className="min-w-0">
                <h2 className={`font-display text-2xl sm:text-3xl leading-snug ${BANNER_STYLE[banner.tone].title}`}>
                  {banner.title}
                </h2>
                <p className="mt-2 text-text-primary leading-relaxed">{banner.body}</p>
              </div>
            </div>
          ) : (
            <div className="rounded-2xl border border-border bg-white p-6 flex items-center gap-3 text-text-secondary">
              <span className="animate-spin w-5 h-5 border-2 border-navy/20 border-t-navy rounded-full" />
              Checking Cyclone…
            </div>
          )}
        </div>

        {/* When nothing answered, the last report is still worth reading — but
            it must not pass for a current one. */}
        {shown && (
          <section className={`mt-8 ${unreachable ? 'opacity-60' : ''}`} aria-label="Components">
            {unreachable && (
              <p className="text-sm text-text-secondary mb-3">
                Last report received at {clock(shown.checked_at)}, before the servers stopped answering:
              </p>
            )}
            <ul className="rounded-2xl border border-border bg-white divide-y divide-border overflow-hidden">
              {shown.components.map(c => {
                const s = STATE[c.state]
                return (
                  <li key={c.key} className="p-4 sm:p-5 flex gap-4">
                    <span className={`mt-1.5 shrink-0 w-3 h-3 rounded-full ${s.dot} ${
                      c.state === 'down' && !unreachable ? 'animate-pulse' : ''}`} aria-hidden="true" />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                        <h3 className="font-semibold text-brand-ink">{c.name}</h3>
                        <span className={`text-sm font-medium ${s.text}`}>{s.label}</span>
                      </div>
                      <p className="text-sm text-text-secondary mt-1 leading-relaxed">{c.summary}</p>
                    </div>
                  </li>
                )
              })}
            </ul>
          </section>
        )}

        <div className="mt-5 flex flex-wrap items-center justify-between gap-3 text-sm text-text-secondary">
          <span>
            {result?.kind === 'report'
              ? <>Checked {ago(result.status.checked_at, now)}. Checks again every 30 seconds.</>
              : result
                ? <>Tried {ago(new Date(now).toISOString(), now)}. Trying again every 30 seconds.</>
                : null}
          </span>
          <button type="button" onClick={() => void check()} disabled={checking}
            className="inline-flex items-center gap-2 text-navy underline disabled:no-underline disabled:opacity-60">
            {checking && <span className="animate-spin w-3.5 h-3.5 border-2 border-navy/20 border-t-navy rounded-full" />}
            {checking ? 'Checking…' : 'Check now'}
          </button>
        </div>

        <section className="mt-12 rounded-2xl border border-border bg-white p-5 sm:p-6">
          <h2 className="font-semibold text-brand-ink">What these mean</h2>
          <dl className="mt-3 grid sm:grid-cols-2 gap-x-8 gap-y-3 text-sm">
            {(['operational', 'degraded', 'down', 'idle', 'unknown'] as ComponentState[]).map(k => (
              <div key={k} className="flex gap-3">
                <span className={`mt-1.5 shrink-0 w-2.5 h-2.5 rounded-full ${STATE[k].dot}`} aria-hidden="true" />
                <div>
                  <dt className="font-medium text-brand-ink">{STATE[k].label}</dt>
                  <dd className="text-text-secondary">{{
                    operational: 'Working as it should.',
                    degraded: 'Working, but slower than usual or partly failing over to backups.',
                    down: 'Not working. The row says what that means for you.',
                    idle: 'Healthy, with nothing happening to report on.',
                    unknown: 'Could not be checked just now. A blind spot, not necessarily a problem.',
                  }[k]}</dd>
                </div>
              </div>
            ))}
          </dl>
        </section>

        <p className="mt-8 text-xs text-text-secondary text-center">
          {shown ? <>Version {shown.version} · answered by server {shown.server} · </> : null}
          <Link to="/" className="underline hover:text-brand-ink">Back to Cyclone</Link>
        </p>
      </main>
    </div>
  )
}
