import { useEffect, useState, type ReactNode } from 'react'
import { NavLink, Navigate, Outlet, useParams } from 'react-router-dom'
import { getGuestMatters, openGuestSession } from '../../lib/api'
import { formatTimestampDate } from '../../lib/money'
import { useAuth } from '../../context/AuthContext'
import type { GuestMatter } from '../../types'

const DESIGNATION_LABEL: Record<GuestMatter['designation'], string> = {
  testifying_expert: 'Testifying expert',
  consulting_expert: 'Consulting expert',
  non_expert: 'Financial professional',
}

/**
 * A financial professional's view of the matters they were given access to (052).
 *
 * Its own shell, like the client portal's, because the API underneath is its
 * own: everything here calls /api/v1/guest, which checks a live, consented,
 * unexpired grant on every request. Three tabs and nothing else — Accounts,
 * Transactions, Inventory — and the only write anywhere is tagging with the
 * guest's own tags.
 */
export default function GuestShell() {
  const { signOut, profile } = useAuth()
  const [matters, setMatters] = useState<GuestMatter[] | null>(null)
  const [reminder, setReminder] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // Recorded as a session so "was this used after it was revoked?" has an answer.
    openGuestSession().then(setReminder).catch(() => { /* never blocks the screen */ })
    getGuestMatters()
      // The server sorts by name too; sorting here as well keeps the picker
      // alphabetical whatever order an older API returns.
      .then(rows => setMatters([...rows].sort((a, b) =>
        a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }))))
      .catch(e => setError(e instanceof Error ? e.message : 'Could not load your matters'))
  }, [])

  if (error) {
    return <Centered title="We could not open your access">{error}</Centered>
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
      <Centered title="No matters are open to you">
        Access is opened by the firm once the client has consented, and it ends when it expires
        or is withdrawn. If you expected to see a matter here, contact the firm.
        <button type="button" className="block mx-auto mt-4 text-sm text-navy underline"
          onClick={signOut}>Sign out</button>
      </Centered>
    )
  }
  return <GuestFrame matters={matters} reminder={reminder} onSignOut={signOut}
    email={String(profile?.auth_email ?? profile?.email ?? '')} />
}

function Centered({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="min-h-screen bg-off-white flex items-center justify-center p-6">
      <div className="card p-6 max-w-md text-center">
        <h1 className="font-display text-2xl text-navy mb-2">{title}</h1>
        <div className="text-text-secondary text-sm">{children}</div>
      </div>
    </div>
  )
}

function GuestFrame({ matters, reminder, onSignOut, email }: {
  matters: GuestMatter[]
  reminder: string | null
  onSignOut: () => void
  email: string
}) {
  const { matterId } = useParams()
  const current = matterId ? Number(matterId) : matters[0].id
  if (!matterId) return <Navigate to={`/guest/${current}/accounts`} replace />
  const matter = matters.find(m => m.id === current)
  if (!matter) return <Navigate to={`/guest/${matters[0].id}/accounts`} replace />

  const tab = 'px-4 py-2 rounded-md text-sm font-medium'
  const active = 'bg-navy text-white'
  const idle = 'text-text-secondary hover:bg-navy/5'

  return (
    <div className="min-h-screen bg-off-white">
      <header className="bg-white border-b border-border">
        <div className="max-w-6xl mx-auto px-6 py-4 flex flex-wrap items-baseline gap-x-4 gap-y-2">
          <h1 className="font-display text-xl text-navy">{matter.name}</h1>
          <span className="text-xs rounded-full px-2 py-0.5 bg-navy/10 text-navy">
            {DESIGNATION_LABEL[matter.designation]}
          </span>
          <span className="text-xs text-text-secondary">
            access until {formatTimestampDate(matter.expires_at)}
          </span>
          <div className="ml-auto flex items-baseline gap-4">
            {matters.length > 1 && (
              <select className="input py-1 text-sm" value={current}
                onChange={e => { window.location.href = `/guest/${e.target.value}/accounts` }}>
                {matters.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
              </select>
            )}
            <span className="text-sm text-text-secondary">{email}</span>
            <button type="button" className="text-sm text-navy underline" onClick={onSignOut}>
              Sign out
            </button>
          </div>
        </div>
        <nav className="max-w-6xl mx-auto px-6 pb-3 flex gap-2">
          {(['accounts', 'transactions', 'inventory'] as const).map(t => (
            <NavLink key={t} to={`/guest/${current}/${t}`}
              className={({ isActive }) => `${tab} ${isActive ? active : idle}`}>
              {t[0].toUpperCase() + t.slice(1)}
            </NavLink>
          ))}
        </nav>
      </header>

      {/* The terms of the access, on every screen: it exists for the client. */}
      <p className="max-w-6xl mx-auto px-6 pt-4 text-xs text-text-secondary">
        {reminder ?? matter.purpose_reminder}
      </p>

      <main className="max-w-6xl mx-auto px-6 py-4">
        <Outlet context={{ matter }} />
      </main>
    </div>
  )
}
