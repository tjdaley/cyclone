import { useEffect, useRef, useState, DragEvent } from 'react'
import {
  getMatters, previewPleading, commitPleading,
  getMatterPleadings, getMatterClaims,
} from '../../lib/api'
import type {
  Matter, MatterPleading, MatterClaim,
  PleadingIngestPreview, PleadingCommitRequest,
  ChildCommitEntry, OCCommitEntry, ClaimCommitEntry,
  ChildSex, ClaimKind, CounselRole, FullName,
} from '../../types'

const CLAIM_KINDS: ClaimKind[] = ['claim', 'defense', 'affirmative_defense', 'counterclaim']
const CLAIM_KIND_LABEL: Record<ClaimKind, string> = {
  claim: 'Claim',
  defense: 'Defense',
  affirmative_defense: 'Affirmative Defense',
  counterclaim: 'Counterclaim',
}

function formatDate(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function blankName(): FullName {
  return { first_name: '', last_name: '', middle_name: null, courtesy_title: null, suffix: null }
}

export default function PleadingsPage() {
  const [matters, setMatters]   = useState<Matter[]>([])
  const [matterId, setMatterId] = useState<number | null>(null)

  // Upload / preview state
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [dragOver, setDragOver]     = useState(false)
  const [uploading, setUploading]   = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [preview, setPreview]       = useState<PleadingIngestPreview | null>(null)

  // Editable preview state (initialized from preview, modified by attorney)
  const [edTitle, setEdTitle]                 = useState('')
  const [edFiledDate, setEdFiledDate]         = useState('')
  const [edServedDate, setEdServedDate]       = useState('')
  const [edIsSupplement, setEdIsSupplement]   = useState(false)
  const [edIsOurClient, setEdIsOurClient]     = useState(false)
  const [edAmendsId, setEdAmendsId]           = useState<number | ''>('')
  const [edAcceptedFields, setEdAcceptedFields] = useState<Record<string, boolean>>({})
  const [edChildren, setEdChildren]           = useState<ChildCommitEntry[]>([])
  const [edOCs, setEdOCs]                     = useState<OCCommitEntry[]>([])
  const [edClaims, setEdClaims]               = useState<ClaimCommitEntry[]>([])

  const [committing, setCommitting]     = useState(false)
  const [commitError, setCommitError]   = useState<string | null>(null)
  const [commitSuccess, setCommitSuccess] = useState<string | null>(null)

  // Existing pleadings/claims for the matter
  const [pleadings, setPleadings] = useState<MatterPleading[]>([])
  const [claims, setClaims]       = useState<MatterClaim[]>([])

  useEffect(() => {
    getMatters()
      .then(ms => setMatters(ms.filter(m => m.status === 'active')))
      .catch(console.error)
  }, [])

  useEffect(() => {
    if (!matterId) { setPleadings([]); setClaims([]); return }
    Promise.all([getMatterPleadings(matterId), getMatterClaims(matterId)])
      .then(([p, c]) => { setPleadings(p); setClaims(c) })
      .catch(console.error)
  }, [matterId, commitSuccess])

  // When a preview arrives, seed the editable state
  useEffect(() => {
    if (!preview) return
    setEdTitle(preview.pleading.title)
    setEdFiledDate(preview.pleading.filed_date ?? '')
    setEdServedDate(preview.pleading.served_date ?? '')
    setEdIsSupplement(preview.pleading.is_supplement)
    setEdIsOurClient(false)
    setEdAmendsId('')

    // All proposed field updates start accepted
    const accepted: Record<string, boolean> = {}
    Object.keys(preview.matter_field_updates).forEach(k => { accepted[k] = true })
    setEdAcceptedFields(accepted)

    setEdChildren(preview.new_children.map(c => ({
      name: c.name,
      date_of_birth: c.date_of_birth ?? '',
      sex: (c.sex ?? 'other') as ChildSex,
      needs_support_after_majority: c.needs_support_after_majority,
    })))

    const oc: OCCommitEntry[] = [
      ...preview.opposing_counsel_matches.map(m => ({
        existing_id: m.existing_id,
        name: m.proposed.name,
        firm_name: m.proposed.firm_name ?? m.existing.firm_name,
        street_address: m.proposed.street_address ?? m.existing.street_address,
        street_address_2: m.proposed.street_address_2 ?? m.existing.street_address_2,
        city: m.proposed.city ?? m.existing.city,
        state: m.proposed.state ?? m.existing.state,
        postal_code: m.proposed.postal_code ?? m.existing.postal_code,
        email: m.proposed.email ?? m.existing.email,
        cell_phone: m.proposed.cell_phone ?? m.existing.cell_phone,
        telephone: m.proposed.telephone ?? m.existing.telephone,
        fax: m.proposed.fax ?? m.existing.fax,
        bar_state: m.existing.bar_state,
        bar_number: m.existing.bar_number,
        email_ccs: m.proposed.email_ccs ?? m.existing.email_ccs,
        opposing_party_id: null,
        role: 'lead' as CounselRole,
      })),
      ...preview.new_opposing_counsel.filter(o => o.bar_state && o.bar_number).map(o => ({
        existing_id: null,
        name: o.name,
        firm_name: o.firm_name,
        street_address: o.street_address,
        street_address_2: o.street_address_2,
        city: o.city,
        state: o.state,
        postal_code: o.postal_code,
        email: o.email,
        cell_phone: o.cell_phone,
        telephone: o.telephone,
        fax: o.fax,
        bar_state: o.bar_state!,
        bar_number: o.bar_number!,
        email_ccs: o.email_ccs,
        opposing_party_id: null,
        role: 'lead' as CounselRole,
      })),
    ]
    setEdOCs(oc)

    setEdClaims(preview.claims.map(c => ({
      kind: c.kind,
      label: c.label,
      narrative: c.narrative,
      statute_rule_cited: c.statute_rule_cited,
      opposing_party_id: null,
    })))
  }, [preview])

  // ── Upload ────────────────────────────────────────────────────────────

  async function handleFile(file: File) {
    if (!matterId) return
    if (file.type !== 'application/pdf') {
      setUploadError('Only PDF files are accepted')
      return
    }
    setUploading(true); setUploadError(null); setPreview(null); setCommitSuccess(null)
    try {
      const result = await previewPleading(matterId, file)
      setPreview(result)
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Preview failed')
    } finally {
      setUploading(false)
    }
  }

  function handleDrop(e: DragEvent) { e.preventDefault(); setDragOver(false); const f = e.dataTransfer.files[0]; if (f) handleFile(f) }
  function handleDragOver(e: DragEvent) { e.preventDefault(); setDragOver(true) }
  function handleDragLeave(e: DragEvent) { e.preventDefault(); setDragOver(false) }

  // ── Edit helpers ──────────────────────────────────────────────────────

  function addChild() {
    setEdChildren(prev => [...prev, {
      name: blankName(), date_of_birth: '', sex: 'other', needs_support_after_majority: false,
    }])
  }
  function removeChild(idx: number) { setEdChildren(prev => prev.filter((_, i) => i !== idx)) }
  function updateChild(idx: number, patch: Partial<ChildCommitEntry>) {
    setEdChildren(prev => prev.map((c, i) => i === idx ? { ...c, ...patch } : c))
  }

  function addClaim() {
    setEdClaims(prev => [...prev, {
      kind: 'claim', label: '', narrative: '', statute_rule_cited: null, opposing_party_id: null,
    }])
  }
  function removeClaim(idx: number) { setEdClaims(prev => prev.filter((_, i) => i !== idx)) }
  function updateClaim(idx: number, patch: Partial<ClaimCommitEntry>) {
    setEdClaims(prev => prev.map((c, i) => i === idx ? { ...c, ...patch } : c))
  }

  function addOC() {
    setEdOCs(prev => [...prev, {
      existing_id: null,
      name: blankName(),
      firm_name: null, street_address: null, street_address_2: null,
      city: null, state: null, postal_code: null,
      email: null, cell_phone: null, telephone: null, fax: null,
      bar_state: 'TX', bar_number: '',
      email_ccs: [],
      opposing_party_id: null,
      role: 'lead',
    }])
  }
  function removeOC(idx: number) { setEdOCs(prev => prev.filter((_, i) => i !== idx)) }
  function updateOC(idx: number, patch: Partial<OCCommitEntry>) {
    setEdOCs(prev => prev.map((o, i) => i === idx ? { ...o, ...patch } : o))
  }

  // ── Commit ────────────────────────────────────────────────────────────

  async function handleCommit() {
    if (!preview || !matterId) return
    setCommitting(true); setCommitError(null)

    // Build matter_field_updates from the accepted ones only
    const fieldUpdates: Record<string, unknown> = {}
    Object.entries(preview.matter_field_updates).forEach(([k, diff]) => {
      if (edAcceptedFields[k]) fieldUpdates[k] = diff.proposed
    })

    const payload: PleadingCommitRequest = {
      matter_id: matterId,
      raw_text: preview.raw_text,
      title: edTitle.trim(),
      filed_date: edFiledDate || null,
      served_date: edServedDate || null,
      opposing_party_id: edIsOurClient ? null : null,  // TODO: pick specific OP if multi-party
      is_supplement: edIsSupplement,
      amends_pleading_id: edAmendsId === '' ? null : Number(edAmendsId),
      matter_field_updates: fieldUpdates,
      children: edChildren.filter(c => c.name.first_name && c.name.last_name && c.date_of_birth),
      opposing_counsel: edOCs.filter(o => o.bar_state && o.bar_number && o.name.last_name),
      claims: edClaims.filter(c => c.label && c.narrative),
    }

    try {
      const result = await commitPleading(payload)
      setCommitSuccess(`Pleading committed: ${result.children_created} children, ${result.opposing_counsel_linked} counsel, ${result.claims_created} claims`)
      setPreview(null)
    } catch (err) {
      setCommitError(err instanceof Error ? err.message : 'Commit failed')
    } finally {
      setCommitting(false)
    }
  }

  function discardPreview() { setPreview(null); setCommitSuccess(null) }

  return (
    <div className="px-6 py-8 max-w-5xl mx-auto">
      <div className="mb-8">
        <h1 className="font-display text-3xl text-navy">Pleadings</h1>
        <p className="text-text-secondary mt-1">Ingest and review pleadings to extract case info, claims, and opposing counsel.</p>
      </div>

      {/* Matter selector */}
      <div className="card p-5 mb-6">
        <label className="label" htmlFor="matter-select">Select matter</label>
        <select id="matter-select" className="input mt-1" value={matterId ?? ''}
          onChange={e => { setMatterId(e.target.value ? Number(e.target.value) : null); setPreview(null) }}>
          <option value="">— choose a matter —</option>
          {matters.map(m => <option key={m.id} value={m.id}>{m.short_name ?? m.matter_name}</option>)}
        </select>
      </div>

      {matterId && !preview && (
        <>
          {/* Upload zone */}
          <div
            className={`card p-8 mb-6 text-center border-2 border-dashed transition-colors cursor-pointer ${dragOver ? 'border-navy bg-navy/5' : 'border-border hover:border-navy/40'}`}
            onDrop={handleDrop} onDragOver={handleDragOver} onDragLeave={handleDragLeave}
            onClick={() => fileInputRef.current?.click()}>
            <input ref={fileInputRef} type="file" accept=".pdf" className="hidden"
              onChange={e => { const f = e.target.files?.[0]; if (f) handleFile(f); e.target.value = '' }} />
            {uploading ? (
              <div>
                <div className="mx-auto mb-3 animate-spin w-8 h-8 border-4 border-navy/20 border-t-navy rounded-full" />
                <p className="text-navy font-medium">Analyzing pleading...</p>
                <p className="text-text-secondary text-sm mt-1">Extracting case info, opposing counsel, and claims</p>
              </div>
            ) : (
              <div>
                <svg className="mx-auto w-10 h-10 text-text-secondary/50 mb-3" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                </svg>
                <p className="text-navy font-medium">Drop a pleading PDF here or click to browse</p>
                <p className="text-text-secondary text-sm mt-1">Petitions, answers, counterpetitions, amended pleadings, supplements</p>
              </div>
            )}
          </div>

          {uploadError && <div className="card p-4 mb-6 bg-red-50 border border-red-200 text-sm text-red-700">{uploadError}</div>}
          {commitSuccess && <div className="card p-4 mb-6 bg-green-50 border border-green-200 text-sm text-green-700">{commitSuccess}</div>}

          {/* Existing pleadings */}
          <div className="card overflow-hidden mb-6">
            <div className="px-5 py-4 border-b border-border"><h2 className="font-semibold text-navy">Pleadings on file</h2></div>
            {pleadings.length === 0 && <div className="px-5 py-10 text-center text-text-secondary text-sm">No pleadings yet.</div>}
            {pleadings.length > 0 && (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-off-white">
                    <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Title</th>
                    <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Filed</th>
                    <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Served</th>
                    <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Live?</th>
                  </tr>
                </thead>
                <tbody>
                  {pleadings.map(p => {
                    const isSuperseded = pleadings.some(x => x.amends_pleading_id === p.id)
                    const isLive = !isSuperseded && !p.is_supplement
                    return (
                      <tr key={p.id} className="border-b border-border last:border-0 hover:bg-off-white/60">
                        <td className="px-5 py-3 font-medium text-navy">{p.title}</td>
                        <td className="px-5 py-3 text-text-secondary">{formatDate(p.filed_date)}</td>
                        <td className="px-5 py-3 text-text-secondary">{formatDate(p.served_date)}</td>
                        <td className="px-5 py-3">
                          {isLive && <span className="text-xs rounded-full px-2.5 py-1 font-medium bg-green-100 text-green-800">Live</span>}
                          {isSuperseded && <span className="text-xs rounded-full px-2.5 py-1 font-medium bg-gray-100 text-gray-600">Superseded</span>}
                          {p.is_supplement && <span className="text-xs rounded-full px-2.5 py-1 font-medium bg-blue-100 text-blue-800">Supplement</span>}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </div>

          {/* Claims summary */}
          {claims.length > 0 && (
            <div className="card p-5 mb-6">
              <h2 className="font-semibold text-navy mb-3">Claims, defenses, and counterclaims</h2>
              <div className="space-y-2">
                {claims.map(c => (
                  <div key={c.id} className="text-sm border-l-2 border-navy/20 pl-3">
                    <span className="text-xs font-semibold text-text-secondary uppercase mr-2">{CLAIM_KIND_LABEL[c.kind]}</span>
                    <span className="font-medium text-navy">{c.label}</span>
                    <p className="text-text-secondary mt-0.5">{c.narrative}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}

      {/* Preview / review form */}
      {preview && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold text-navy text-xl">Review extracted data</h2>
            <button type="button" className="text-sm text-text-secondary underline" onClick={discardPreview}>
              Discard and re-upload
            </button>
          </div>

          {preview.warnings.length > 0 && (
            <div className="card p-4 bg-amber-50 border border-amber-200 text-sm text-amber-800">
              {preview.warnings.map((w, i) => <p key={i}>{w}</p>)}
            </div>
          )}

          {/* Pleading metadata */}
          <div className="card p-5 space-y-3">
            <h3 className="font-semibold text-navy">Pleading details</h3>
            <div>
              <label className="label">Title</label>
              <input className="input mt-1" value={edTitle} onChange={e => setEdTitle(e.target.value)} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label">Filed date</label>
                <input type="date" className="input mt-1" value={edFiledDate} onChange={e => setEdFiledDate(e.target.value)} />
              </div>
              <div>
                <label className="label">Served date</label>
                <input type="date" className="input mt-1" value={edServedDate} onChange={e => setEdServedDate(e.target.value)} />
              </div>
            </div>
            <div className="flex items-center gap-4">
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={edIsOurClient} onChange={e => setEdIsOurClient(e.target.checked)} className="w-4 h-4 accent-navy" />
                <span className="text-sm text-navy">Our client's pleading</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={edIsSupplement} onChange={e => setEdIsSupplement(e.target.checked)} className="w-4 h-4 accent-navy" />
                <span className="text-sm text-navy">Supplement</span>
              </label>
            </div>
            {pleadings.length > 0 && (
              <div>
                <label className="label">Amends (supersedes) — optional</label>
                <select className="input mt-1" value={edAmendsId} onChange={e => setEdAmendsId(e.target.value ? Number(e.target.value) : '')}>
                  <option value="">— none (new pleading) —</option>
                  {pleadings.map(p => <option key={p.id} value={p.id}>{p.title}</option>)}
                </select>
              </div>
            )}
            {preview.pleading.amends_pleading_title && (
              <p className="text-xs text-amber-700">
                LLM hint: this appears to amend "{preview.pleading.amends_pleading_title}" — pick from the dropdown above.
              </p>
            )}
          </div>

          {/* Matter field updates */}
          {Object.keys(preview.matter_field_updates).length > 0 && (
            <div className="card p-5">
              <h3 className="font-semibold text-navy mb-3">Proposed matter updates</h3>
              <p className="text-xs text-text-secondary mb-3">Uncheck any you don't want applied.</p>
              <div className="space-y-2">
                {Object.entries(preview.matter_field_updates).map(([field, diff]) => (
                  <label key={field} className="flex items-start gap-2 cursor-pointer">
                    <input type="checkbox" className="mt-0.5 w-4 h-4 accent-navy"
                      checked={edAcceptedFields[field] ?? false}
                      onChange={e => setEdAcceptedFields(prev => ({ ...prev, [field]: e.target.checked }))} />
                    <div className="text-sm">
                      <span className="font-medium text-navy capitalize">{field.replace(/_/g, ' ')}</span>
                      <div className="text-text-secondary">
                        <span className="line-through">{String(diff.current ?? '—')}</span>
                        <span className="mx-2">→</span>
                        <span className="text-navy">{String(diff.proposed)}</span>
                      </div>
                    </div>
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* Children */}
          <div className="card p-5">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold text-navy">Children</h3>
              <button type="button" className="text-xs text-navy underline" onClick={addChild}>+ Add child</button>
            </div>
            {edChildren.length === 0 && <p className="text-sm text-text-secondary">No children found.</p>}
            <div className="space-y-3">
              {edChildren.map((c, idx) => (
                <div key={idx} className="border border-border rounded p-3">
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-2 mb-2">
                    <input className="input text-sm" placeholder="First name" value={c.name.first_name}
                      onChange={e => updateChild(idx, { name: { ...c.name, first_name: e.target.value } })} />
                    <input className="input text-sm" placeholder="Last name" value={c.name.last_name}
                      onChange={e => updateChild(idx, { name: { ...c.name, last_name: e.target.value } })} />
                    <input type="date" className="input text-sm" value={c.date_of_birth}
                      onChange={e => updateChild(idx, { date_of_birth: e.target.value })} />
                  </div>
                  <div className="flex items-center gap-4">
                    <select className="input text-sm w-32" value={c.sex}
                      onChange={e => updateChild(idx, { sex: e.target.value as ChildSex })}>
                      <option value="male">Male</option>
                      <option value="female">Female</option>
                      <option value="other">Other</option>
                    </select>
                    <label className="flex items-center gap-2 text-sm cursor-pointer">
                      <input type="checkbox" className="w-4 h-4 accent-navy"
                        checked={c.needs_support_after_majority}
                        onChange={e => updateChild(idx, { needs_support_after_majority: e.target.checked })} />
                      <span className="text-navy">Needs support past majority</span>
                    </label>
                    <button type="button" className="ml-auto text-red-500 text-xs" onClick={() => removeChild(idx)}>Remove</button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Opposing counsel */}
          <div className="card p-5">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold text-navy">Opposing counsel</h3>
              <button type="button" className="text-xs text-navy underline" onClick={addOC}>+ Add counsel</button>
            </div>
            {edOCs.length === 0 && <p className="text-sm text-text-secondary">None extracted.</p>}
            <div className="space-y-3">
              {edOCs.map((o, idx) => (
                <div key={idx} className="border border-border rounded p-3">
                  {o.existing_id && (
                    <p className="text-xs text-green-700 mb-2">Matched existing counsel (bar #{o.bar_state}:{o.bar_number})</p>
                  )}
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-2 mb-2">
                    <input className="input text-sm" placeholder="First name" value={o.name.first_name}
                      onChange={e => updateOC(idx, { name: { ...o.name, first_name: e.target.value } })} />
                    <input className="input text-sm" placeholder="Last name" value={o.name.last_name}
                      onChange={e => updateOC(idx, { name: { ...o.name, last_name: e.target.value } })} />
                    <input className="input text-sm" placeholder="Firm name" value={o.firm_name ?? ''}
                      onChange={e => updateOC(idx, { firm_name: e.target.value || null })} />
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-2 mb-2">
                    <input className="input text-sm" placeholder="Bar state" value={o.bar_state}
                      onChange={e => updateOC(idx, { bar_state: e.target.value })} />
                    <input className="input text-sm" placeholder="Bar number" value={o.bar_number}
                      onChange={e => updateOC(idx, { bar_number: e.target.value })} />
                    <input className="input text-sm" placeholder="Email" value={o.email ?? ''}
                      onChange={e => updateOC(idx, { email: e.target.value || null })} />
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-2 mb-2">
                    <input className="input text-sm" placeholder="Telephone" value={o.telephone ?? ''}
                      onChange={e => updateOC(idx, { telephone: e.target.value || null })} />
                    <input className="input text-sm" placeholder="Cell phone" value={o.cell_phone ?? ''}
                      onChange={e => updateOC(idx, { cell_phone: e.target.value || null })} />
                    <input className="input text-sm" placeholder="Fax" value={o.fax ?? ''}
                      onChange={e => updateOC(idx, { fax: e.target.value || null })} />
                  </div>
                  <button type="button" className="text-red-500 text-xs" onClick={() => removeOC(idx)}>Remove</button>
                </div>
              ))}
            </div>
          </div>

          {/* Claims */}
          <div className="card p-5">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold text-navy">Claims, defenses, counterclaims</h3>
              <button type="button" className="text-xs text-navy underline" onClick={addClaim}>+ Add claim</button>
            </div>
            {edClaims.length === 0 && <p className="text-sm text-text-secondary">None extracted. You can add claims manually.</p>}
            <div className="space-y-3">
              {edClaims.map((c, idx) => (
                <div key={idx} className="border border-border rounded p-3 space-y-2">
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                    <select className="input text-sm" value={c.kind}
                      onChange={e => updateClaim(idx, { kind: e.target.value as ClaimKind })}>
                      {CLAIM_KINDS.map(k => <option key={k} value={k}>{CLAIM_KIND_LABEL[k]}</option>)}
                    </select>
                    <input className="input text-sm md:col-span-2" placeholder="Label" value={c.label}
                      onChange={e => updateClaim(idx, { label: e.target.value })} />
                  </div>
                  <textarea className="input text-sm w-full" rows={2} placeholder="Narrative" value={c.narrative}
                    onChange={e => updateClaim(idx, { narrative: e.target.value })} />
                  <div className="flex items-center gap-2">
                    <input className="input text-sm flex-1" placeholder="Statute / rule cited (optional)"
                      value={c.statute_rule_cited ?? ''}
                      onChange={e => updateClaim(idx, { statute_rule_cited: e.target.value || null })} />
                    <button type="button" className="text-red-500 text-xs" onClick={() => removeClaim(idx)}>Remove</button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Commit bar */}
          <div className="card p-5 flex items-center gap-3">
            <button type="button" className="btn-primary" disabled={committing || !edTitle.trim()} onClick={handleCommit}>
              {committing ? 'Committing...' : 'Commit pleading'}
            </button>
            <button type="button" className="btn-secondary" onClick={discardPreview}>Cancel</button>
            {commitError && <span className="text-sm text-red-600">{commitError}</span>}
          </div>
        </div>
      )}
    </div>
  )
}
