import { useEffect, useRef, useState, DragEvent } from 'react'
import {
  getMatters, previewPleading, commitPleading,
  getMatterPleadings, getMatterClaims, updatePleading,
  uploadPleadingPdf, openPleadingPdf,
} from '../../lib/api'
import type {
  Matter, MatterPleading, MatterClaim, PleadingStatus,
  PleadingIngestPreview, PleadingCommitRequest,
  ChildCommitEntry, OCCommitEntry, ClaimCommitEntry, OpposingPartyCommitEntry,
  ChildSex, ClaimKind, CounselRole, FullName,
} from '../../types'

const PLEADING_STATUSES: PleadingStatus[] = ['live', 'superseded', 'withdrawn', 'inactive']
const PLEADING_STATUS_COLOR: Record<PleadingStatus, string> = {
  live:       'bg-green-100 text-green-800',
  superseded: 'bg-gray-100 text-gray-600',
  withdrawn:  'bg-amber-100 text-amber-800',
  inactive:   'bg-gray-100 text-gray-600',
}

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
  const [edParties, setEdParties]             = useState<OpposingPartyCommitEntry[]>([])
  const [edFilingParty, setEdFilingParty]     = useState('')
  const [edChildren, setEdChildren]           = useState<ChildCommitEntry[]>([])
  const [edOCs, setEdOCs]                     = useState<OCCommitEntry[]>([])
  const [edClaims, setEdClaims]               = useState<ClaimCommitEntry[]>([])

  const [committing, setCommitting]     = useState(false)
  const [commitError, setCommitError]   = useState<string | null>(null)
  const [commitSuccess, setCommitSuccess] = useState<string | null>(null)

  // Existing pleadings/claims for the matter
  const [pleadings, setPleadings] = useState<MatterPleading[]>([])
  const [claims, setClaims]       = useState<MatterClaim[]>([])
  const [savingStatus, setSavingStatus]   = useState<number | null>(null)
  const [statusError, setStatusError]     = useState<string | null>(null)
  const [pendingPdf, setPendingPdf]       = useState<File | null>(null)
  const [showAllClaims, setShowAllClaims] = useState(false)

  async function handleStatusChange(pleading: MatterPleading, status: PleadingStatus) {
    setSavingStatus(pleading.id)
    setStatusError(null)
    try {
      const updated = await updatePleading(pleading.id, { status })
      setPleadings(prev => prev.map(p => p.id === pleading.id ? updated : p))
    } catch (e) {
      setStatusError(e instanceof Error ? e.message : 'Could not update status')
    } finally {
      setSavingStatus(null)
    }
  }

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

    setEdParties(preview.opposing_parties.map(p => ({
      existing_id: p.existing_id,
      full_name: p.full_name,
      relationship: p.relationship,
    })))
    // A pleading filed against us was filed by an adverse party; default to the
    // first one extracted so the attorney confirms rather than types.
    setEdFilingParty(preview.opposing_parties[0]?.full_name ?? '')

    setEdChildren(preview.new_children.map(c => ({
      existing_id: c.existing_id,
      name: c.name,
      date_of_birth: c.date_of_birth ?? '',
      sex: (c.sex ?? 'other') as ChildSex,
      needs_support_after_majority: c.needs_support_after_majority,
    })))

    const oc: OCCommitEntry[] = [
      ...preview.opposing_counsel_matches.map(m => ({
        existing_id: m.existing_id,
        represents: m.proposed.represents,
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
      // Show every extracted attorney, even one with no bar number. The bar
      // pair is required to save (it is the dedup key), so an entry missing it
      // is flagged in the form for the attorney to complete — silently dropping
      // it here made extraction look like it had failed.
      ...preview.new_opposing_counsel.map(o => ({
        existing_id: null,
        represents: o.represents,
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
        bar_state: o.bar_state ?? '',
        bar_number: o.bar_number ?? '',
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
      // Held so it can be stored against the pleading once commit gives it an id.
      setPendingPdf(file)
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
      existing_id: null,
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
      // Our client's own pleading has no adverse filing party. Otherwise send the
      // name — the party may be created by this very commit, so it has no id yet
      // and the backend resolves it once the parties exist.
      opposing_party_id: null,
      opposing_party_name: edIsOurClient ? null : (edFilingParty || null),
      is_supplement: edIsSupplement,
      amends_pleading_id: edAmendsId === '' ? null : Number(edAmendsId),
      matter_field_updates: fieldUpdates,
      opposing_parties: edParties.filter(p => p.full_name.trim()),
      children: edChildren.filter(c => c.name.first_name && c.name.last_name && c.date_of_birth),
      opposing_counsel: edOCs.filter(o => o.bar_state && o.bar_number && o.name.last_name),
      claims: edClaims.filter(c => c.label && c.narrative),
    }

    try {
      const result = await commitPleading(payload)

      // Store the original PDF now that the pleading has an id. A failure here
      // is not fatal — the pleading is already committed — so it is reported
      // separately rather than turning the commit into an error.
      let pdfNote = ''
      if (pendingPdf) {
        try {
          await uploadPleadingPdf(result.pleading.id, pendingPdf)
          pdfNote = ', PDF stored'
        } catch {
          pdfNote = ' — PDF could not be stored; the record was still saved'
        }
      }
      setPendingPdf(null)

      setCommitSuccess(
        `Pleading committed: ${result.opposing_parties_created} parties, ` +
        `${result.children_created} children, ${result.opposing_counsel_linked} counsel, ` +
        `${result.claims_created} claims${pdfNote}`,
      )
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
            <div className="px-5 py-4 border-b border-border">
              <h2 className="font-semibold text-navy">Pleadings on file</h2>
              {statusError && <p className="text-xs text-red-600 mt-1">{statusError}</p>}
            </div>
            {pleadings.length === 0 && <div className="px-5 py-10 text-center text-text-secondary text-sm">No pleadings yet.</div>}
            {pleadings.length > 0 && (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-off-white">
                    <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Title</th>
                    <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Filed</th>
                    <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Served</th>
                    <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Status</th>
                    <th className="px-5 py-3 w-24"><span className="sr-only">PDF</span></th>
                  </tr>
                </thead>
                <tbody>
                  {pleadings.map(p => (
                    <tr key={p.id} className={`border-b border-border last:border-0 hover:bg-off-white/60 ${p.status === 'live' ? '' : 'opacity-60'}`}>
                      <td className="px-5 py-3 font-medium text-navy">
                        {p.title}
                        {p.is_supplement && (
                          <span className="ml-2 text-xs rounded-full px-2 py-0.5 font-medium bg-blue-100 text-blue-800">
                            supplement
                          </span>
                        )}
                        {p.amends_pleading_id && (
                          <span className="ml-2 text-xs text-text-secondary">
                            amends {pleadings.find(x => x.id === p.amends_pleading_id)?.title ?? `#${p.amends_pleading_id}`}
                          </span>
                        )}
                      </td>
                      <td className="px-5 py-3 text-text-secondary">{formatDate(p.filed_date)}</td>
                      <td className="px-5 py-3 text-text-secondary">{formatDate(p.served_date)}</td>
                      <td className="px-5 py-3">
                        <div className="flex items-center gap-2">
                          <span className={`text-xs rounded-full px-2.5 py-1 font-medium capitalize ${PLEADING_STATUS_COLOR[p.status]}`}>
                            {p.status}
                          </span>
                          <select
                            className="input text-xs py-1 w-32"
                            value={p.status}
                            disabled={savingStatus === p.id}
                            onChange={e => handleStatusChange(p, e.target.value as PleadingStatus)}
                          >
                            {PLEADING_STATUSES.map(s => <option key={s} value={s}>{s}</option>)}
                          </select>
                        </div>
                      </td>
                      <td className="px-5 py-3 text-right">
                        {p.storage_path ? (
                          <button type="button" className="text-xs text-navy underline"
                            onClick={() => openPleadingPdf(p.id).catch(e =>
                              setStatusError(e instanceof Error ? e.message : 'Could not open the PDF'))}>
                            View PDF
                          </button>
                        ) : (
                          <span className="text-xs text-text-secondary" title="No PDF was stored for this pleading">
                            no PDF
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Claims summary — only what is still operative. A claim pleaded in a
              superseded or withdrawn pleading is history, not the live case. */}
          {claims.length > 0 && (() => {
            const liveIds = new Set(pleadings.filter(p => p.status === 'live').map(p => p.id))
            const liveClaims = claims.filter(c => liveIds.has(c.matter_pleading_id))
            const hiddenCount = claims.length - liveClaims.length
            const shown = showAllClaims ? claims : liveClaims
            return (
              <div className="card p-5 mb-6">
                <div className="flex flex-wrap items-baseline justify-between gap-2 mb-3">
                  <h2 className="font-semibold text-navy">Claims, defenses, and counterclaims</h2>
                  {hiddenCount > 0 && (
                    <button type="button" className="text-xs text-navy underline"
                      onClick={() => setShowAllClaims(v => !v)}>
                      {showAllClaims
                        ? `Hide ${hiddenCount} from non-live pleadings`
                        : `Show ${hiddenCount} from non-live pleadings`}
                    </button>
                  )}
                </div>
                {shown.length === 0 ? (
                  <p className="text-sm text-text-secondary">
                    No claims on live pleadings{hiddenCount > 0 ? ` (${hiddenCount} on non-live pleadings)` : ''}.
                  </p>
                ) : (
                  <div className="space-y-2">
                    {shown.map(c => {
                      const source = pleadings.find(p => p.id === c.matter_pleading_id)
                      const isLive = source?.status === 'live'
                      return (
                        <div key={c.id} className={`text-sm border-l-2 pl-3 ${isLive ? 'border-navy/20' : 'border-gray-300 opacity-60'}`}>
                          <span className="text-xs font-semibold text-text-secondary uppercase mr-2">{CLAIM_KIND_LABEL[c.kind]}</span>
                          <span className="font-medium text-navy">{c.label}</span>
                          {!isLive && source && (
                            <span className="ml-2 text-xs text-text-secondary">
                              from {source.status} pleading
                            </span>
                          )}
                          <p className="text-text-secondary mt-0.5">{c.narrative}</p>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })()}
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
            {!edIsOurClient && (
              <div>
                <label className="label">Filed by</label>
                <select className="input mt-1" value={edFilingParty}
                  onChange={e => setEdFilingParty(e.target.value)}>
                  <option value="">— unassigned —</option>
                  {edParties.filter(p => p.full_name.trim()).map(p => (
                    <option key={p.full_name} value={p.full_name}>{p.full_name}</option>
                  ))}
                </select>
                <p className="text-xs text-text-secondary mt-1">
                  Which adverse party filed this. Choices come from the parties below.
                </p>
              </div>
            )}
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

          {/* Opposing parties — everything else that names a party points at these */}
          <div className="card p-5">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold text-navy">Opposing parties</h3>
              <button type="button" className="text-xs text-navy underline"
                onClick={() => setEdParties(prev => [...prev, { existing_id: null, full_name: '', relationship: null }])}>
                + Add party
              </button>
            </div>
            {edParties.length === 0 && (
              <p className="text-sm text-text-secondary">
                None extracted. Add the adverse party here — counsel and claims can only be
                assigned to a party that exists.
              </p>
            )}
            <div className="space-y-2">
              {edParties.map((p, idx) => (
                <div key={idx} className="border border-border rounded p-3">
                  {p.existing_id && (
                    <p className="text-xs text-green-700 mb-2">
                      Already on this matter — will be reused, not added again
                    </p>
                  )}
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                    <input className="input text-sm md:col-span-2" placeholder="Full name"
                      value={p.full_name}
                      onChange={e => setEdParties(prev => prev.map((x, i) =>
                        i === idx ? { ...x, full_name: e.target.value } : x))} />
                    <input className="input text-sm" placeholder="Relationship (e.g. spouse)"
                      value={p.relationship ?? ''}
                      onChange={e => setEdParties(prev => prev.map((x, i) =>
                        i === idx ? { ...x, relationship: e.target.value || null } : x))} />
                  </div>
                  <button type="button" className="text-red-500 text-xs mt-2"
                    onClick={() => setEdParties(prev => prev.filter((_, i) => i !== idx))}>
                    Remove
                  </button>
                </div>
              ))}
            </div>
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
                  {c.existing_id && (
                    <p className="text-xs text-green-700 mb-2">
                      Already on this matter — will be updated, not added again
                    </p>
                  )}
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
                  {o.represents && (
                    <p className="text-xs text-text-secondary mb-2">
                      Represents <span className="font-medium text-text-primary">{o.represents}</span> —
                      confirm this is not our client
                    </p>
                  )}
                  {(!o.bar_state || !o.bar_number) && (
                    <p className="text-xs text-amber-700 mb-2">
                      Bar state and number are required to save this attorney — add them below or
                      this entry will be discarded on commit.
                    </p>
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
