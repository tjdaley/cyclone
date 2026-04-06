import { useEffect, useState, FormEvent } from 'react'
import { getClients, conflictCheck } from '../../lib/api'

interface Client {
  id: number
  first_name: string
  last_name: string
  email: string
  status: string
  phone: string | null
}

interface ConflictHit {
  id: number
  full_name: string
  role: string
  matter_caption: string
}

function fullName(c: Client) {
  return `${c.first_name} ${c.last_name}`
}

const STATUS_COLOR: Record<string, string> = {
  active:   'bg-green-100 text-green-800',
  inactive: 'bg-gray-100 text-gray-600',
  prospect: 'bg-blue-100 text-blue-800',
}

export default function ClientsPage() {
  const [clients, setClients]   = useState<Client[]>([])
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState<string | null>(null)
  const [search, setSearch]     = useState('')

  // Conflict check panel
  const [ccName, setCcName]         = useState('')
  const [ccOpposing, setCcOpposing] = useState('')
  const [checking, setChecking]     = useState(false)
  const [ccHits, setCcHits]         = useState<ConflictHit[] | null>(null)
  const [ccError, setCcError]       = useState<string | null>(null)

  useEffect(() => {
    getClients()
      .then((data: unknown) => setClients(data as Client[]))
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [])

  const visible = clients.filter(c =>
    !search || fullName(c).toLowerCase().includes(search.toLowerCase()) ||
    c.email.toLowerCase().includes(search.toLowerCase())
  )

  async function handleConflictCheck(e: FormEvent) {
    e.preventDefault()
    if (!ccName.trim()) return
    setChecking(true)
    setCcHits(null)
    setCcError(null)
    const opposingNames = ccOpposing.split(',').map(s => s.trim()).filter(Boolean)
    try {
      const result = await conflictCheck(ccName.trim(), opposingNames)
      setCcHits((result as { hits: ConflictHit[] }).hits ?? [])
    } catch (err) {
      setCcError(err instanceof Error ? err.message : 'Check failed')
    } finally {
      setChecking(false)
    }
  }

  return (
    <div className="px-6 py-8 max-w-5xl mx-auto">
      <div className="mb-8">
        <h1 className="font-display text-3xl text-navy">Clients</h1>
        <p className="text-text-secondary mt-1">{clients.length} total</p>
      </div>

      {/* Conflict check */}
      <div className="card p-5 mb-6">
        <h2 className="font-semibold text-navy mb-3">Conflict of interest check</h2>
        <form onSubmit={handleConflictCheck} className="space-y-3">
          <div>
            <label className="label">Prospective client name</label>
            <input
              className="input mt-1"
              placeholder="Jane Smith"
              value={ccName}
              onChange={e => setCcName(e.target.value)}
            />
          </div>
          <div>
            <label className="label">Opposing party names (comma-separated, optional)</label>
            <input
              className="input mt-1"
              placeholder="John Smith, ABC Corp"
              value={ccOpposing}
              onChange={e => setCcOpposing(e.target.value)}
            />
          </div>
          <button type="submit" className="btn-primary" disabled={checking || !ccName.trim()}>
            {checking ? 'Checking…' : 'Run conflict check'}
          </button>
        </form>

        {ccError && <p className="mt-3 text-sm text-red-600">{ccError}</p>}

        {ccHits !== null && (
          <div className="mt-4">
            {ccHits.length === 0 ? (
              <div className="flex items-center gap-2 text-sm text-green-700 bg-green-50 rounded-lg px-4 py-3">
                <svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
                No conflicts found.
              </div>
            ) : (
              <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3">
                <p className="text-sm font-semibold text-red-700 mb-2">
                  {ccHits.length} potential conflict{ccHits.length !== 1 ? 's' : ''} — attorney review required
                </p>
                <ul className="space-y-1">
                  {ccHits.map((h, i) => (
                    <li key={i} className="text-sm text-red-800">
                      <span className="font-medium">{h.full_name}</span> · {h.role} · {h.matter_caption}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Client list */}
      <div className="mb-4">
        <input
          className="input w-full md:w-72"
          placeholder="Search clients…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
      </div>

      <div className="card overflow-hidden">
        {loading && (
          <div className="px-5 py-10 text-center text-text-secondary text-sm">Loading…</div>
        )}
        {error && (
          <div className="px-5 py-10 text-center text-red-600 text-sm">{error}</div>
        )}
        {!loading && !error && visible.length === 0 && (
          <div className="px-5 py-10 text-center text-text-secondary text-sm">No clients found.</div>
        )}
        {!loading && !error && visible.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-off-white">
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Name</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide hidden md:table-cell">Email</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Status</th>
              </tr>
            </thead>
            <tbody>
              {visible.map(c => (
                <tr key={c.id} className="border-b border-border last:border-0 hover:bg-off-white/60 transition-colors">
                  <td className="px-5 py-3 font-medium text-navy">{fullName(c)}</td>
                  <td className="px-5 py-3 text-text-secondary hidden md:table-cell">{c.email}</td>
                  <td className="px-5 py-3">
                    <span className={`text-xs rounded-full px-2.5 py-1 font-medium capitalize ${STATUS_COLOR[c.status] ?? 'bg-gray-100 text-gray-600'}`}>
                      {c.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
