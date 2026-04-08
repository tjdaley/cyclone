import { useEffect, useState, FormEvent } from 'react'
import { getClients, getStaff, createClient, updateClient, conflictCheck, ClientCreatePayload } from '../../lib/api'

interface ClientName {
  courtesy_title?: string | null
  first_name: string
  middle_name?: string | null
  last_name: string
  suffix?: string | null
}

interface Client {
  id: number
  name: ClientName
  auth_email: string
  email: string
  telephone: string
  referral_type: string
  referral_source: string
  referred_to_staff_id: number | null
  prior_counsel: string | null
  status: string
  ok_to_rehire: boolean
  ending_ar_balance: number
  notes: string | null
}

interface ConflictHit {
  id: number
  full_name: string
  role: string
  matter_caption: string
}

interface StaffOption {
  id: number
  name: { first_name: string; last_name: string }
}

function fullName(c: Client) {
  return `${c.name.first_name} ${c.name.last_name}`
}

const REFERRAL_TYPES = ['attorney', 'former client', 'search', 'ai', 'other']
const CLIENT_STATUSES = ['prospect', 'pending_conflict_check', 'conflict_flagged', 'active', 'inactive']

const STATUS_COLOR: Record<string, string> = {
  active:                 'bg-green-100 text-green-800',
  inactive:               'bg-gray-100 text-gray-600',
  prospect:               'bg-blue-100 text-blue-800',
  pending_conflict_check: 'bg-amber-100 text-amber-800',
  conflict_flagged:       'bg-red-100 text-red-800',
}

