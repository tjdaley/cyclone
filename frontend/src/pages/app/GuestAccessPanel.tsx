import { useEffect, useState } from 'react'
import {
  attestGuestConsent, exportGuestDocuments, getGuestDocuments, getMatterGuests, inviteGuest,
  renewGuest, revokeGuest, setGuestDesignation,
} from '../../lib/api'
import { formatDate, formatTimestampDate } from '../../lib/money'
import ExportButtons from '../../components/ExportButtons'
import type {
  ConsentMethod, GuestDesignation, GuestGrant, GuestGrantStatus, GuestLedgerEntry,
} from '../../types'

const DESIGNATIONS: GuestDesignation[] = ['testifying_expert', 'consulting_expert', 'non_expert']
const DESIGNATION_LABEL: Record<GuestDesignation, string> = {
  testifying_expert: 'Testifying expert',
  consulting_expert: 'Consulting expert',
  non_expert: 'Non-expert',
}
const METHOD_LABEL: Record<ConsentMethod, string> = {
  phone: 'By phone', in_person: 'In person', email: 'By email', letter: 'By letter', other: 'Other',
}
const STATUS: Record<GuestGrantStatus, { label: string; cls: string }> = {
  pending_consent: { label: 'awaiting consent', cls: 'bg-amber-100 text-amber-800' },
  active: { label: 'active', cls: 'bg-green-100 text-green-800' },
  expired: { label: 'expired', cls: 'bg-gray-100 text-gray-600' },
  revoked: { label: 'revoked', cls: 'bg-red-100 text-red-800' },
}

/**
 * Financial professionals with access to this matter (052).
 *
 * A tracing expert or CPA sees this matter's Accounts, Transactions and
 * Inventory — nothing else — once a staff member records that the client
 * consented. Access expires on its date and can be withdrawn at any time; both
 * take effect on the guest's next click. "Documents provided" is the list a
 * disclosure needs: every statement the guest had access to, kept even if the
 * statement is later deleted.
 */
export default function GuestAccessPanel({ matterId }: { matterId: number }) {
  const [grants, setGrants] = useState<GuestGrant[]>([])
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function reload() {
    try { setGrants(await getMatterGuests(matterId)) }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not load guest access') }
  }
  useEffect(() => { void reload() }, [matterId])  // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="card p-5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="font-semibold text-navy">
          Financial professionals
          <span className="ml-2 text-sm font-normal text-text-secondary">
            {grants.filter(g => g.status === 'active').length} active
          </span>
        </h2>
        <button type="button" className="text-xs text-navy underline" onClick={() => setAdding(a => !a)}>
          {adding ? 'Cancel' : 'Give a professional access'}
        </button>
      </div>
      <p className="text-sm text-text-secondary mb-3">
        A tracing expert, CPA or similar can see this matter&rsquo;s accounts, transactions and
        inventory, export them, and tag transactions with tags of their own — nothing else, and
        only once you record that the client consented.
      </p>

      {adding && (
        <InviteForm matterId={matterId} onDone={async () => { setAdding(false); await reload() }}
          onError={setError} />
      )}
      {error && <p className="text-xs text-red-700 mb-2">{error}</p>}

      {grants.length === 0 ? (
        <p className="text-sm text-text-secondary">Nobody outside the firm has access to this matter.</p>
      ) : (
        <div className="space-y-3">
          {grants.map(g => (
            <GrantRow key={g.id} matterId={matterId} grant={g} onChanged={reload} onError={setError} />
          ))}
        </div>
      )}
    </div>
  )
}

