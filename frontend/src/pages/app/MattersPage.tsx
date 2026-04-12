import { useEffect, useState, FormEvent } from 'react'
import { useAuth } from '../../context/AuthContext'
import {
  getMatters, createMatter, updateMatter, getClients, getStaff,
  getRateOverrides, setRateOverride, deleteRateOverride,
} from '../../lib/api'
import type { Matter, MatterCreatePayload, RateOverride, Client, Staff } from '../../types'

const MATTER_TYPES = [
  'divorce', 'child_custody', 'modification', 'enforcement',
  'cps', 'probate', 'estate_planning', 'civil', 'other',
]
const MATTER_STATUSES = ['intake', 'conflict_review', 'active', 'closed', 'archived']

const STATUS_COLOR: Record<string, string> = {
  active:          'bg-green-100 text-green-800',
  intake:          'bg-blue-100 text-blue-800',
  conflict_review: 'bg-amber-100 text-amber-800',
  closed:          'bg-gray-100 text-gray-600',
  archived:        'bg-gray-100 text-gray-500',
}

function isClosed(status: string) {
  return status === 'closed' || status === 'archived'
}

function sortMatters(a: Matter, b: Matter) {
  const aClosed = isClosed(a.status) ? 1 : 0
  const bClosed = isClosed(b.status) ? 1 : 0
  if (aClosed !== bClosed) return aClosed - bClosed
  const aName = (a.short_name ?? a.matter_name).toLowerCase()
  const bName = (b.short_name ?? b.matter_name).toLowerCase()
  return aName.localeCompare(bName)
}

