import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext'
import { getClients, getMatters, getRecentMatters } from '../../lib/api'
import type { Client, Matter } from '../../types'

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="card p-5">
      <p className="text-xs text-text-secondary uppercase tracking-widest font-semibold mb-1">{label}</p>
      <p className="font-display text-3xl text-navy">{value}</p>
      {sub && <p className="text-xs text-text-secondary mt-1">{sub}</p>}
    </div>
  )
}

const STATUS_COLOR: Record<string, string> = {
  active:          'bg-green-100 text-green-800',
  intake:          'bg-blue-100 text-blue-800',
  conflict_review: 'bg-amber-100 text-amber-800',
  closed:          'bg-gray-100 text-gray-600',
  archived:        'bg-gray-100 text-gray-500',
}

const MAX_RESULTS = 25

function clientName(client: Client | undefined): string {
  return client ? `${client.name.first_name} ${client.name.last_name}` : ''
}

/** Every word typed must appear in the client's name, the matter's names, the cause number or the billing id. */
function matches(matter: Matter, client: Client | undefined, words: string[]): boolean {
  const haystack = [
    clientName(client), matter.matter_name, matter.short_name,
    matter.matter_number, matter.billing_id,
  ].filter(Boolean).join(' ').toLowerCase()
  return words.every(w => haystack.includes(w))
}

function MatterRows({ matters, clients }: { matters: Matter[]; clients: Map<number, Client> }) {
  return (
    <ul className="divide-y divide-border">
      {matters.map(m => (
        <li key={m.id}>
          <Link to={`/app/matters/${m.id}`}
            className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-5 py-3 hover:bg-off-white/60 transition-colors">
            <span className="font-medium text-navy">{m.short_name ?? m.matter_name}</span>
            <span className="text-sm text-text-secondary">{clientName(clients.get(m.client_id))}</span>
            {m.matter_number && <span className="text-xs text-text-secondary">Cause No. {m.matter_number}</span>}
            {m.billing_id && <span className="text-xs text-text-secondary">Billing {m.billing_id}</span>}
            <span className={`ml-auto text-xs rounded-full px-2.5 py-1 font-medium capitalize ${STATUS_COLOR[m.status] ?? 'bg-gray-100 text-gray-600'}`}>
              {m.status.replace(/_/g, ' ')}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  )
}

export default function DashboardPage() {
  const { profile } = useAuth()
  const [matters, setMatters] = useState<Matter[]>([])
  const [clients, setClients] = useState<Map<number, Client>>(new Map())
  const [recent, setRecent] = useState<Matter[]>([])
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([getMatters(), getClients(), getRecentMatters()])
      .then(([ms, cs, rs]) => {
        setMatters(ms)
        setClients(new Map(cs.map(c => [c.id, c])))
        setRecent(rs)
      })
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load matters'))
      .finally(() => setLoading(false))
  }, [])

  const open    = matters.filter(m => m.status === 'active' || m.status === 'intake' || m.status === 'conflict_review').length
  const proBono = matters.filter(m => m.is_pro_bono).length

  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean)
  const results = useMemo(
    () => words.length
      ? matters.filter(m => matches(m, clients.get(m.client_id), words))
      : [],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [matters, clients, query],
  )

  return (
    <div className="px-6 py-8 max-w-6xl mx-auto">
      <div className="mb-8">
        <h1 className="font-display text-3xl text-navy">Dashboard</h1>
        <p className="text-text-secondary mt-1">
          Welcome back{profile ? ` · ${profile.role}` : ''}.
        </p>
      </div>

      {/* Stat strip */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        <StatCard label="Open matters"   value={loading ? '—' : open} />
        <StatCard label="Total matters"  value={loading ? '—' : matters.length} />
        <StatCard label="Pro bono"       value={loading ? '—' : proBono} />
        <StatCard label="Your role"      value={profile?.role ?? '—'} />
      </div>

      {/* Find a matter */}
      <div className="card overflow-hidden mb-6">
        <div className="px-5 py-4 border-b border-border">
          <label className="label" htmlFor="matter-search">Find a matter</label>
          <input id="matter-search" className="input mt-1" autoFocus value={query}
            placeholder="Client name, matter name, cause number or billing ID"
            onChange={e => setQuery(e.target.value)} />
        </div>
        {words.length > 0 && (
          results.length === 0
            ? <p className="px-5 py-6 text-sm text-text-secondary">No matter matches that.</p>
            : <>
                <MatterRows matters={results.slice(0, MAX_RESULTS)} clients={clients} />
                {results.length > MAX_RESULTS && (
                  <p className="px-5 py-3 text-xs text-text-secondary border-t border-border">
                    Showing {MAX_RESULTS} of {results.length}. Add another word to narrow it.
                  </p>
                )}
              </>
        )}
      </div>

      {/* Recently opened */}
      <div className="card overflow-hidden">
        <div className="px-5 py-4 border-b border-border flex items-center justify-between">
          <h2 className="font-semibold text-navy">Recently opened</h2>
          <Link to="/app/matters" className="text-sm text-navy/70 hover:text-navy transition-colors">
            All matters →
          </Link>
        </div>

        {loading && <div className="px-5 py-10 text-center text-text-secondary text-sm">Loading…</div>}
        {error && <div className="px-5 py-10 text-center text-red-600 text-sm">{error}</div>}

        {!loading && !error && recent.length === 0 && (
          <div className="px-5 py-10 text-center text-text-secondary text-sm">
            Matters you open will appear here. Search above, or{' '}
            <Link to="/app/matters" className="text-navy underline">see all matters</Link>.
          </div>
        )}

        {!loading && !error && recent.length > 0 && <MatterRows matters={recent} clients={clients} />}
      </div>
    </div>
  )
}