function InviteForm({ matterId, onDone, onError }: {
  matterId: number
  onDone: () => Promise<void>
  onError: (m: string | null) => void
}) {
  const [draft, setDraft] = useState({
    full_name: '', email: '', organization: '', profession: '',
    designation: 'consulting_expert' as GuestDesignation, purpose: '', expires_in_days: 180,
  })
  const [busy, setBusy] = useState(false)
  const set = <K extends keyof typeof draft>(k: K, v: (typeof draft)[K]) =>
    setDraft(prev => ({ ...prev, [k]: v }))

  async function save() {
    setBusy(true); onError(null)
    try {
      await inviteGuest(matterId, {
        ...draft,
        organization: draft.organization || null, profession: draft.profession || null,
        purpose: draft.purpose || null,
      })
      await onDone()
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Could not give access')
    } finally { setBusy(false) }
  }

  return (
    <div className="border border-border rounded p-3 mb-3 grid gap-3 md:grid-cols-2">
      <div>
        <label className="label" htmlFor="g-name">Name</label>
        <input id="g-name" className="input mt-1" value={draft.full_name}
          onChange={e => set('full_name', e.target.value)} />
      </div>
      <div>
        <label className="label" htmlFor="g-email">Email they sign in with</label>
        <input id="g-email" className="input mt-1" type="email" value={draft.email}
          onChange={e => set('email', e.target.value)} />
      </div>
      <div>
        <label className="label" htmlFor="g-org">Organization</label>
        <input id="g-org" className="input mt-1" value={draft.organization}
          onChange={e => set('organization', e.target.value)} />
      </div>
      <div>
        <label className="label" htmlFor="g-prof">Profession</label>
        <input id="g-prof" className="input mt-1" placeholder="Forensic accountant, CPA, …"
          value={draft.profession} onChange={e => set('profession', e.target.value)} />
      </div>
      <div>
        <label className="label" htmlFor="g-desig">Designation</label>
        <select id="g-desig" className="input mt-1" value={draft.designation}
          onChange={e => set('designation', e.target.value as GuestDesignation)}>
          {DESIGNATIONS.map(d => <option key={d} value={d}>{DESIGNATION_LABEL[d]}</option>)}
        </select>
        <p className="text-xs text-text-secondary mt-1">
          What a testifying expert was provided is disclosable; the list is kept for you.
        </p>
      </div>
      <div>
        <label className="label" htmlFor="g-days">Access lasts (days)</label>
        <input id="g-days" className="input mt-1" type="number" min={1} max={730}
          value={draft.expires_in_days}
          onChange={e => set('expires_in_days', Number(e.target.value) || 180)} />
      </div>
      <div className="md:col-span-2">
        <label className="label" htmlFor="g-purpose">Retained to</label>
        <input id="g-purpose" className="input mt-1" placeholder="Trace the separate-property claim on …"
          value={draft.purpose} onChange={e => set('purpose', e.target.value)} />
      </div>
      <div className="md:col-span-2 flex items-center justify-between">
        <p className="text-xs text-text-secondary">
          Nothing is visible to them until you record the client&rsquo;s consent.
        </p>
        <button type="button" className="btn-primary" onClick={save}
          disabled={busy || !draft.full_name.trim() || !draft.email.includes('@')}>
          {busy ? 'Saving…' : 'Add'}
        </button>
      </div>
    </div>
  )
}