export default function MattersPage() {
  const { profile } = useAuth()
  const [matters, setMatters]   = useState<Matter[]>([])
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState<string | null>(null)
  const [filter, setFilter]     = useState<'all' | 'active' | 'closed'>('active')
  const [search, setSearch]     = useState('')

  const [clients, setClients]   = useState<Client[]>([])
  const [staff, setStaff]       = useState<Staff[]>([])

  // Create form
  const [showCreate, setShowCreate]     = useState(false)
  const [newClientId, setNewClientId]   = useState<number | ''>('')
  const [newShortName, setNewShortName] = useState('')
  const [newName, setNewName]           = useState('')
  const [newType, setNewType]           = useState('')
  const [newProBono, setNewProBono]     = useState(false)
  const [newState, setNewState]         = useState('Texas')
  const [newCounty, setNewCounty]       = useState('')
  const [newCourtName, setNewCourtName] = useState('')
  const [newMatterNumber, setNewMatterNumber] = useState('')
  const [newNotes, setNewNotes]         = useState('')
  const [creating, setCreating]         = useState(false)
  const [createError, setCreateError]   = useState<string | null>(null)

  // Edit form
  const [editId, setEditId]               = useState<number | null>(null)
  const [edShortName, setEdShortName]     = useState('')
  const [edName, setEdName]               = useState('')
  const [edStatus, setEdStatus]           = useState('')
  const [edProBono, setEdProBono]         = useState(false)
  const [edState, setEdState]             = useState('')
  const [edCounty, setEdCounty]           = useState('')
  const [edCourtName, setEdCourtName]     = useState('')
  const [edMatterNumber, setEdMatterNumber] = useState('')
  const [edFeeDate, setEdFeeDate]         = useState('')
  const [edOpenedDate, setEdOpenedDate]   = useState('')
  const [edClosedDate, setEdClosedDate]   = useState('')
  const [edRetainer, setEdRetainer]       = useState('')
  const [edNotes, setEdNotes]             = useState('')
  const [saving, setSaving]               = useState(false)
  const [editError, setEditError]         = useState<string | null>(null)

  // Rate override panel (shown within edit)
  const [overrides, setOverrides]             = useState<RateOverride[]>([])
  const [loadingOverrides, setLoadingOverrides] = useState(false)
  const [ovStaffId, setOvStaffId]             = useState<number | ''>('')
  const [ovRate, setOvRate]                   = useState('')
  const [savingOv, setSavingOv]               = useState(false)
  const [ovError, setOvError]                 = useState<string | null>(null)

  useEffect(() => {
    getMatters()
      .then(setMatters)
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
    getClients().then(setClients).catch(console.error)
    getStaff().then(setStaff).catch(console.error)
  }, [])

  useEffect(() => {
    if (!editId) { setOverrides([]); return }
    setLoadingOverrides(true); setOvError(null)
    getRateOverrides(editId)
      .then(setOverrides)
      .catch(console.error)
      .finally(() => setLoadingOverrides(false))
  }, [editId])

  const visible = matters
    .filter(m => {
      if (filter === 'active' && isClosed(m.status)) return false
      if (filter === 'closed' && !isClosed(m.status)) return false
      if (search) {
        const q = search.toLowerCase()
        const sn = (m.short_name ?? '').toLowerCase()
        const mn = m.matter_name.toLowerCase()
        if (!sn.includes(q) && !mn.includes(q)) return false
      }
      return true
    })
    .sort(sortMatters)

  function generateShortName(clientId: number | '', matterType: string) {
    if (!clientId || !matterType) return ''
    const c = clients.find(cl => cl.id === Number(clientId))
    if (!c) return ''
    return `${c.name.last_name.toUpperCase()} - ${matterType.replace(/_/g, ' ')} - ${new Date().getFullYear()}`
  }

  const canCreate = profile?.role === 'attorney' || profile?.role === 'admin'
  const isAdmin = profile?.role === 'admin'

  function openEdit(m: Matter) {
    if (editId === m.id) { setEditId(null); return }
    setEditId(m.id)
    setEdShortName(m.short_name ?? '')
    setEdName(m.matter_name)
    setEdStatus(m.status)
    setEdProBono(m.is_pro_bono)
    setEdState(m.state)
    setEdCounty(m.county)
    setEdCourtName(m.court_name ?? '')
    setEdMatterNumber(m.matter_number ?? '')
    setEdFeeDate(m.fee_agreement_signed_date ?? '')
    setEdOpenedDate(m.opened_date ?? '')
    setEdClosedDate(m.closed_date ?? '')
    setEdRetainer(String(m.retainer_amount))
    setEdNotes(m.notes ?? '')
    setEditError(null)
  }

  async function handleSaveEdit(e: FormEvent) {
    e.preventDefault()
    if (editId === null) return
    setSaving(true); setEditError(null)
    try {
      const payload: Record<string, unknown> = {
        short_name: edShortName.trim() || null,
        matter_name: edName.trim(),
        status: edStatus,
        is_pro_bono: edProBono,
        state: edState.trim(),
        county: edCounty.trim(),
        court_name: edCourtName.trim() || null,
        matter_number: edMatterNumber.trim() || null,
        fee_agreement_signed_date: edFeeDate || null,
        opened_date: edOpenedDate || null,
        closed_date: edClosedDate || null,
        retainer_amount: parseFloat(edRetainer) || 0,
        notes: edNotes.trim() || null,
      }
      const updated = await updateMatter(editId, payload)
      setMatters(prev => prev.map(m => m.id === editId ? updated : m))
      setEditId(null)
    } catch (err) {
      setEditError(err instanceof Error ? err.message : 'Failed to save')
    } finally { setSaving(false) }
  }

  async function handleCreateMatter(e: FormEvent) {
    e.preventDefault()
    if (!newClientId || !newName.trim() || !newType || !newCounty.trim()) return
    setCreating(true); setCreateError(null)
    try {
      const payload: MatterCreatePayload = {
        client_id: Number(newClientId),
        short_name: newShortName.trim() || undefined,
        matter_name: newName.trim(),
        matter_type: newType,
        is_pro_bono: newProBono,
        state: newState.trim() || 'Texas',
        county: newCounty.trim(),
        court_name: newCourtName.trim() || undefined,
        matter_number: newMatterNumber.trim() || undefined,
        notes: newNotes.trim() || undefined,
      }
      const created = await createMatter(payload)
      setMatters(prev => [created, ...prev])
      setShowCreate(false)
      setNewClientId(''); setNewShortName(''); setNewName(''); setNewType(''); setNewProBono(false)
      setNewState('Texas'); setNewCounty(''); setNewCourtName(''); setNewMatterNumber(''); setNewNotes('')
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'Failed to create matter')
    } finally { setCreating(false) }
  }

  async function handleSetOverride(e: FormEvent) {
    e.preventDefault()
    if (!editId || !ovStaffId || !ovRate) return
    setSavingOv(true); setOvError(null)
    try {
      const saved = await setRateOverride(editId, Number(ovStaffId), parseFloat(ovRate))
      setOverrides(prev => {
        const idx = prev.findIndex(o => o.staff_id === Number(ovStaffId))
        if (idx >= 0) { const next = [...prev]; next[idx] = saved; return next }
        return [...prev, saved]
      })
      setOvStaffId(''); setOvRate('')
    } catch (err) { setOvError(err instanceof Error ? err.message : 'Failed to save override') }
    finally { setSavingOv(false) }
  }

  async function handleDeleteOverride(overrideId: number) {
    if (!editId) return
    try {
      await deleteRateOverride(editId, overrideId)
      setOverrides(prev => prev.filter(o => o.id !== overrideId))
    } catch (err) { setOvError(err instanceof Error ? err.message : 'Failed to delete override') }
  }

  function staffDisplayName(staffId: number) {
    const s = staff.find(st => st.id === staffId)
    return s ? `${s.name.first_name} ${s.name.last_name}` : `Staff #${staffId}`
  }

  return (
    <div className="px-6 py-8 max-w-5xl mx-auto">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-8">
        <div>
          <h1 className="font-display text-3xl text-navy">Matters</h1>
          <p className="text-text-secondary mt-1">{matters.length} total</p>
        </div>
        {canCreate && (
          <button className="btn-primary" onClick={() => { setShowCreate(s => !s); setEditId(null) }}>
            {showCreate ? 'Cancel' : 'New matter'}
          </button>
        )}
      </div>

      {/* Create matter form */}
      {showCreate && (
        <div className="card p-5 mb-6">
          <h2 className="font-semibold text-navy mb-3">New matter</h2>
          <form onSubmit={handleCreateMatter} className="space-y-3">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="label">Client *</label>
                <select className="input mt-1" value={newClientId} onChange={e => {
                  const cid = e.target.value ? Number(e.target.value) : '' as const
                  setNewClientId(cid)
                  setNewShortName(generateShortName(cid, newType))
                }}>
                  <option value="">— select client —</option>
                  {clients.map(c => <option key={c.id} value={c.id}>{c.name.first_name} {c.name.last_name}</option>)}
                </select>
              </div>
              <div>
                <label className="label">Matter type *</label>
                <select className="input mt-1" value={newType} onChange={e => {
                  const mt = e.target.value; setNewType(mt)
                  setNewShortName(generateShortName(newClientId, mt))
                }}>
                  <option value="">— select type —</option>
                  {MATTER_TYPES.map(t => <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>)}
                </select>
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="label">Short name</label>
                <input className="input mt-1" value={newShortName} onChange={e => setNewShortName(e.target.value)} placeholder="Auto-generated from client + type + year" />
              </div>
              <div>
                <label className="label">Matter name *</label>
                <input className="input mt-1" value={newName} onChange={e => setNewName(e.target.value)} placeholder="e.g. Smith v. Smith — Divorce" />
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <div>
                <label className="label">State *</label>
                <input className="input mt-1" value={newState} onChange={e => setNewState(e.target.value)} />
              </div>
              <div>
                <label className="label">County *</label>
                <input className="input mt-1" value={newCounty} onChange={e => setNewCounty(e.target.value)} placeholder="e.g. Dallas" />
              </div>
              <div>
                <label className="label">Court name</label>
                <input className="input mt-1" value={newCourtName} onChange={e => setNewCourtName(e.target.value)} placeholder="e.g. 401st District Court" />
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="label">Matter number</label>
                <input className="input mt-1" value={newMatterNumber} onChange={e => setNewMatterNumber(e.target.value)} placeholder="Court-assigned case number" />
              </div>
              <div className="flex items-end pb-1">
                <div className="flex items-center gap-2">
                  <input type="checkbox" id="pro-bono" checked={newProBono} onChange={e => setNewProBono(e.target.checked)} className="rounded border-border" />
                  <label htmlFor="pro-bono" className="text-sm text-navy">Pro bono</label>
                </div>
              </div>
            </div>
            <div>
              <label className="label">Notes</label>
              <textarea className="input mt-1" rows={2} value={newNotes} onChange={e => setNewNotes(e.target.value)} />
            </div>
            {createError && <p className="text-sm text-red-600">{createError}</p>}
            <div className="flex gap-3">
              <button type="submit" className="btn-primary" disabled={creating || !newClientId || !newName.trim() || !newType || !newCounty.trim()}>
                {creating ? 'Creating…' : 'Create matter'}
              </button>
              <button type="button" className="btn-secondary" onClick={() => setShowCreate(false)}>Cancel</button>
            </div>
          </form>
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-3 mb-6">
        <input className="input flex-1" placeholder="Search matters…" value={search} onChange={e => setSearch(e.target.value)} />
        <div className="flex rounded-lg border border-border overflow-hidden text-sm">
          {(['all', 'active', 'closed'] as const).map(f => (
            <button key={f} onClick={() => setFilter(f)}
              className={`px-4 py-2 capitalize font-medium transition-colors ${filter === f ? 'bg-navy text-white' : 'bg-white text-text-secondary hover:bg-off-white'}`}>
              {f}
            </button>
          ))}
        </div>
      </div>

      <div className="card overflow-hidden">
        {loading && <div className="px-5 py-10 text-center text-text-secondary text-sm">Loading…</div>}
        {error && <div className="px-5 py-10 text-center text-red-600 text-sm">{error}</div>}
        {!loading && !error && visible.length === 0 && <div className="px-5 py-10 text-center text-text-secondary text-sm">No matters match your filter.</div>}
        {!loading && !error && visible.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-off-white">
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Matter</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide hidden md:table-cell">Type</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Status</th>
              </tr>
            </thead>
            <tbody>
              {visible.map(m => {
                const closed = isClosed(m.status)
                return (
                  <>
                    <tr
                      key={m.id}
                      onClick={() => { openEdit(m); setShowCreate(false) }}
                      className={`border-b border-border last:border-0 hover:bg-off-white/60 transition-colors cursor-pointer ${closed ? 'opacity-50' : ''}`}
                    >
                      <td className={`px-5 py-3 font-medium ${closed ? 'text-text-secondary' : 'text-navy'}`}>
                        {m.short_name ?? m.matter_name}
                        {m.is_pro_bono && <span className="ml-2 text-xs bg-purple-100 text-purple-700 rounded-full px-2 py-0.5">Pro bono</span>}
                      </td>
                      <td className="px-5 py-3 text-text-secondary hidden md:table-cell capitalize">{m.matter_type.replace(/_/g, ' ')}</td>
                      <td className="px-5 py-3">
                        <span className={`text-xs rounded-full px-2.5 py-1 font-medium capitalize ${STATUS_COLOR[m.status] ?? 'bg-gray-100 text-gray-600'}`}>
                          {m.status.replace(/_/g, ' ')}
                        </span>
                      </td>
                    </tr>

                    {/* Edit + rate overrides panel */}
                    {editId === m.id && (
                      <tr key={`edit-${m.id}`}>
                        <td colSpan={3} className="px-5 py-4 bg-off-white/50 border-b border-border">
                          <div className="space-y-6" onClick={e => e.stopPropagation()}>
                            {/* Edit form */}
                            <form onSubmit={handleSaveEdit} className="space-y-3 max-w-3xl">
                              <h3 className="font-semibold text-navy text-sm">Edit matter</h3>
                              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                                <div>
                                  <label className="label">Short name</label>
                                  <input className="input mt-1" value={edShortName} onChange={e => setEdShortName(e.target.value)} />
                                </div>
                                <div>
                                  <label className="label">Matter name</label>
                                  <input className="input mt-1" value={edName} onChange={e => setEdName(e.target.value)} />
                                </div>
                              </div>
                              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                                <div>
                                  <label className="label">Status</label>
                                  <select className="input mt-1" value={edStatus} onChange={e => setEdStatus(e.target.value)}>
                                    {MATTER_STATUSES.map(s => <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>)}
                                  </select>
                                </div>
                                <div>
                                  <label className="label">State</label>
                                  <input className="input mt-1" value={edState} onChange={e => setEdState(e.target.value)} />
                                </div>
                                <div>
                                  <label className="label">County</label>
                                  <input className="input mt-1" value={edCounty} onChange={e => setEdCounty(e.target.value)} />
                                </div>
                              </div>
                              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                                <div>
                                  <label className="label">Court name</label>
                                  <input className="input mt-1" value={edCourtName} onChange={e => setEdCourtName(e.target.value)} />
                                </div>
                                <div>
                                  <label className="label">Matter number</label>
                                  <input className="input mt-1" value={edMatterNumber} onChange={e => setEdMatterNumber(e.target.value)} />
                                </div>
                                <div>
                                  <label className="label">Retainer ($)</label>
                                  <input className="input mt-1" type="number" step="0.01" min="0" value={edRetainer} onChange={e => setEdRetainer(e.target.value)} />
                                </div>
                              </div>
                              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                                <div>
                                  <label className="label">Fee agreement signed</label>
                                  <input className="input mt-1" type="date" value={edFeeDate} onChange={e => setEdFeeDate(e.target.value)} />
                                </div>
                                <div>
                                  <label className="label">Opened date</label>
                                  <input className="input mt-1" type="date" value={edOpenedDate} onChange={e => setEdOpenedDate(e.target.value)} />
                                </div>
                                <div>
                                  <label className="label">Closed date</label>
                                  <input className="input mt-1" type="date" value={edClosedDate} onChange={e => setEdClosedDate(e.target.value)} />
                                </div>
                              </div>
                              <div className="flex items-center gap-2">
                                <input type="checkbox" id={`pb-${m.id}`} checked={edProBono} onChange={e => setEdProBono(e.target.checked)} className="rounded border-border" />
                                <label htmlFor={`pb-${m.id}`} className="text-sm text-navy">Pro bono</label>
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

                            {/* Rate overrides */}
                            <div className="max-w-lg border-t border-border pt-4">
                              <h3 className="font-semibold text-navy text-sm mb-3">Rate overrides</h3>
                              {loadingOverrides && <p className="text-sm text-text-secondary">Loading…</p>}
                              {!loadingOverrides && overrides.length === 0 && <p className="text-sm text-text-secondary mb-3">No rate overrides set. Default rates apply.</p>}
                              {!loadingOverrides && overrides.length > 0 && (
                                <table className="w-full text-sm mb-4">
                                  <thead>
                                    <tr className="border-b border-border">
                                      <th className="text-left py-1.5 text-xs font-semibold text-text-secondary uppercase tracking-wide">Staff</th>
                                      <th className="text-left py-1.5 text-xs font-semibold text-text-secondary uppercase tracking-wide">Rate</th>
                                      {isAdmin && <th />}
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {overrides.map(o => (
                                      <tr key={o.id} className="border-b border-border last:border-0">
                                        <td className="py-1.5 text-navy">{staffDisplayName(o.staff_id)}</td>
                                        <td className="py-1.5 text-navy">${o.rate.toFixed(0)}/hr</td>
                                        {isAdmin && (
                                          <td className="py-1.5 text-right">
                                            <button onClick={() => handleDeleteOverride(o.id)} className="text-xs text-red-600 hover:underline">Remove</button>
                                          </td>
                                        )}
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              )}
                              {ovError && <p className="text-sm text-red-600 mb-2">{ovError}</p>}
                              {isAdmin && (
                                <form onSubmit={handleSetOverride} className="flex gap-2 items-end">
                                  <div className="flex-1">
                                    <label className="label">Staff member</label>
                                    <select className="input mt-1" value={ovStaffId} onChange={e => setOvStaffId(e.target.value ? Number(e.target.value) : '')}>
                                      <option value="">— select —</option>
                                      {staff.filter(s => s.role === 'attorney' || s.role === 'paralegal').map(s => (
                                        <option key={s.id} value={s.id}>
                                          {s.name.first_name} {s.name.last_name} {s.default_billing_rate != null ? `($${s.default_billing_rate}/hr default)` : ''}
                                        </option>
                                      ))}
                                    </select>
                                  </div>
                                  <div className="w-28">
                                    <label className="label">Rate ($/hr)</label>
                                    <input className="input mt-1" type="number" min="0" step="0.01" placeholder="250" value={ovRate} onChange={e => setOvRate(e.target.value)} />
                                  </div>
                                  <button type="submit" className="btn-primary" disabled={savingOv || !ovStaffId || !ovRate}>
                                    {savingOv ? 'Saving…' : 'Set'}
                                  </button>
                                </form>
                              )}
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
