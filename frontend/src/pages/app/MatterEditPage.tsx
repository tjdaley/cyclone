import { useEffect, useState, FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext'
import {
  getMatter, updateMatter, getStaff,
  getRateOverrides, setRateOverride, deleteRateOverride,
} from '../../lib/api'
import MatterHeader from '../../components/MatterHeader'
import type { Matter, RateOverride, Staff } from '../../types'

const MATTER_STATUSES = ['intake', 'conflict_review', 'active', 'closed', 'archived']

function isClosed(status: string) {
  return status === 'closed' || status === 'archived'
}

/**
 * A matter's own record: name, status, court, numbers, dates, retainer, and the
 * per-staff rate overrides. Its own page rather than a row that unfolds inside
 * the matter list, so it reads as the one matter being edited.
 *
 * Saving or cancelling goes back to wherever the person came from — the matter
 * dashboard or the list.
 */
export default function MatterEditPage() {
  const { matterId: param } = useParams<{ matterId: string }>()
  const matterId = Number(param)
  const navigate = useNavigate()
  const { profile } = useAuth()
  const isAdmin = profile?.role === 'admin'

  const [matter, setMatter] = useState<Matter | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [staff, setStaff] = useState<Staff[]>([])

  const [shortName, setShortName]     = useState('')
  const [name, setName]               = useState('')
  const [status, setStatus]           = useState('')
  const [proBono, setProBono]         = useState(false)
  const [state, setState]             = useState('')
  const [county, setCounty]           = useState('')
  const [courtName, setCourtName]     = useState('')
  const [matterNumber, setMatterNumber] = useState('')
  const [billingId, setBillingId]     = useState('')
  const [feeDate, setFeeDate]         = useState('')
  const [openedDate, setOpenedDate]   = useState('')
  const [closedDate, setClosedDate]   = useState('')
  const [retainer, setRetainer]       = useState('')
  const [notes, setNotes]             = useState('')
  const [saving, setSaving]           = useState(false)
  const [saveError, setSaveError]     = useState<string | null>(null)
  const [closePrompt, setClosePrompt] = useState(false)

  const [overrides, setOverrides]               = useState<RateOverride[]>([])
  const [loadingOverrides, setLoadingOverrides] = useState(true)
  const [ovStaffId, setOvStaffId]               = useState<number | ''>('')
  const [ovRate, setOvRate]                     = useState('')
  const [savingOv, setSavingOv]                 = useState(false)
  const [ovError, setOvError]                   = useState<string | null>(null)

  useEffect(() => {
    if (!matterId) return
    getMatter(matterId)
      .then(m => {
        setMatter(m)
        setShortName(m.short_name ?? '')
        setName(m.matter_name)
        setStatus(m.status)
        setProBono(m.is_pro_bono)
        setState(m.state)
        setCounty(m.county)
        setCourtName(m.court_name ?? '')
        setMatterNumber(m.matter_number ?? '')
        setBillingId(m.billing_id ?? '')
        setFeeDate(m.fee_agreement_signed_date ?? '')
        setOpenedDate(m.opened_date ?? '')
        setClosedDate(m.closed_date ?? '')
        setRetainer(String(m.retainer_amount))
        setNotes(m.notes ?? '')
      })
      .catch(e => setLoadError(e instanceof Error ? e.message : 'Could not load the matter'))
    getRateOverrides(matterId)
      .then(setOverrides)
      .catch(console.error)
      .finally(() => setLoadingOverrides(false))
    getStaff().then(setStaff).catch(console.error)
  }, [matterId])

  /** Back to where the person came from; the dashboard when there is nowhere to go back to. */
  function leave() {
    if (window.history.length > 1) navigate(-1)
    else navigate(`/app/matters/${matterId}`)
  }

  function handleSave(e: FormEvent) {
    e.preventDefault()
    if (!matter) return
    // Only on the way INTO closed, and only if there is still something to turn
    // off: re-saving a closed matter's notes must not ask again.
    if (isClosed(status) && !isClosed(matter.status) && (matter.client_portal_enabled ?? true)) {
      setClosePrompt(true)
      return
    }
    void save()
  }

  async function save(extra: Record<string, unknown> = {}) {
    setClosePrompt(false)
    setSaving(true); setSaveError(null)
    try {
      await updateMatter(matterId, {
        short_name: shortName.trim() || null,
        matter_name: name.trim(),
        status,
        is_pro_bono: proBono,
        state: state.trim(),
        county: county.trim(),
        court_name: courtName.trim() || null,
        matter_number: matterNumber.trim() || null,
        billing_id: billingId.trim() || null,
        fee_agreement_signed_date: feeDate || null,
        opened_date: openedDate || null,
        closed_date: closedDate || null,
        retainer_amount: parseFloat(retainer) || 0,
        notes: notes.trim() || null,
        ...extra,
      })
      leave()
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Failed to save')
    } finally { setSaving(false) }
  }

  async function handleSetOverride(e: FormEvent) {
    e.preventDefault()
    if (!ovStaffId || !ovRate) return
    setSavingOv(true); setOvError(null)
    try {
      const saved = await setRateOverride(matterId, Number(ovStaffId), parseFloat(ovRate))
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
    try {
      await deleteRateOverride(matterId, overrideId)
      setOverrides(prev => prev.filter(o => o.id !== overrideId))
    } catch (err) { setOvError(err instanceof Error ? err.message : 'Failed to delete override') }
  }

  function staffDisplayName(staffId: number) {
    const s = staff.find(st => st.id === staffId)
    return s ? `${s.name.first_name} ${s.name.last_name}` : `Staff #${staffId}`
  }

  if (loadError) {
    return (
      <div className="px-6 py-16 text-center">
        <p className="text-red-600 text-sm mb-4">{loadError}</p>
        <Link to="/app/matters" className="text-navy underline text-sm">Back to matters</Link>
      </div>
    )
  }
  if (!matter) {
    return <div className="px-6 py-16 text-center text-text-secondary text-sm">Loading matter…</div>
  }

  return (
    <div className="px-6 py-8 max-w-4xl mx-auto space-y-6">
      <MatterHeader matterId={matterId} matter={matter} />
      <h1 className="font-display text-xl text-navy">Edit matter</h1>

      <form onSubmit={handleSave} className="card p-5 space-y-3">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <label className="label" htmlFor="me-short">Short name</label>
            <input id="me-short" className="input mt-1" value={shortName} onChange={e => setShortName(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="me-name">Matter name</label>
            <input id="me-name" className="input mt-1" value={name} onChange={e => setName(e.target.value)} />
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <label className="label" htmlFor="me-status">Status</label>
            <select id="me-status" className="input mt-1" value={status} onChange={e => setStatus(e.target.value)}>
              {MATTER_STATUSES.map(s => <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>)}
            </select>
          </div>
          <div>
            <label className="label" htmlFor="me-state">State</label>
            <input id="me-state" className="input mt-1" value={state} onChange={e => setState(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="me-county">County</label>
            <input id="me-county" className="input mt-1" value={county} onChange={e => setCounty(e.target.value)} />
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <label className="label" htmlFor="me-court">Court name</label>
            <input id="me-court" className="input mt-1" value={courtName} onChange={e => setCourtName(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="me-number">Matter number</label>
            <input id="me-number" className="input mt-1" value={matterNumber} onChange={e => setMatterNumber(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="me-billing">Billing ID</label>
            <input id="me-billing" className="input mt-1" value={billingId} onChange={e => setBillingId(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="me-retainer">Retainer ($)</label>
            <input id="me-retainer" className="input mt-1" type="number" step="0.01" min="0" value={retainer} onChange={e => setRetainer(e.target.value)} />
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <label className="label" htmlFor="me-fee">Fee agreement signed</label>
            <input id="me-fee" className="input mt-1" type="date" value={feeDate} onChange={e => setFeeDate(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="me-opened">Opened date</label>
            <input id="me-opened" className="input mt-1" type="date" value={openedDate} onChange={e => setOpenedDate(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="me-closed">Closed date</label>
            <input id="me-closed" className="input mt-1" type="date" value={closedDate} onChange={e => setClosedDate(e.target.value)} />
          </div>
        </div>
        <div className="flex items-center gap-2">
          <input type="checkbox" id="me-probono" checked={proBono} onChange={e => setProBono(e.target.checked)} className="rounded border-border" />
          <label htmlFor="me-probono" className="text-sm text-navy">Pro bono</label>
        </div>
        <div>
          <label className="label" htmlFor="me-notes">Notes</label>
          <textarea id="me-notes" className="input mt-1" rows={3} value={notes} onChange={e => setNotes(e.target.value)} />
        </div>
        {saveError && <p className="text-sm text-red-600">{saveError}</p>}
        <div className="flex gap-3">
          <button type="submit" className="btn-primary" disabled={saving}>{saving ? 'Saving…' : 'Save changes'}</button>
          <button type="button" className="btn-secondary" onClick={leave}>Cancel</button>
        </div>
      </form>

      <div className="card p-5">
        <h2 className="font-semibold text-navy mb-3">Rate overrides</h2>
        {loadingOverrides && <p className="text-sm text-text-secondary">Loading…</p>}
        {!loadingOverrides && overrides.length === 0 && <p className="text-sm text-text-secondary mb-3">No rate overrides set. Default rates apply.</p>}
        {!loadingOverrides && overrides.length > 0 && (
          <table className="w-full text-sm mb-4 max-w-lg">
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
          <form onSubmit={handleSetOverride} className="flex gap-2 items-end max-w-lg">
            <div className="flex-1">
              <label className="label" htmlFor="me-ov-staff">Staff member</label>
              <select id="me-ov-staff" className="input mt-1" value={ovStaffId} onChange={e => setOvStaffId(e.target.value ? Number(e.target.value) : '')}>
                <option value="">— select —</option>
                {staff.filter(s => s.role === 'attorney' || s.role === 'paralegal').map(s => (
                  <option key={s.id} value={s.id}>
                    {s.name.first_name} {s.name.last_name} {s.default_billing_rate != null ? `($${s.default_billing_rate}/hr default)` : ''}
                  </option>
                ))}
              </select>
            </div>
            <div className="w-28">
              <label className="label" htmlFor="me-ov-rate">Rate ($/hr)</label>
              <input id="me-ov-rate" className="input mt-1" type="number" min="0" step="0.01" placeholder="250" value={ovRate} onChange={e => setOvRate(e.target.value)} />
            </div>
            <button type="submit" className="btn-primary" disabled={savingOv || !ovStaffId || !ovRate}>
              {savingOv ? 'Saving…' : 'Set'}
            </button>
          </form>
        )}
      </div>

      {closePrompt && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-start justify-center overflow-y-auto p-4"
          role="dialog" aria-modal="true" aria-label="Portal access for this matter"
          onClick={e => { if (e.target === e.currentTarget) setClosePrompt(false) }}>
          <div className="card p-5 w-full max-w-lg my-16 bg-white">
            <h3 className="font-semibold text-navy">Turn off portal access to this matter?</h3>
            <p className="text-sm text-text-secondary mt-1">
              You are closing this matter. If the client has portal access, they can keep reading
              its accounts and transactions unless you turn it off. You can turn it back on later
              from the matter&rsquo;s page, for a modification or an enforcement.
            </p>
            <div className="flex flex-wrap justify-end gap-2 mt-4">
              <button type="button" className="btn-secondary" onClick={() => setClosePrompt(false)}>
                Keep editing
              </button>
              <button type="button" className="btn-secondary" onClick={() => void save()}>
                Close and leave it on
              </button>
              <button type="button" className="btn-primary"
                onClick={() => void save({ client_portal_enabled: false })}>
                Close and turn it off
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