function GrantRow({ matterId, grant, onChanged, onError }: {
  matterId: number
  grant: GuestGrant
  onChanged: () => Promise<void>
  onError: (m: string | null) => void
}) {
  const [consenting, setConsenting] = useState(false)
  const [revoking, setRevoking] = useState(false)
  const [showDocs, setShowDocs] = useState(false)
  const [docs, setDocs] = useState<GuestLedgerEntry[]>([])
  const [busy, setBusy] = useState(false)
  const status = STATUS[grant.status]
  const live = grant.status !== 'revoked'

  /** Run one change. True when it saved, so a form can close on success and stay open on failure. */
  async function act(fn: () => Promise<unknown>): Promise<boolean> {
    setBusy(true); onError(null)
    try { await fn() }
    catch (e) {
      onError(e instanceof Error ? e.message : 'Could not save')
      setBusy(false)
      return false
    }
    // Saved. Refresh the list after, so a slow reload never keeps a form open
    // over a change that already happened.
    try { await onChanged() } finally { setBusy(false) }
    return true
  }

  async function toggleDocs() {
    if (!showDocs) {
      try { setDocs(await getGuestDocuments(matterId, grant.id)) }
      catch (e) { onError(e instanceof Error ? e.message : 'Could not load the list'); return }
    }
    setShowDocs(s => !s)
  }

  return (
    <div className="border border-border rounded p-3 text-sm">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="font-medium text-text-primary">{grant.full_name}</span>
        {grant.organization && <span className="text-text-secondary">{grant.organization}</span>}
        <span className="text-xs text-text-secondary">{grant.email}</span>
        <span className={`text-xs rounded-full px-2 py-0.5 ${status.cls}`}>{status.label}</span>
        <select className="text-xs border border-border rounded px-1 py-0.5 bg-white"
          value={grant.designation} disabled={busy || !live}
          onChange={e => act(() => setGuestDesignation(matterId, grant.id, e.target.value as GuestDesignation))}>
          {DESIGNATIONS.map(d => <option key={d} value={d}>{DESIGNATION_LABEL[d]}</option>)}
        </select>
        <span className="ml-auto flex gap-3 text-xs">
          {grant.status === 'pending_consent' && (
            <button type="button" className="text-navy underline" onClick={() => setConsenting(c => !c)}>
              {consenting ? 'Cancel' : 'Record consent'}
            </button>
          )}
          {(grant.status === 'active' || grant.status === 'expired' || grant.status === 'pending_consent') && (
            <button type="button" className="text-navy underline" disabled={busy}
              onClick={() => {
                const days = window.prompt('Run access for how many days from today?', '180')
                if (days && Number(days) > 0) void act(() => renewGuest(matterId, grant.id, Number(days)))
              }}>Renew</button>
          )}
          {live && (
            <button type="button" className="text-red-700 underline" disabled={busy}
              onClick={() => setRevoking(true)}>
              Revoke
            </button>
          )}
        </span>
      </div>

      <p className="text-xs text-text-secondary mt-1">
        {grant.status === 'revoked'
          ? <>Revoked {formatTimestampDate(grant.revoked_at)} by {grant.revoked_by}
              {grant.revoke_reason ? ` — ${grant.revoke_reason}` : ''}.</>
          : <>Access {grant.status === 'expired' ? 'expired' : 'until'} {formatTimestampDate(grant.expires_at)}.</>}
        {' '}Added by {grant.requested_by ?? 'staff'} on {formatTimestampDate(grant.created_at)}.
        {grant.last_signed_in_at && <> Last opened {formatTimestampDate(grant.last_signed_in_at)}.</>}
        {grant.purpose && <> Retained to: {grant.purpose}.</>}
      </p>
      {grant.consents.map((c, i) => (
        <p key={i} className="text-xs text-text-secondary">
          Consent {METHOD_LABEL[c.method].toLowerCase()} on {formatDate(c.consent_date)}, attested
          by {c.attested_by}{c.note ? ` — ${c.note}` : ''}.
        </p>
      ))}

      {consenting && (
        <ConsentForm busy={busy} onSubmit={async payload => {
          let saved = false
          await act(async () => {
            await attestGuestConsent(matterId, grant.id, payload)
            saved = true
            setConsenting(false)   // the moment it is recorded, not after the list reloads
          })
          if (!saved) setConsenting(true)
        }} />
      )}

      {revoking && (
        <RevokeDialog name={grant.full_name} busy={busy}
          onCancel={() => setRevoking(false)}
          onRevoke={async (reason, everywhere) => {
            if (await act(() => revokeGuest(matterId, grant.id, reason, everywhere))) setRevoking(false)
          }} />
      )}

      {grant.status !== 'pending_consent' && (
        <button type="button" className="text-xs text-navy underline mt-2" onClick={toggleDocs}>
          {showDocs ? 'Hide' : 'Documents provided'} ({grant.documents_provided})
        </button>
      )}
      {showDocs && <DocumentList matterId={matterId} grant={grant} docs={docs} />}
    </div>
  )
}

/**
 * Withdraw access — this matter, every matter, or not at all.
 *
 * A real dialog rather than two browser prompts, because the prompts could not
 * offer a true Cancel and made OK — the button people click without reading —
 * the one that withdrew access everywhere. Here Cancel has the focus, "this
 * matter" is the ordinary choice, and "every matter" is set apart and red.
 */
function RevokeDialog({ name, busy, onCancel, onRevoke }: {
  name: string
  busy: boolean
  onCancel: () => void
  onRevoke: (reason: string | null, everywhere: boolean) => Promise<void>
}) {
  const [reason, setReason] = useState('')
  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-start justify-center overflow-y-auto p-4"
      role="dialog" aria-modal="true" aria-label="Withdraw access"
      onKeyDown={e => { if (e.key === 'Escape') onCancel() }}
      onClick={e => { if (e.target === e.currentTarget) onCancel() }}>
      <div className="card p-5 w-full max-w-lg my-16 bg-white">
        <h3 className="font-semibold text-navy">Withdraw {name}&rsquo;s access</h3>
        <p className="text-sm text-text-secondary mt-1">
          It takes effect on their next click. What they were provided stays on record, and they
          can be given access again later — with the client&rsquo;s consent again.
        </p>
        <label className="label mt-4 block" htmlFor="revoke-reason">Reason (optional)</label>
        <input id="revoke-reason" className="input mt-1" value={reason}
          placeholder="Engagement ended, report delivered, …"
          onChange={e => setReason(e.target.value)} />
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <button type="button" className="btn-secondary" autoFocus disabled={busy} onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className="btn-primary" disabled={busy}
            onClick={() => onRevoke(reason.trim() || null, false)}>
            {busy ? 'Withdrawing…' : 'Withdraw from this matter'}
          </button>
          <button type="button"
            className="ml-auto text-sm text-red-700 underline disabled:opacity-40 disabled:no-underline"
            disabled={busy} onClick={() => onRevoke(reason.trim() || null, true)}>
            Withdraw from every matter
          </button>
        </div>
      </div>
    </div>
  )
}

