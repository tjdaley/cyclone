import { useEffect, useRef, useState, FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext'
import {
  getMatters, createMatter, getClients, previewMatterIntake,
} from '../../lib/api'
import type {
  Matter, MatterCreatePayload, Client, MatterIntakePreview,
} from '../../types'
import MatterIntakePanel from './MatterIntakePanel'

const MATTER_TYPES = [
  'divorce', 'child_custody', 'modification', 'enforcement',
  'cps', 'probate', 'estate_planning', 'civil', 'other',
]

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
  const navigate = useNavigate()

  // Matter intake from a dropped pleading
  const [intakePreview, setIntakePreview] = useState<MatterIntakePreview | null>(null)
  const [intakeBusy, setIntakeBusy]       = useState(false)
  const [intakeError, setIntakeError]     = useState<string | null>(null)
  const [intakeStatus, setIntakeStatus]   = useState('')
  const [dragOver, setDragOver]           = useState(false)
  const intakeInputRef = useRef<HTMLInputElement>(null)

  async function handleIntakeFile(file: File) {
    if (file.type !== 'application/pdf') {
      setIntakeError('Only PDF files are accepted')
      return
    }
    setIntakeBusy(true); setIntakeError(null); setIntakeStatus('Uploading…')
    try {
      // Extraction runs in the worker; this reports progress while it does.
      const preview = await previewMatterIntake(file, (status, seconds) => {
        setIntakeStatus(
          status === 'queued' ? 'Queued for extraction…'
          : status === 'running' ? `Reading the pleading… ${seconds}s`
          : status === 'succeeded' ? 'Done'
          : 'Extraction failed',
        )
      })
      setIntakePreview(preview)
    } catch (e) {
      setIntakeError(e instanceof Error ? e.message : 'Could not read that pleading')
    } finally {
      setIntakeBusy(false); setIntakeStatus('')
    }
  }

  const [matters, setMatters]   = useState<Matter[]>([])
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState<string | null>(null)
  const [filter, setFilter]     = useState<'all' | 'active' | 'closed'>('active')
  const [search, setSearch]     = useState('')

  const [clients, setClients]   = useState<Client[]>([])

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
  const [newBillingId, setNewBillingId] = useState('')
  const [newNotes, setNewNotes]         = useState('')
  const [creating, setCreating]         = useState(false)
  const [createError, setCreateError]   = useState<string | null>(null)


  useEffect(() => {
    getMatters()
      .then(setMatters)
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
    getClients().then(setClients).catch(console.error)
  }, [])

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
        billing_id: newBillingId.trim() || undefined,
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

  // Intake takes over the page: reviewing a case style has nothing to do with
  // browsing the list, and the two side by side would be noise.
  if (intakePreview) {
    return (
      <div className="px-6 py-8 max-w-5xl mx-auto">
        <MatterIntakePanel preview={intakePreview} onCancel={() => setIntakePreview(null)} />
      </div>
    )
  }

  return (
    <div className="px-6 py-8 max-w-5xl mx-auto">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-8">
        <div>
          <h1 className="font-display text-3xl text-navy">Matters</h1>
          <p className="text-text-secondary mt-1">{matters.length} total</p>
        </div>
        {canCreate && (
          <button className="btn-primary" onClick={() => { setShowCreate(s => !s) }}>
            {showCreate ? 'Cancel' : 'New matter'}
          </button>
        )}
      </div>

      {/* Open a matter by dropping a pleading. The button matters as much as the
          drop zone — dragging is not available on a tablet. */}
      {canCreate && (
        <div
          onDragOver={e => { e.preventDefault(); setDragOver(true) }}
          onDragLeave={e => { e.preventDefault(); setDragOver(false) }}
          onDrop={e => {
            e.preventDefault(); setDragOver(false)
            const f = e.dataTransfer.files[0]
            if (f) handleIntakeFile(f)
          }}
          className={`card mb-6 border-2 border-dashed text-center py-6 px-4 transition-colors ${
            dragOver ? 'border-navy bg-navy/5' : 'border-border'}`}
        >
          {intakeBusy ? (
            <p className="text-sm text-text-secondary">{intakeStatus || 'Reading the pleading…'}</p>
          ) : (
            <>
              <p className="text-sm text-text-secondary">
                Drop a filed pleading here to open a new matter from it
              </p>
              <button type="button" className="text-sm text-navy underline mt-1"
                onClick={() => intakeInputRef.current?.click()}>
                or choose a PDF
              </button>
              <input ref={intakeInputRef} type="file" accept="application/pdf" className="hidden"
                onChange={e => { const f = e.target.files?.[0]; if (f) handleIntakeFile(f); e.target.value = '' }} />
            </>
          )}
          {intakeError && <p className="text-sm text-red-600 mt-2">{intakeError}</p>}
        </div>
      )}

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
              <div>
                <label className="label">Billing ID</label>
                <input className="input mt-1" value={newBillingId} onChange={e => setNewBillingId(e.target.value)} placeholder="The matter's number in the billing system" />
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
                <th className="px-5 py-3 w-16"><span className="sr-only">Edit</span></th>
              </tr>
            </thead>
            <tbody>
              {visible.map(m => {
                const closed = isClosed(m.status)
                return (
                  <>
                    <tr
                      key={m.id}
                      onClick={() => navigate(`/app/matters/${m.id}`)}
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
                      {/* Row opens the matter; Edit opens its record. */}
                      <td className="px-5 py-3 text-right">
                        <button
                          type="button"
                          className="text-xs text-navy underline"
                          onClick={e => {
                            e.stopPropagation()
                            navigate(`/app/matters/${m.id}/edit`)
                          }}
                        >
                          Edit
                        </button>
                      </td>
                    </tr>

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
