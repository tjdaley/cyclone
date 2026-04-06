import { useEffect, useState } from 'react'
import { getMatters } from '../../lib/api'

interface Matter {
  id: number
  caption: string
  matter_type: string
  status: string
  is_pro_bono: boolean
  open_date: string
}

const STATUS_COLOR: Record<string, string> = {
  open:    'bg-green-100 text-green-800',
  closed:  'bg-gray-100 text-gray-600',
  pending: 'bg-amber-100 text-amber-800',
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

export default function MattersPage() {
  const [matters, setMatters]   = useState<Matter[]>([])
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState<string | null>(null)
  const [filter, setFilter]     = useState<'all' | 'open' | 'closed'>('open')
  const [search, setSearch]     = useState('')

  useEffect(() => {
    getMatters()
      .then((data: unknown) => setMatters(data as Matter[]))
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [])

  const visible = matters.filter(m => {
    if (filter === 'open'   && m.status !== 'open')   return false
    if (filter === 'closed' && m.status !== 'closed') return false
    if (search && !m.caption.toLowerCase().includes(search.toLowerCase())) return false
    return true
  })

  return (
    <div className="px-6 py-8 max-w-5xl mx-auto">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-8">
        <div>
          <h1 className="font-display text-3xl text-navy">Matters</h1>
          <p className="text-text-secondary mt-1">{matters.length} total</p>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-3 mb-6">
        <input
          className="input flex-1"
          placeholder="Search matters…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
        <div className="flex rounded-lg border border-border overflow-hidden text-sm">
          {(['all', 'open', 'closed'] as const).map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-4 py-2 capitalize font-medium transition-colors ${
                filter === f ? 'bg-navy text-white' : 'bg-white text-text-secondary hover:bg-off-white'
              }`}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      <div className="card overflow-hidden">
        {loading && (
          <div className="px-5 py-10 text-center text-text-secondary text-sm">Loading…</div>
        )}

        {error && (
          <div className="px-5 py-10 text-center text-red-600 text-sm">{error}</div>
        )}

        {!loading && !error && visible.length === 0 && (
          <div className="px-5 py-10 text-center text-text-secondary text-sm">
            No matters match your filter.
          </div>
        )}

        {!loading && !error && visible.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-off-white">
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Caption</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide hidden md:table-cell">Type</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide hidden lg:table-cell">Opened</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Status</th>
              </tr>
            </thead>
            <tbody>
              {visible.map(m => (
                <tr key={m.id} className="border-b border-border last:border-0 hover:bg-off-white/60 transition-colors">
                  <td className="px-5 py-3 font-medium text-navy">
                    {m.caption}
                    {m.is_pro_bono && (
                      <span className="ml-2 text-xs bg-purple-100 text-purple-700 rounded-full px-2 py-0.5">Pro bono</span>
                    )}
                  </td>
                  <td className="px-5 py-3 text-text-secondary hidden md:table-cell capitalize">
                    {m.matter_type.replace(/_/g, ' ')}
                  </td>
                  <td className="px-5 py-3 text-text-secondary hidden lg:table-cell">
                    {formatDate(m.open_date)}
                  </td>
                  <td className="px-5 py-3">
                    <span className={`text-xs rounded-full px-2.5 py-1 font-medium capitalize ${STATUS_COLOR[m.status] ?? 'bg-gray-100 text-gray-600'}`}>
                      {m.status}
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
