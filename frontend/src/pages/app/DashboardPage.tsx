import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext'
import { getMatters } from '../../lib/api'

interface Matter {
  id: number
  matter_name: string
  short_name: string
  matter_type: string
  status: string
  is_pro_bono: boolean
}

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

export default function DashboardPage() {
  const { profile } = useAuth()
  const [matters, setMatters] = useState<Matter[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getMatters()
      .then((data: unknown) => setMatters(data as Matter[]))
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load matters'))
      .finally(() => setLoading(false))
  }, [])

  const open   = matters.filter(m => m.status === 'active' || m.status === 'intake' || m.status === 'conflict_review').length
  const proBono = matters.filter(m => m.is_pro_bono).length

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

      {/* Recent matters */}
      <div className="card overflow-hidden">
        <div className="px-5 py-4 border-b border-border flex items-center justify-between">
          <h2 className="font-semibold text-navy">Recent matters</h2>
          <Link to="/app/matters" className="text-sm text-navy/70 hover:text-navy transition-colors">
            View all →
          </Link>
        </div>

        {loading && (
          <div className="px-5 py-10 text-center text-text-secondary text-sm">Loading…</div>
        )}

        {error && (
          <div className="px-5 py-10 text-center text-red-600 text-sm">{error}</div>
        )}

        {!loading && !error && matters.length === 0 && (
          <div className="px-5 py-10 text-center text-text-secondary text-sm">
            No matters yet.{' '}
            <Link to="/app/matters" className="text-navy underline">Open the first one.</Link>
          </div>
        )}

        {!loading && !error && matters.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-off-white">
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Caption</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide hidden md:table-cell">Type</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Status</th>
              </tr>
            </thead>
            <tbody>
              {matters.slice(0, 8).map(m => (
                <tr key={m.id} className="border-b border-border last:border-0 hover:bg-off-white/60 transition-colors">
                  <td className="px-5 py-3 font-medium text-navy">
                    {m.short_name}
                    {m.is_pro_bono && (
                      <span className="ml-2 text-xs bg-purple-100 text-purple-700 rounded-full px-2 py-0.5">Pro bono</span>
                    )}
                  </td>
                  <td className="px-5 py-3 text-text-secondary hidden md:table-cell capitalize">{m.matter_type.replace('_', ' ')}</td>
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