export default function ClientsPage() {
  const [clients, setClients]   = useState<Client[]>([])
  const [staff, setStaff]       = useState<StaffOption[]>([])
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState<string | null>(null)
  const [search, setSearch]     = useState('')

  // Conflict check
  const [ccName, setCcName]         = useState('')
  const [ccOpposing, setCcOpposing] = useState('')
  const [checking, setChecking]     = useState(false)
  const [ccHits, setCcHits]         = useState<ConflictHit[] | null>(null)
  const [ccError, setCcError]       = useState<string | null>(null)

  // Create form
  const [showCreate, setShowCreate]             = useState(false)
  const [newFirst, setNewFirst]                 = useState('')
  const [newLast, setNewLast]                   = useState('')
  const [newAuthEmail, setNewAuthEmail]         = useState('')
  const [newEmail, setNewEmail]                 = useState('')
  const [newTelephone, setNewTelephone]         = useState('')
  const [newReferralType, setNewReferralType]   = useState('')
  const [newReferralSource, setNewReferralSource] = useState('')
  const [newReferredTo, setNewReferredTo]       = useState<number | ''>('')
  const [newPriorCounsel, setNewPriorCounsel]   = useState('')
  const [newNotes, setNewNotes]                 = useState('')
  const [creating, setCreating]                 = useState(false)
  const [createError, setCreateError]           = useState<string | null>(null)

  // Edit form
  const [editId, setEditId]                     = useState<number | null>(null)
  const [edFirst, setEdFirst]                   = useState('')
  const [edLast, setEdLast]                     = useState('')
  const [edAuthEmail, setEdAuthEmail]           = useState('')
  const [edEmail, setEdEmail]                   = useState('')
  const [edTelephone, setEdTelephone]           = useState('')
  const [edReferralType, setEdReferralType]     = useState('')
  const [edReferralSource, setEdReferralSource] = useState('')
  const [edReferredTo, setEdReferredTo]         = useState<number | ''>('')
  const [edPriorCounsel, setEdPriorCounsel]     = useState('')
  const [edStatus, setEdStatus]                 = useState('')
  const [edOkToRehire, setEdOkToRehire]         = useState(true)
  const [edArBalance, setEdArBalance]           = useState('')
  const [edNotes, setEdNotes]                   = useState('')
  const [saving, setSaving]                     = useState(false)
  const [editError, setEditError]               = useState<string | null>(null)

  useEffect(() => {
    getClients()
      .then((data: unknown) => setClients(data as Client[]))
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))

    getStaff()
      .then((data: unknown) => setStaff(data as StaffOption[]))
      .catch(console.error)
  }, [])

  const visible = clients.filter(c =>
    !search || fullName(c).toLowerCase().includes(search.toLowerCase()) ||
    c.email.toLowerCase().includes(search.toLowerCase())
  )

  function openEdit(c: Client) {
    if (editId === c.id) { setEditId(null); return }
    setEditId(c.id)
    setEdFirst(c.name.first_name)
    setEdLast(c.name.last_name)
    setEdAuthEmail(c.auth_email)
    setEdEmail(c.email)
    setEdTelephone(c.telephone)
    setEdReferralType(c.referral_type)
    setEdReferralSource(c.referral_source)
    setEdReferredTo(c.referred_to_staff_id ?? '')
    setEdPriorCounsel(c.prior_counsel ?? '')
    setEdStatus(c.status)
    setEdOkToRehire(c.ok_to_rehire)
    setEdArBalance(String(c.ending_ar_balance))
    setEdNotes(c.notes ?? '')
    setEditError(null)
  }

  async function handleSaveEdit(e: FormEvent) {
    e.preventDefault()
    if (editId === null) return
    setSaving(true)
    setEditError(null)
    try {
      const payload: Record<string, unknown> = {
        name: { first_name: edFirst.trim(), last_name: edLast.trim() },
        auth_email: edAuthEmail.trim(),
        email: edEmail.trim(),
        telephone: edTelephone.trim(),
        referral_type: edReferralType,
        referral_source: edReferralSource.trim(),
        referred_to_staff_id: edReferredTo || null,
        prior_counsel: edPriorCounsel.trim() || null,
        status: edStatus,
        ok_to_rehire: edOkToRehire,
        ending_ar_balance: parseFloat(edArBalance) || 0,
        notes: edNotes.trim() || null,
      }
      const updated = await updateClient(editId, payload) as Client
      setClients(prev => prev.map(c => c.id === editId ? updated : c))
      setEditId(null)
    } catch (err) {
      setEditError(err instanceof Error ? err.message : 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  async function handleConflictCheck(e: FormEvent) {
    e.preventDefault()
    if (!ccName.trim()) return
    setChecking(true); setCcHits(null); setCcError(null)
    const opposingNames = ccOpposing.split(',').map(s => s.trim()).filter(Boolean)
    try {
      const result = await conflictCheck(ccName.trim(), opposingNames)
      setCcHits((result as { hits: ConflictHit[] }).hits ?? [])
    } catch (err) {
      setCcError(err instanceof Error ? err.message : 'Check failed')
    } finally { setChecking(false) }
  }

  async function handleCreateClient(e: FormEvent) {
    e.preventDefault()
    if (!newFirst.trim() || !newLast.trim() || !newAuthEmail.trim() || !newEmail.trim() || !newTelephone.trim() || !newReferralType || !newReferralSource.trim()) return
    setCreating(true); setCreateError(null)
    try {
      const payload: ClientCreatePayload = {
        name: { first_name: newFirst.trim(), last_name: newLast.trim() },
        auth_email: newAuthEmail.trim(),
        email: newEmail.trim(),
        telephone: newTelephone.trim(),
        referral_type: newReferralType,
        referral_source: newReferralSource.trim(),
        referred_to_staff_id: newReferredTo || null,
        prior_counsel: newPriorCounsel.trim() || undefined,
        notes: newNotes.trim() || undefined,
      }
      const created = await createClient(payload)
      setClients(prev => [created as Client, ...prev])
      setShowCreate(false)
      setNewFirst(''); setNewLast(''); setNewAuthEmail(''); setNewEmail(''); setNewTelephone('')
      setNewReferralType(''); setNewReferralSource(''); setNewReferredTo(''); setNewPriorCounsel(''); setNewNotes('')
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'Failed to create client')
    } finally { setCreating(false) }
  }

  return (
    <div className="px-6 py-8 max-w-5xl mx-auto">
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="font-display text-3xl text-navy">Clients</h1>
          <p className="text-text-secondary mt-1">{clients.length} total</p>
        </div>
        <button className="btn-primary" onClick={() => { setShowCreate(s => !s); setEditId(null) }}>
          {showCreate ? 'Cancel' : 'New client'}
        </button>
      </div>

      {/* Create client form */}
      {showCreate && (
        <div className="card p-5 mb-6">
          <h2 className="font-semibold text-navy mb-3">New client</h2>
          <form onSubmit={handleCreateClient} className="space-y-3">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="label">First name *</label>
                <input className="input mt-1" value={newFirst} onChange={e => setNewFirst(e.target.value)} />
              </div>
              <div>
                <label className="label">Last name *</label>
                <input className="input mt-1" value={newLast} onChange={e => setNewLast(e.target.value)} />
              </div>
            </div>
            <div>
              <label className="label">Login email (for client portal) *</label>
              <input className="input mt-1" type="email" value={newAuthEmail} onChange={e => setNewAuthEmail(e.target.value)} placeholder="The email the client will use to sign in" />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="label">Contact email *</label>
                <input className="input mt-1" type="email" value={newEmail} onChange={e => setNewEmail(e.target.value)} />
              </div>
              <div>
                <label className="label">Telephone *</label>
                <input className="input mt-1" type="tel" value={newTelephone} onChange={e => setNewTelephone(e.target.value)} />
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <div>
                <label className="label">Referral type *</label>
                <select className="input mt-1" value={newReferralType} onChange={e => setNewReferralType(e.target.value)}>
                  <option value="">— select —</option>
                  {REFERRAL_TYPES.map(t => <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>)}
                </select>
              </div>
              <div>
                <label className="label">Referral source *</label>
                <input className="input mt-1" value={newReferralSource} onChange={e => setNewReferralSource(e.target.value)} placeholder="Name of referring attorney, client, etc." />
              </div>
              <div>
                <label className="label">Referred to</label>
                <select className="input mt-1" value={newReferredTo} onChange={e => setNewReferredTo(e.target.value ? Number(e.target.value) : '')}>
                  <option value="">Firm (general)</option>
                  {staff.map(s => <option key={s.id} value={s.id}>{s.name.first_name} {s.name.last_name}</option>)}
                </select>
              </div>
            </div>
            <div>
              <label className="label">Prior counsel</label>
              <input className="input mt-1" value={newPriorCounsel} onChange={e => setNewPriorCounsel(e.target.value)} placeholder="Name of prior attorney, if any" />
            </div>
            <div>
              <label className="label">Notes</label>
              <textarea className="input mt-1" rows={2} value={newNotes} onChange={e => setNewNotes(e.target.value)} />
            </div>
            {createError && <p className="text-sm text-red-600">{createError}</p>}
            <div className="flex gap-3">
              <button type="submit" className="btn-primary" disabled={creating || !newFirst.trim() || !newLast.trim() || !newAuthEmail.trim() || !newEmail.trim() || !newTelephone.trim() || !newReferralType || !newReferralSource.trim()}>
                {creating ? 'Creating…' : 'Create client'}
              </button>
              <button type="button" className="btn-secondary" onClick={() => setShowCreate(false)}>Cancel</button>
            </div>
          </form>
        </div>
      )}

      {/* Conflict check */}
      <div className="card p-5 mb-6">
        <h2 className="font-semibold text-navy mb-3">Conflict of interest check</h2>
        <form onSubmit={handleConflictCheck} className="space-y-3">
          <div>
            <label className="label">Prospective client name</label>
            <input className="input mt-1" placeholder="Jane Smith" value={ccName} onChange={e => setCcName(e.target.value)} />
          </div>
          <div>
            <label className="label">Opposing party names (comma-separated, optional)</label>
            <input className="input mt-1" placeholder="John Smith, ABC Corp" value={ccOpposing} onChange={e => setCcOpposing(e.target.value)} />
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
                <svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" /></svg>
                No conflicts found.
              </div>
            ) : (
              <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3">
                <p className="text-sm font-semibold text-red-700 mb-2">{ccHits.length} potential conflict{ccHits.length !== 1 ? 's' : ''} — attorney review required</p>
                <ul className="space-y-1">
                  {ccHits.map((h, i) => (
                    <li key={i} className="text-sm text-red-800"><span className="font-medium">{h.full_name}</span> · {h.role} · {h.matter_caption}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Client list */}
      <div className="mb-4">
        <input className="input w-full md:w-72" placeholder="Search clients…" value={search} onChange={e => setSearch(e.target.value)} />
      </div>

      <div className="card overflow-hidden">
        {loading && <div className="px-5 py-10 text-center text-text-secondary text-sm">Loading…</div>}
        {error && <div className="px-5 py-10 text-center text-red-600 text-sm">{error}</div>}
        {!loading && !error && visible.length === 0 && <div className="px-5 py-10 text-center text-text-secondary text-sm">No clients found.</div>}
        {!loading && !error && visible.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-off-white">
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Name</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide hidden md:table-cell">Email</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide hidden lg:table-cell">Telephone</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Status</th>
              </tr>
            </thead>
            <tbody>
              {visible.map(c => (
                <>
                  <tr
                    key={c.id}
                    onClick={() => { openEdit(c); setShowCreate(false) }}
                    className="border-b border-border last:border-0 hover:bg-off-white/60 transition-colors cursor-pointer"
                  >
                    <td className="px-5 py-3 font-medium text-navy">{fullName(c)}</td>
                    <td className="px-5 py-3 text-text-secondary hidden md:table-cell">{c.email}</td>
                    <td className="px-5 py-3 text-text-secondary hidden lg:table-cell">{c.telephone}</td>
                    <td className="px-5 py-3">
                      <span className={`text-xs rounded-full px-2.5 py-1 font-medium capitalize ${STATUS_COLOR[c.status] ?? 'bg-gray-100 text-gray-600'}`}>
                        {c.status.replace(/_/g, ' ')}
                      </span>
                    </td>
                  </tr>

                  {/* Inline edit form */}
                  {editId === c.id && (
                    <tr key={`edit-${c.id}`}>
                      <td colSpan={4} className="px-5 py-4 bg-off-white/50 border-b border-border">
                        <form onSubmit={handleSaveEdit} className="space-y-3 max-w-3xl" onClick={e => e.stopPropagation()}>
                          <h3 className="font-semibold text-navy text-sm mb-1">Edit client</h3>
                          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                            <div>
                              <label className="label">First name</label>
                              <input className="input mt-1" value={edFirst} onChange={e => setEdFirst(e.target.value)} />
                            </div>
                            <div>
                              <label className="label">Last name</label>
                              <input className="input mt-1" value={edLast} onChange={e => setEdLast(e.target.value)} />
                            </div>
                          </div>
                          <div>
                            <label className="label">Login email (client portal)</label>
                            <input className="input mt-1" type="email" value={edAuthEmail} onChange={e => setEdAuthEmail(e.target.value)} />
                          </div>
                          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                            <div>
                              <label className="label">Contact email</label>
                              <input className="input mt-1" type="email" value={edEmail} onChange={e => setEdEmail(e.target.value)} />
                            </div>
                            <div>
                              <label className="label">Telephone</label>
                              <input className="input mt-1" type="tel" value={edTelephone} onChange={e => setEdTelephone(e.target.value)} />
                            </div>
                          </div>
                          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                            <div>
                              <label className="label">Referral type</label>
                              <select className="input mt-1" value={edReferralType} onChange={e => setEdReferralType(e.target.value)}>
                                {REFERRAL_TYPES.map(t => <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>)}
                              </select>
                            </div>
                            <div>
                              <label className="label">Referral source</label>
                              <input className="input mt-1" value={edReferralSource} onChange={e => setEdReferralSource(e.target.value)} />
                            </div>
                            <div>
                              <label className="label">Referred to</label>
                              <select className="input mt-1" value={edReferredTo} onChange={e => setEdReferredTo(e.target.value ? Number(e.target.value) : '')}>
                                <option value="">Firm (general)</option>
                                {staff.map(s => <option key={s.id} value={s.id}>{s.name.first_name} {s.name.last_name}</option>)}
                              </select>
                            </div>
                          </div>
                          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                            <div>
                              <label className="label">Status</label>
                              <select className="input mt-1" value={edStatus} onChange={e => setEdStatus(e.target.value)}>
                                {CLIENT_STATUSES.map(s => <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>)}
                              </select>
                            </div>
                            <div>
                              <label className="label">Prior counsel</label>
                              <input className="input mt-1" value={edPriorCounsel} onChange={e => setEdPriorCounsel(e.target.value)} />
                            </div>
                            <div>
                              <label className="label">Ending A/R balance</label>
                              <input className="input mt-1" type="number" step="0.01" value={edArBalance} onChange={e => setEdArBalance(e.target.value)} />
                            </div>
                          </div>
                          <div className="flex items-center gap-2">
                            <input type="checkbox" id={`rehire-${c.id}`} checked={edOkToRehire} onChange={e => setEdOkToRehire(e.target.checked)} className="rounded border-border" />
                            <label htmlFor={`rehire-${c.id}`} className="text-sm text-navy">OK to rehire</label>
                          </div>
                          <div>
                            <label className="label">Notes</label>
                            <textarea className="input mt-1" rows={2} value={edNotes} onChange={e => setEdNotes(e.target.value)} />
                          </div>
                          {editError && <p className="text-sm text-red-600">{editError}</p>}
                          <div className="flex gap-3">
                            <button type="submit" className="btn-primary" disabled={saving}>{saving ? 'Saving…' : 'Save changes'}</button>
                            <button type="button" className="btn-secondary" onClick={() => setEditId(null)}>Cancel</button>
                          </div>
                        </form>
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
