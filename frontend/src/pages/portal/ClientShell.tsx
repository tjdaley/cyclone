import { useEffect, useState } from 'react'
import { NavLink, Navigate, Outlet, useParams } from 'react-router-dom'
import { getPortalMatters } from '../../lib/api'
import { useAuth } from '../../context/AuthContext'
import type { PortalMatter } from '../../types'

/**
 * The client's own view of their case.
 *
 * A separate shell rather than the staff `AppShell` with items hidden, for the
 * same reason the API has its own prefix: hiding a control is not a protection,
 * and a surface built for two audiences ends up written for neither. The body
 * is already in `relaxed` density for this role — more room, larger type.
 *
 * Everything here is read-only except tagging.
 */
export default function ClientShell() {
  const { profile, signOut } = useAuth()
  const [matters, setMatters] = useState<PortalMatter[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getPortalMatters()
      .then(setMatters)
      .catch(e => setError(e instanceof Error ? e.message : 'Could not load your case'))
  }, [])

  if (error) {
    return (
      <div className="min-h-screen bg-off-white flex items-center justify-center p-6">
        <div className="card p-6 max-w-md text-center">
          <h1 className="font-display text-2xl text-navy mb-2">We could not open your case</h1>
          <p className="text-text-secondary">{error}</p>
          <p className="text-sm text-text-secondary mt-4">
            Please contact your attorney's office if this keeps happening.
          </p>
        </div>
      </div>
    )
  }

  if (matters === null) {
    return (
      <div className="min-h-screen bg-off-white flex items-center justify-center">
        <div className="animate-spin w-8 h-8 border-4 border-navy/20 border-t-navy rounded-full" />
      </div>
    )
  }

  if (matters.length === 0) {
    return (
      <div className="min-h-screen bg-off-white flex items-center justify-center p-6">
        <div className="card p-6 max-w-md text-center">
          <h1 className="font-display text-2xl text-navy mb-2">Nothing to show yet</h1>
          <p className="text-text-secondary">
            Your attorney's office has not shared a case with you yet. They will let you know
            when there is something here.
          </p>
        </div>
      </div>
    )
  }

  return <PortalFrame matters={matters} onSignOut={signOut} name={String(profile?.email ?? '')} />
}

function PortalFrame({ matters, onSignOut, name }: {
  matters: PortalMatter[]
  onSignOut: () => void
  name: string
}) {
  const { matterId } = useParams()
  // One case is the overwhelmingly common shape, so there is no case picker to
  // navigate first — it opens straight into the only one.
  const current = matterId ? Number(matterId) : matters[0].id
  if (!matterId) return <Navigate to={`/portal/${current}/transactions`} replace />

  const matter = matters.find(m => m.id === current)
  if (!matter) return <Navigate to={`/portal/${matters[0].id}/transactions`} replace />

  const tab = 'px-4 py-2 rounded-md text-sm font-medium'
  const active = 'bg-navy text-white'
  const idle = 'text-text-secondary hover:bg-navy/5'

  return (
    <div className="min-h-screen bg-off-white">
      <header className="bg-white border-b border-border">
        <div className="max-w-5xl mx-auto px-6 py-4 flex flex-wrap items-baseline gap-x-4 gap-y-2">
          <h1 className="font-display text-xl text-navy">{matter.matter_name}</h1>
          {matter.matter_number && (
            <span className="text-sm text-text-secondary">Cause No. {matter.matter_number}</span>
          )}
          <div className="ml-auto flex items-baseline gap-4">
            {matters.length > 1 && (
              <select className="input py-1 text-sm" value={current}
                onChange={e => { window.location.href = `/portal/${e.target.value}/transactions` }}>
                {matters.map(m => <option key={m.id} value={m.id}>{m.matter_name}</option>)}
              </select>
            )}
            <span className="text-sm text-text-secondary">{name}</span>
            <button type="button" className="text-sm text-navy underline" onClick={onSignOut}>
              Sign out
            </button>
          </div>
        </div>
        <nav className="max-w-5xl mx-auto px-6 pb-3 flex gap-2">
          <NavLink to={`/portal/${current}/transactions`}
            className={({ isActive }) => `${tab} ${isActive ? active : idle}`}>
            Transactions
          </NavLink>
          <NavLink to={`/portal/${current}/accounts`}
            className={({ isActive }) => `${tab} ${isActive ? active : idle}`}>
            Accounts &amp; statements
          </NavLink>
          <NavLink to={`/portal/${current}/statements-held`}
            className={({ isActive }) => `${tab} ${isActive ? active : idle}`}>
            What is still needed
          </NavLink>
          {/* Last, and deliberately plainly named: somebody who is lost looks
              for help, not for "documentation". */}
          <NavLink to={`/portal/${current}/guide`}
            className={({ isActive }) => `${tab} ${isActive ? active : idle}`}>
            How this works
          </NavLink>
        </nav>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-6">
        <Outlet context={{ matter }} />
      </main>
    </div>
  )
}
