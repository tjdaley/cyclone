import { useEffect, useState } from 'react'
import { getPortalAccess, givePortalAccess, revokePortalAccess } from '../../lib/api'
import type { PortalAccess } from '../../types'

/**
 * Gives or revokes a client's sign-in to the portal, from the client edit form.
 *
 * Access goes to the login email as SAVED, so an edited-but-unsaved address
 * holds the button back: the screen must not show one address while the server
 * grants another.
 */
export default function ClientPortalAccessPanel({ clientId, savedEmail, draftEmail }: {
  clientId: number
  savedEmail: string
  draftEmail: string
}) {
  const [access, setAccess] = useState<PortalAccess | null>(null)
  const [busy, setBusy] = useState<'give' | 'revoke' | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let current = true
    setAccess(null)
    setError(null)
    getPortalAccess(clientId)
      .then(a => { if (current) setAccess(a) })
      .catch(e => { if (current) setError(e instanceof Error ? e.message : 'Could not read portal access') })
    return () => { current = false }
  }, [clientId])

  const unsaved = draftEmail.trim().toLowerCase() !== savedEmail.trim().toLowerCase()

  async function give() {
    if (!window.confirm(
      `Give portal access to ${savedEmail}?\n\nThe client will be able to read the whole of each of ` +
      `their matters, including the other side's produced accounts and transactions.`,
    )) return
    setBusy('give'); setError(null)
    try {
      setAccess(await givePortalAccess(clientId))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not give access')
    } finally { setBusy(null) }
  }

  async function revoke() {
    if (!window.confirm(
      `Revoke portal access for ${access?.auth_email ?? savedEmail}?\n\nThey lose access on their ` +
      `next click. Anything they tagged stays on the transactions.`,
    )) return
    setBusy('revoke'); setError(null)
    try {
      setAccess(await revokePortalAccess(clientId))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not revoke access')
    } finally { setBusy(null) }
  }

  return (
    <div className="rounded-lg border border-border bg-white p-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-navy">Client portal</p>
          <p className="text-sm text-text-secondary">
            {access === null && !error && 'Checking…'}
            {access?.status === 'none' && 'No access. A login email alone does not let the client sign in.'}
            {access?.status === 'awaiting_sign_in' && `Access given to ${access.auth_email}. They have not signed in yet.`}
            {access?.status === 'active' && `Active. Signs in as ${access.auth_email}.`}
          </p>
        </div>
        {access?.status === 'none' && (
          <button type="button" className="btn-primary text-sm" onClick={give} disabled={busy !== null || unsaved}>
            {busy === 'give' ? 'Giving access…' : 'Give portal access'}
          </button>
        )}
        {access && access.status !== 'none' && (
          <button type="button" className="btn-secondary text-sm" onClick={revoke} disabled={busy !== null}>
            {busy === 'revoke' ? 'Revoking…' : 'Revoke portal access'}
          </button>
        )}
      </div>
      {access?.status === 'none' && unsaved && (
        <p className="mt-2 text-xs text-text-secondary">Save the new login email first.</p>
      )}
      {access && access.status !== 'none' && unsaved && (
        <p className="mt-2 text-xs text-amber-700">
          To change the login email, revoke access first, then save and give access again.
        </p>
      )}
      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
    </div>
  )
}
