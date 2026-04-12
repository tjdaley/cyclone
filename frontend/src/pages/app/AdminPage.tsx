import { useEffect, useState } from 'react'
import { getStaff } from '../../lib/api'
import type { Staff } from '../../types'

function fullName(s: Staff) {
  return `${s.name.first_name} ${s.name.last_name}`
}

const ROLE_COLOR: Record<string, string> = {
  attorney:  'bg-navy/10 text-navy',
  paralegal: 'bg-blue-100 text-blue-800',
  admin:     'bg-purple-100 text-purple-800',
}

export default function AdminPage() {
  const [staff, setStaff]     = useState<Staff[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState<string | null>(null)

  useEffect(() => {
    getStaff()
      .then(setStaff)
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load staff'))
      .finally(() => setLoading(false))
  }, [])

  const linked   = staff.filter(s => s.supabase_uid).length
  const unlinked = staff.filter(s => !s.supabase_uid).length

  return (
    <div className="px-6 py-8 max-w-5xl mx-auto">
      <div className="mb-8">
        <h1 className="font-display text-3xl text-navy">Administration</h1>
        <p className="text-text-secondary mt-1">Manage staff accounts and firm settings.</p>
      </div>

      {/* Stat strip */}
      <div className="grid grid-cols-3 gap-4 mb-8">
        <div className="card p-5">
          <p className="text-xs text-text-secondary uppercase tracking-widest font-semibold mb-1">Total staff</p>
          <p className="font-display text-3xl text-navy">{loading ? '—' : staff.length}</p>
        </div>
        <div className="card p-5">
          <p className="text-xs text-text-secondary uppercase tracking-widest font-semibold mb-1">Linked accounts</p>
          <p className="font-display text-3xl text-green-700">{loading ? '—' : linked}</p>
        </div>
        <div className="card p-5">
          <p className="text-xs text-text-secondary uppercase tracking-widest font-semibold mb-1">Pending login</p>
          <p className="font-display text-3xl text-amber-600">{loading ? '—' : unlinked}</p>
        </div>
      </div>

      {/* Staff table */}
      <div className="card overflow-hidden">
        <div className="px-5 py-4 border-b border-border">
          <h2 className="font-semibold text-navy">Staff members</h2>
        </div>

        {loading && (
          <div className="px-5 py-10 text-center text-text-secondary text-sm">Loading…</div>
        )}
        {error && (
          <div className="px-5 py-10 text-center text-red-600 text-sm">{error}</div>
        )}
        {!loading && !error && staff.length === 0 && (
          <div className="px-5 py-10 text-center text-text-secondary text-sm">No staff members found.</div>
        )}
        {!loading && !error && staff.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-off-white">
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Name</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide hidden md:table-cell">Auth email</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Role</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Account</th>
                <th className="text-right px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide hidden lg:table-cell">Billing rate</th>
              </tr>
            </thead>
            <tbody>
              {staff.map(s => (
                <tr key={s.id} className="border-b border-border last:border-0 hover:bg-off-white/60 transition-colors">
                  <td className="px-5 py-3 font-medium text-navy">{fullName(s)}</td>
                  <td className="px-5 py-3 text-text-secondary hidden md:table-cell">
                    {s.auth_email ?? <span className="text-xs text-text-secondary/50 italic">not set</span>}
                  </td>
                  <td className="px-5 py-3">
                    <span className={`text-xs rounded-full px-2.5 py-1 font-medium capitalize ${ROLE_COLOR[s.role] ?? 'bg-gray-100 text-gray-600'}`}>
                      {s.role}
                    </span>
                  </td>
                  <td className="px-5 py-3">
                    {s.supabase_uid ? (
                      <span className="inline-flex items-center gap-1 text-xs text-green-700">
                        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                        </svg>
                        Linked
                      </span>
                    ) : (
                      <span className="text-xs text-amber-600">Pending first login</span>
                    )}
                  </td>
                  <td className="px-5 py-3 text-right text-text-secondary hidden lg:table-cell">
                    {s.default_billing_rate != null
                      ? `$${s.default_billing_rate.toFixed(0)}/hr`
                      : <span className="text-xs italic text-text-secondary/50">—</span>
                    }
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Info box */}
      <div className="mt-6 rounded-lg bg-navy/5 border border-navy/10 px-5 py-4 text-sm text-text-secondary">
        <p className="font-medium text-navy mb-1">Adding new staff</p>
        <p className="leading-relaxed">
          Staff records must be pre-populated with an <code className="font-mono text-xs bg-white rounded px-1 py-0.5 border border-border">auth_email</code> before
          the individual signs in for the first time. The correlation flow runs automatically on first login — no separate invitation step is needed.
        </p>
      </div>
    </div>
  )
}