function ConsentForm({ onSubmit, busy }: {
  onSubmit: (p: { method: ConsentMethod; consent_date: string; note: string | null }) => Promise<void>
  busy: boolean
}) {
  const [method, setMethod] = useState<ConsentMethod>('phone')
  const [when, setWhen] = useState(new Date().toISOString().slice(0, 10))
  const [note, setNote] = useState('')
  return (
    <div className="mt-2 border-t border-border pt-2 flex flex-wrap items-end gap-3">
      <div>
        <label className="label" htmlFor="c-method">The client consented</label>
        <select id="c-method" className="input mt-1" value={method}
          onChange={e => setMethod(e.target.value as ConsentMethod)}>
          {(Object.keys(METHOD_LABEL) as ConsentMethod[]).map(m => (
            <option key={m} value={m}>{METHOD_LABEL[m]}</option>
          ))}
        </select>
      </div>
      <div>
        <label className="label" htmlFor="c-date">On</label>
        <input id="c-date" type="date" className="input mt-1" value={when}
          max={new Date().toISOString().slice(0, 10)} onChange={e => setWhen(e.target.value)} />
      </div>
      <div className="flex-1 min-w-48">
        <label className="label" htmlFor="c-note">Note</label>
        <input id="c-note" className="input mt-1" placeholder="Spoke with the client; agreed to access for …"
          value={note} onChange={e => setNote(e.target.value)} />
      </div>
      <button type="button" className="btn-primary" disabled={busy || !when}
        onClick={() => onSubmit({ method, consent_date: when, note: note.trim() || null })}>
        {busy ? 'Recording…' : 'Record and open access'}
      </button>
      <p className="basis-full text-xs text-text-secondary">
        This record names you as the person attesting. They are emailed that access is open, with a
        reminder that it exists solely for the client&rsquo;s benefit.
      </p>
    </div>
  )
}

function DocumentList({ matterId, grant, docs }: {
  matterId: number
  grant: GuestGrant
  docs: GuestLedgerEntry[]
}) {
  const [name, setName] = useState(`Documents Provided to ${grant.full_name}`)
  return (
    <div className="mt-2 border-t border-border pt-2">
      {docs.length === 0 ? (
        <p className="text-xs text-text-secondary">No statements have been made available yet.</p>
      ) : (
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-text-secondary">
              <th className="py-1 pr-3 font-medium">Account</th>
              <th className="py-1 pr-3 font-medium">Period</th>
              <th className="py-1 pr-3 font-medium">Document</th>
              <th className="py-1 pr-3 font-medium">Bates</th>
              <th className="py-1 font-medium">Available</th>
            </tr>
          </thead>
          <tbody>
            {docs.map(d => (
              <tr key={d.statement_id} className={d.removed_at ? 'text-gray-400' : ''}>
                <td className="py-1 pr-3">{d.account_label}</td>
                <td className="py-1 pr-3 tabular-nums">{formatDate(d.period_start)} – {formatDate(d.period_end)}</td>
                <td className="py-1 pr-3">{d.source_filename ?? '—'}</td>
                <td className="py-1 pr-3 font-mono">
                  {d.bates_first}{d.bates_last && d.bates_last !== d.bates_first ? `–${d.bates_last}` : ''}
                </td>
                <td className="py-1">
                  from {formatTimestampDate(d.first_available_at)}
                  {d.removed_at && <> until {formatTimestampDate(d.removed_at)} (since deleted)</>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="mt-2">
        <ExportButtons name={name} onNameChange={setName} count={docs.length}
          hint="For a disclosure: every statement this person had access to under this grant"
          onExport={format => exportGuestDocuments(matterId, grant.id, format,
            name.trim() || 'Documents Provided')} />
      </div>
    </div>
  )
}
