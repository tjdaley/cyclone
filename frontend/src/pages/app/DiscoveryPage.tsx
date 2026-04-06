import { useEffect, useState } from 'react'
import { getMatters, getDiscoveryRequests } from '../../lib/api'

interface Matter {
  id: number
  caption: string
  status: string
}

interface DiscoveryRequest {
  id: number
  request_type: string
  request_number: string
  request_text: string
  status: string
  assigned_to_client: boolean
}

const STATUS_COLOR: Record<string, string> = {
  pending_client:   'bg-amber-100 text-amber-800',
  client_responded: 'bg-blue-100 text-blue-800',
  attorney_review:  'bg-purple-100 text-purple-800',
  finalized:        'bg-green-100 text-green-800',
  objected:         'bg-red-100 text-red-800',
}

const TYPE_LABEL: Record<string, string> = {
  interrogatory: 'Interrogatory',
  rfa:           'RFA',
  rfp:           'RFP',
}

export default function DiscoveryPage() {
  const [matters, setMatters]       = useState<Matter[]>([])
  const [matterId, setMatterId]     = useState<number | null>(null)
  const [requests, setRequests]     = useState<DiscoveryRequest[]>([])
  const [loading, setLoading]       = useState(false)
  const [error, setError]           = useState<string | null>(null)
  const [typeFilter, setTypeFilter] = useState<string>('all')
  const [expanded, setExpanded]     = useState<number | null>(null)

  useEffect(() => {
    getMatters().then((data: unknown) => {
      const ms = data as Matter[]
      setMatters(ms.filter(m => m.status === 'open'))
    }).catch(console.error)
  }, [])

  useEffect(() => {
    if (!matterId) return
    setLoading(true)
    setError(null)
    getDiscoveryRequests(matterId)
      .then((data: unknown) => setRequests(data as DiscoveryRequest[]))
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [matterId])

  const types = ['all', ...Array.from(new Set(requests.map(r => r.request_type)))]
  const visible = requests.filter(r => typeFilter === 'all' || r.request_type === typeFilter)

  const summary = {
    total:    requests.length,
    pending:  requests.filter(r => r.status === 'pending_client').length,
    review:   requests.filter(r => r.status === 'attorney_review').length,
    done:     requests.filter(r => r.status === 'finalized').length,
  }

  return (
    <div className="px-6 py-8 max-w-5xl mx-auto">
      <div className="mb-8">
        <h1 className="font-display text-3xl text-navy">Discovery</h1>
        <p className="text-text-secondary mt-1">Review, classify, and track discovery requests.</p>
      </div>

      {/* Matter selector */}
      <div className="card p-5 mb-6">
        <label className="label" htmlFor="matter-select">Select matter</label>
        <select
          id="matter-select"
          className="input mt-1"
          value={matterId ?? ''}
          onChange={e => setMatterId(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">— choose a matter —</option>
          {matters.map(m => (
            <option key={m.id} value={m.id}>{m.caption}</option>
          ))}
        </select>
      </div>

      {matterId && (
        <>
          {/* Summary strip */}
          <div className="grid grid-cols-4 gap-3 mb-6">
            {[
              { label: 'Total',         val: summary.total },
              { label: 'Pending client', val: summary.pending },
              { label: 'Atty review',   val: summary.review },
              { label: 'Finalized',     val: summary.done },
            ].map(s => (
              <div key={s.label} className="card p-4 text-center">
                <p className="font-display text-2xl text-navy">{s.val}</p>
                <p className="text-xs text-text-secondary mt-1">{s.label}</p>
              </div>
            ))}
          </div>

          {/* Type filter */}
          <div className="flex flex-wrap gap-2 mb-4">
            {types.map(t => (
              <button
                key={t}
                onClick={() => setTypeFilter(t)}
                className={`text-xs px-3 py-1.5 rounded-full font-medium capitalize transition-colors ${
                  typeFilter === t
                    ? 'bg-navy text-white'
                    : 'bg-off-white border border-border text-text-secondary hover:text-navy'
                }`}
              >
                {t === 'all' ? 'All' : TYPE_LABEL[t] ?? t}
              </button>
            ))}
          </div>

          {/* Request list */}
          <div className="space-y-3">
            {loading && (
              <div className="card px-5 py-10 text-center text-text-secondary text-sm">Loading…</div>
            )}
            {error && (
              <div className="card px-5 py-10 text-center text-red-600 text-sm">{error}</div>
            )}
            {!loading && !error && visible.length === 0 && (
              <div className="card px-5 py-10 text-center text-text-secondary text-sm">
                No discovery requests found.
              </div>
            )}
            {!loading && !error && visible.map(r => (
              <div key={r.id} className="card overflow-hidden">
                <button
                  className="w-full flex items-start justify-between gap-4 px-5 py-4 text-left hover:bg-off-white/60 transition-colors"
                  onClick={() => setExpanded(expanded === r.id ? null : r.id)}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <span className="text-xs font-mono text-text-secondary flex-shrink-0">
                      {TYPE_LABEL[r.request_type] ?? r.request_type} {r.request_number}
                    </span>
                    <span className="text-sm text-navy truncate">{r.request_text.slice(0, 120)}{r.request_text.length > 120 ? '…' : ''}</span>
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0">
                    <span className={`text-xs rounded-full px-2.5 py-1 font-medium ${STATUS_COLOR[r.status] ?? 'bg-gray-100 text-gray-600'}`}>
                      {r.status.replace(/_/g, ' ')}
                    </span>
                    <svg
                      className={`w-4 h-4 text-text-secondary transition-transform ${expanded === r.id ? 'rotate-180' : ''}`}
                      fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24"
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                    </svg>
                  </div>
                </button>

                {expanded === r.id && (
                  <div className="px-5 pb-5 border-t border-border pt-4">
                    <p className="text-sm text-navy leading-relaxed whitespace-pre-wrap">{r.request_text}</p>
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
