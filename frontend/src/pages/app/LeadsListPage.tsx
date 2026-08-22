import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext'
import { getLeads, assignLead } from '../../lib/api'
import type { LeadListItem, LeadStatus } from '../../types'

const STATUS_LABELS: Record<LeadStatus, string> = {
  new: 'New',
  attempted: 'Attempted',
  contacted: 'Contacted',
  qualified: 'Qualified',
  disqualified: 'Disqualified',
  conflicted: 'Conflicted',
  consultation_scheduled: 'Consult scheduled',
  consulted: 'Consulted',
  engaged: 'Engaged',
  lost: 'Lost',
  nurture: 'Nurture',
}

const STATUS_COLOR: Record<LeadStatus, string> = {
  new: 'bg-blue-100 text-blue-800',
  attempted: 'bg-amber-100 text-amber-800',
  contacted: 'bg-indigo-100 text-indigo-800',
  qualified: 'bg-teal-100 text-teal-800',
  disqualified: 'bg-gray-100 text-gray-600',
  conflicted: 'bg-red-100 text-red-800',
  consultation_scheduled: 'bg-purple-100 text-purple-800',
  consulted: 'bg-purple-200 text-purple-900',
  engaged: 'bg-green-100 text-green-800',
  lost: 'bg-gray-100 text-gray-500',
  nurture: 'bg-yellow-100 text-yellow-800',
}

// Terminal dispositions — these drop out of the "Open" filter and cannot be claimed.
const CLOSED_STATUSES: LeadStatus[] = ['engaged', 'lost', 'disqualified', 'conflicted']

function isClosed(s: LeadStatus) {
  return CLOSED_STATUSES.includes(s)
}

function formatDate(iso: string) {
  const d = new Date(iso)
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

export default function LeadsListPage() {
  const { profile } = useAuth()
  const myStaffId = profile?.staff_id ?? null

  const [leads, setLeads]     = useState<LeadListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState<string | null>(null)
  const [filter, setFilter]   = useState<'open' | 'all' | 'closed' | 'mine'>('open')
  const [search, setSearch]   = useState('')
  const [claiming, setClaiming] = useState<string | null>(null)

  useEffect(() => {
    getLeads()
      .then(setLeads)
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [])

  async function handleClaim(sessionUuid: string) {
    if (!myStaffId) return
    setClaiming(sessionUuid)
    try {
      const updated = await assignLead(sessionUuid, { staff_id: myStaffId })
      setLeads(prev => prev.map(l => l.session_uuid === sessionUuid
        ? { ...l, assigned_staff_id: updated.assigned_staff_id }
        : l))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to claim lead')
    } finally {
      setClaiming(null)
    }
  }

  const visible = useMemo(() => {
    return leads.filter(l => {
      const closed = isClosed(l.status)
      if (filter === 'open'   && closed) return false
      if (filter === 'closed' && !closed) return false
      if (filter === 'mine'   && l.assigned_staff_id !== myStaffId) return false
      if (search) {
        const q = search.toLowerCase()
        const name = (l.full_name ?? '').toLowerCase()
        const email = (l.email ?? '').toLowerCase()
        const source = (l.lead_source ?? '').toLowerCase()
        if (!name.includes(q) && !email.includes(q) && !source.includes(q)) return false
      }
      return true
    })
  }, [leads, filter, search, myStaffId])

  return (
    <div className="px-6 py-8 max-w-6xl mx-auto">
      <div className="mb-8">
        <h1 className="font-display text-3xl text-navy">Leads</h1>
        <p className="text-text-secondary mt-1">{leads.length} total · {visible.length} shown</p>
      </div>

      <div className="flex flex-col sm:flex-row gap-3 mb-6">
        <input
          className="input flex-1"
          placeholder="Search by name, email, or source…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
        <div className="flex rounded-lg border border-border overflow-hidden text-sm">
          {(['open', 'mine', 'all', 'closed'] as const).map(f => (
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
        {loading && <div className="px-5 py-10 text-center text-text-secondary text-sm">Loading…</div>}
        {error && <div className="px-5 py-10 text-center text-red-600 text-sm">{error}</div>}
        {!loading && !error && visible.length === 0 && (
          <div className="px-5 py-10 text-center text-text-secondary text-sm">No leads match your filter.</div>
        )}
        {!loading && !error && visible.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-off-white">
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Lead</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide hidden md:table-cell">Source</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide hidden lg:table-cell">Captured</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Status</th>
              </tr>
            </thead>
            <tbody>
              {visible.map(l => (
                <tr key={l.session_uuid} className="border-b border-border last:border-0 hover:bg-off-white/60 transition-colors">
                  <td className="px-5 py-3">
                    <Link to={`/app/leads/${l.session_uuid}`} className="font-medium text-navy hover:underline">
                      {l.full_name ?? '(no name)'}
                    </Link>
                    <div className="text-xs text-text-secondary mt-0.5">
                      {l.email && <span>{l.email}</span>}
                      {l.email && l.telephone && <span className="mx-1">·</span>}
                      {l.telephone && <span>{l.telephone}</span>}
                      {l.attorney_slug && <span className="ml-2 text-xs bg-gray-100 text-gray-700 rounded px-1.5 py-0.5">{l.attorney_slug}</span>}
                    </div>
                  </td>
                  <td className="px-5 py-3 text-text-secondary hidden md:table-cell">
                    {l.lead_source ?? '—'}
                    {l.audit_score != null && l.audit_score > 0 && (
                      <span className="ml-2 text-xs bg-amber-50 text-amber-800 rounded px-1.5 py-0.5">score {l.audit_score}</span>
                    )}
                  </td>
                  <td className="px-5 py-3 text-text-secondary hidden lg:table-cell whitespace-nowrap">{formatDate(l.captured_at)}</td>
                  <td className="px-5 py-3">
                    <span className={`text-xs rounded-full px-2.5 py-1 font-medium ${STATUS_COLOR[l.status]}`}>
                      {STATUS_LABELS[l.status]}
                    </span>
                    {l.assigned_staff_id === null && myStaffId !== null && !isClosed(l.status) && (
                      <button
                        onClick={() => handleClaim(l.session_uuid)}
                        disabled={claiming === l.session_uuid}
                        className="ml-2 text-xs px-2.5 py-1 rounded-full border border-navy text-navy hover:bg-navy hover:text-white transition-colors disabled:opacity-50"
                      >
                        {claiming === l.session_uuid ? '…' : 'Assign to me'}
                      </button>
                    )}
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
