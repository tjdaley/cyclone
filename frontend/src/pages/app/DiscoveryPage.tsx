import { useEffect, useState, useRef, DragEvent } from 'react'
import {
  getMatters, getDiscoveryDocuments, getDiscoveryItems, uploadDiscoveryPDF,
  updateDiscoveryItem, getStandardPrivileges, getStandardObjections,
  downloadDiscoveryDocx,
} from '../../lib/api'
import type {
  Matter, DiscoveryDocument, DiscoveryRequestItem, DiscoveryUploadResponse,
  DiscoveryRequestItemUpdatePayload, StandardPrivilege, StandardObjection,
} from '../../types'

const TYPE_LABEL: Record<string, string> = {
  interrogatories: 'Interrogatories',
  production:      'Requests for Production',
  disclosures:     'Disclosures',
  admissions:      'Requests for Admission',
}

const STATUS_COLOR: Record<string, string> = {
  pending_client:   'bg-amber-100 text-amber-800',
  pending_attorney: 'bg-purple-100 text-purple-800',
  pending_review:   'bg-blue-100 text-blue-800',
  finalized:        'bg-green-100 text-green-800',
  objected:         'bg-red-100 text-red-800',
}

function effectiveStatus(item: DiscoveryRequestItem, draft?: Partial<DiscoveryRequestItemUpdatePayload>): string {
  const clientNeeded    = draft?.client_response_needed ?? item.client_response_needed
  const interpretations = draft?.interpretations ?? item.interpretations
  const privileges      = draft?.privileges ?? item.privileges
  const objections      = draft?.objections ?? item.objections
  const response        = draft?.response ?? item.response

  if (!clientNeeded && item.status === 'pending_client') {
    const hasContent = interpretations.length > 0 || privileges.length > 0 || objections.length > 0 || (response && response.trim().length > 0)
    return hasContent ? item.status : 'pending_attorney'
  }
  return item.status
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

export default function DiscoveryPage() {
  const [matters, setMatters]         = useState<Matter[]>([])
  const [matterId, setMatterId]       = useState<number | null>(null)
  const [documents, setDocuments]     = useState<DiscoveryDocument[]>([])
  const [loadingDocs, setLoadingDocs] = useState(false)

  const [selectedDocId, setSelectedDocId] = useState<number | null>(null)
  const [items, setItems]                 = useState<DiscoveryRequestItem[]>([])
  const [loadingItems, setLoadingItems]   = useState(false)
  const [expandedItem, setExpandedItem]   = useState<number | null>(null)

  // Upload
  const [uploading, setUploading]       = useState(false)
  const [uploadResult, setUploadResult] = useState<DiscoveryUploadResponse | null>(null)
  const [uploadError, setUploadError]   = useState<string | null>(null)
  const [dragOver, setDragOver]         = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Standard lookups
  const [stdPrivileges, setStdPrivileges] = useState<StandardPrivilege[]>([])
  const [stdObjections, setStdObjections] = useState<StandardObjection[]>([])

  // Per-item draft edits
  const [drafts, setDrafts]       = useState<Record<number, Partial<DiscoveryRequestItemUpdatePayload>>>({})
  const [saving, setSaving]       = useState<Record<number, boolean>>({})
  const [saveError, setSaveError] = useState<Record<number, string | null>>({})

  useEffect(() => {
    getMatters().then(ms => setMatters(ms.filter(m => m.status === 'active'))).catch(console.error)
  }, [])

  useEffect(() => {
    if (!matterId) { setDocuments([]); return }
    setLoadingDocs(true)
    setUploadResult(null); setUploadError(null)
    getDiscoveryDocuments(matterId).then(setDocuments).catch(console.error).finally(() => setLoadingDocs(false))
  }, [matterId])

  useEffect(() => {
    if (!selectedDocId) { setItems([]); return }
    setLoadingItems(true)
    getDiscoveryItems(selectedDocId).then(setItems).catch(console.error).finally(() => setLoadingItems(false))

    const doc = documents.find(d => d.id === selectedDocId)
    if (doc) {
      Promise.all([getStandardPrivileges(), getStandardObjections(doc.request_type)])
        .then(([p, o]) => { setStdPrivileges(p); setStdObjections(o) })
        .catch(console.error)
    }
  }, [selectedDocId, documents])

  // ── Upload handlers ────────────────────────────────────────────────────

  async function handleFile(file: File) {
    if (!matterId) return
    if (file.type !== 'application/pdf') { setUploadError('Only PDF files are accepted'); return }
    setUploading(true); setUploadError(null); setUploadResult(null)
    try {
      const result = await uploadDiscoveryPDF(matterId, file)
      setUploadResult(result)
      const docs = await getDiscoveryDocuments(matterId)
      setDocuments(docs)
    } catch (err) { setUploadError(err instanceof Error ? err.message : 'Upload failed') }
    finally { setUploading(false) }
  }

  function handleDrop(e: DragEvent) { e.preventDefault(); setDragOver(false); const f = e.dataTransfer.files[0]; if (f) handleFile(f) }
  function handleDragOver(e: DragEvent) { e.preventDefault(); setDragOver(true) }
  function handleDragLeave(e: DragEvent) { e.preventDefault(); setDragOver(false) }

  // ── Item edit helpers ──────────────────────────────────────────────────

  function patchDraft(itemId: number, patch: Partial<DiscoveryRequestItemUpdatePayload>) {
    setDrafts(prev => ({ ...prev, [itemId]: { ...prev[itemId], ...patch } }))
  }

  async function saveItem(item: DiscoveryRequestItem) {
    const draft = drafts[item.id]
    if (!draft || Object.keys(draft).length === 0) return
    setSaving(prev => ({ ...prev, [item.id]: true }))
    setSaveError(prev => ({ ...prev, [item.id]: null }))
    try {
      const updated = await updateDiscoveryItem(item.id, draft)
      setItems(prev => prev.map(i => i.id === updated.id ? updated : i))
      setDrafts(prev => { const next = { ...prev }; delete next[item.id]; return next })
    } catch (err) {
      setSaveError(prev => ({ ...prev, [item.id]: err instanceof Error ? err.message : 'Save failed' }))
    } finally {
      setSaving(prev => ({ ...prev, [item.id]: false }))
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────

  return (
    <div className="px-6 py-8 max-w-5xl mx-auto">
      <div className="mb-8">
        <h1 className="font-display text-3xl text-navy">Discovery</h1>
        <p className="text-text-secondary mt-1">Upload, review, and track discovery requests.</p>
      </div>

      {/* Matter selector */}
      <div className="card p-5 mb-6">
        <label className="label" htmlFor="matter-select">Select matter</label>
        <select id="matter-select" className="input mt-1" value={matterId ?? ''}
          onChange={e => { setMatterId(e.target.value ? Number(e.target.value) : null); setSelectedDocId(null) }}>
          <option value="">— choose a matter —</option>
          {matters.map(m => <option key={m.id} value={m.id}>{m.short_name ?? m.matter_name}</option>)}
        </select>
      </div>

      {matterId && (
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
                <p className="text-navy font-medium">Processing PDF...</p>
                <p className="text-text-secondary text-sm mt-1">Extracting text, classifying document, and parsing individual requests</p>
              </div>
            ) : (
              <div>
                <svg className="mx-auto w-10 h-10 text-text-secondary/50 mb-3" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
                </svg>
                <p className="text-navy font-medium">Drop a discovery PDF here or click to browse</p>
                <p className="text-text-secondary text-sm mt-1">Interrogatories, Requests for Production, Admissions, or Disclosures</p>
              </div>
            )}
          </div>

          {uploadError && <div className="card p-4 mb-6 bg-red-50 border border-red-200 text-sm text-red-700">{uploadError}</div>}

          {uploadResult && (
            <div className="card p-5 mb-6 bg-green-50 border border-green-200">
              <h3 className="font-semibold text-green-800 mb-2">Ingestion complete</h3>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm mb-3">
                <div><p className="text-text-secondary text-xs">Type</p><p className="font-medium text-green-800">{TYPE_LABEL[uploadResult.document.request_type] ?? uploadResult.document.request_type}</p></div>
                <div><p className="text-text-secondary text-xs">Served</p><p className="font-medium text-green-800">{formatDate(uploadResult.document.propounded_date)}</p></div>
                <div><p className="text-text-secondary text-xs">Due</p><p className="font-medium text-green-800">{formatDate(uploadResult.document.due_date)}</p></div>
                <div><p className="text-text-secondary text-xs">Items parsed</p><p className="font-medium text-green-800">{uploadResult.item_count}</p></div>
              </div>
              {uploadResult.warnings.length > 0 && (
                <div className="mt-2 text-xs text-amber-700">{uploadResult.warnings.map((w, i) => <p key={i}>{w}</p>)}</div>
              )}
            </div>
          )}

          {/* Document list */}
          <div className="card overflow-hidden mb-6">
            <div className="px-5 py-4 border-b border-border"><h2 className="font-semibold text-navy">Discovery documents</h2></div>
            {loadingDocs && <div className="px-5 py-10 text-center text-text-secondary text-sm">Loading...</div>}
            {!loadingDocs && documents.length === 0 && <div className="px-5 py-10 text-center text-text-secondary text-sm">No discovery documents yet. Upload a PDF above.</div>}
            {!loadingDocs && documents.length > 0 && (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-off-white">
                    <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Type</th>
                    <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Served</th>
                    <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Due</th>
                    <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide hidden md:table-cell">Response served</th>
                    <th className="w-10" />
                  </tr>
                </thead>
                <tbody>
                  {documents.map(d => (
                    <tr key={d.id} onClick={() => setSelectedDocId(selectedDocId === d.id ? null : d.id)}
                      className={`border-b border-border last:border-0 hover:bg-off-white/60 transition-colors cursor-pointer ${selectedDocId === d.id ? 'bg-off-white' : ''}`}>
                      <td className="px-5 py-3 font-medium text-navy">{TYPE_LABEL[d.request_type] ?? d.request_type}</td>
                      <td className="px-5 py-3 text-text-secondary">{formatDate(d.propounded_date)}</td>
                      <td className="px-5 py-3 text-text-secondary">{formatDate(d.due_date)}</td>
                      <td className="px-5 py-3 text-text-secondary hidden md:table-cell">
                        {d.response_served_date ? formatDate(d.response_served_date) : <span className="text-amber-600 text-xs">Pending</span>}
                      </td>
                      <td className="px-3 py-3 text-right">
                        <button
                          title="Download Word document"
                          className="text-navy/50 hover:text-navy transition-colors"
                          onClick={e => { e.stopPropagation(); downloadDiscoveryDocx(d.id).catch(console.error) }}>
                          <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
                          </svg>
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Items for selected document */}
          {selectedDocId && (
            <div className="space-y-3">
              <h3 className="font-semibold text-navy">
                Request items
                {items.length > 0 && <span className="text-text-secondary font-normal ml-2 text-sm">{items.length} items</span>}
              </h3>

              {loadingItems && <div className="card px-5 py-10 text-center text-text-secondary text-sm">Loading...</div>}
              {!loadingItems && items.length === 0 && <div className="card px-5 py-10 text-center text-text-secondary text-sm">No items found.</div>}

              {!loadingItems && items.map(item => {
                const draft = drafts[item.id] ?? {}
                const isDirty = Object.keys(draft).length > 0

                // Merge draft over saved values for display
                const sourceText      = draft.source_text ?? item.source_text
                const clientNeeded    = draft.client_response_needed ?? item.client_response_needed
                const interpretations = draft.interpretations ?? item.interpretations
                const privileges      = draft.privileges ?? item.privileges
                const objections      = draft.objections ?? item.objections
                const response        = draft.response ?? item.response ?? ''

                return (
                  <div key={item.id} className="card overflow-hidden">
                    {/* Collapsed header */}
                    <button
                      className="w-full flex items-start justify-between gap-4 px-5 py-4 text-left hover:bg-off-white/60 transition-colors"
                      onClick={() => setExpandedItem(expandedItem === item.id ? null : item.id)}>
                      <div className="flex items-center gap-3 min-w-0">
                        <span className="text-xs font-mono text-text-secondary flex-shrink-0">#{item.request_number}</span>
                        <span className="text-sm text-navy truncate">{item.source_text.slice(0, 120)}{item.source_text.length > 120 ? '...' : ''}</span>
                      </div>
                      <div className="flex items-center gap-2 flex-shrink-0">
                        {isDirty && <span className="text-xs text-amber-600">unsaved</span>}
                        {!clientNeeded && <span className="text-xs bg-gray-100 text-gray-600 rounded-full px-2 py-0.5">No client response</span>}
                        {(() => {
                          const s = effectiveStatus(item, draft)
                          return (
                            <span className={`text-xs rounded-full px-2.5 py-1 font-medium ${STATUS_COLOR[s] ?? 'bg-gray-100 text-gray-600'}`}>
                              {s.replace(/_/g, ' ')}
                            </span>
                          )
                        })()}
                        <svg className={`w-4 h-4 text-text-secondary transition-transform ${expandedItem === item.id ? 'rotate-180' : ''}`}
                          fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                        </svg>
                      </div>
                    </button>

                    {/* Expanded editing panel */}
                    {expandedItem === item.id && (
                      <div className="px-5 pb-6 border-t border-border pt-4 space-y-5">

                        {/* Client response toggle */}
                        <label className="flex items-center gap-2 cursor-pointer select-none">
                          <input type="checkbox" className="w-4 h-4 accent-navy" checked={clientNeeded}
                            onChange={e => patchDraft(item.id, { client_response_needed: e.target.checked })} />
                          <span className="text-sm font-medium text-navy">Client response needed</span>
                        </label>

                        {/* Source text (OCR cleanup) */}
                        <div>
                          <label className="label mb-1 block">Request text (edit to clean up OCR errors)</label>
                          <textarea className="input w-full h-28 text-sm font-mono resize-y" value={sourceText}
                            onChange={e => patchDraft(item.id, { source_text: e.target.value })} />
                        </div>

                        {/* Privileges */}
                        {stdPrivileges.length > 0 && (
                          <div>
                            <p className="label mb-2">Privileges</p>
                            <div className="space-y-1.5">
                              {stdPrivileges.map(priv => {
                                const checked = privileges.some(p => p.privilege_name === priv.slug)
                                return (
                                  <label key={priv.slug} className="flex items-start gap-2 cursor-pointer">
                                    <input type="checkbox" className="mt-0.5 w-4 h-4 accent-navy flex-shrink-0" checked={checked}
                                      onChange={e => {
                                        const next = e.target.checked
                                          ? [...privileges, { privilege_name: priv.slug, text: priv.text }]
                                          : privileges.filter(p => p.privilege_name !== priv.slug)
                                        patchDraft(item.id, { privileges: next })
                                      }} />
                                    <span className="text-sm text-navy capitalize">{priv.slug.replace(/-/g, ' ')}</span>
                                  </label>
                                )
                              })}
                            </div>
                          </div>
                        )}

                        {/* Objections */}
                        {stdObjections.length > 0 && (
                          <div>
                            <p className="label mb-2">Objections</p>
                            <div className="space-y-3">
                              {stdObjections.map(obj => {
                                const existing = objections.find(o => o.objection_name === obj.slug)
                                const checked = !!existing
                                return (
                                  <div key={obj.slug}>
                                    <label className="flex items-start gap-2 cursor-pointer">
                                      <input type="checkbox" className="mt-0.5 w-4 h-4 accent-navy flex-shrink-0" checked={checked}
                                        onChange={e => {
                                          const next = e.target.checked
                                            ? [...objections, { objection_name: obj.slug, text: obj.text }]
                                            : objections.filter(o => o.objection_name !== obj.slug)
                                          patchDraft(item.id, { objections: next })
                                        }} />
                                      <span className="text-sm text-navy capitalize">{obj.slug.replace(/-/g, ' ')}</span>
                                    </label>
                                    {checked && (
                                      <textarea className="input w-full h-20 text-sm mt-1 ml-6 resize-y" value={existing!.text}
                                        onChange={e => {
                                          const next = objections.map(o =>
                                            o.objection_name === obj.slug ? { ...o, text: e.target.value } : o
                                          )
                                          patchDraft(item.id, { objections: next })
                                        }} />
                                    )}
                                  </div>
                                )
                              })}
                            </div>
                          </div>
                        )}

                        {/* Interpretations */}
                        <div>
                          <p className="label mb-2">Interpretations</p>
                          <div className="space-y-2">
                            {interpretations.map((text, idx) => (
                              <div key={idx} className="flex gap-2 items-start">
                                <input type="text" className="input flex-1 text-sm" value={text}
                                  onChange={e => {
                                    const next = interpretations.map((t, i) => i === idx ? e.target.value : t)
                                    patchDraft(item.id, { interpretations: next })
                                  }} />
                                <button type="button" className="text-red-500 hover:text-red-700 text-xs mt-2 flex-shrink-0"
                                  onClick={() => patchDraft(item.id, { interpretations: interpretations.filter((_, i) => i !== idx) })}>
                                  Remove
                                </button>
                              </div>
                            ))}
                            <button type="button" className="text-xs text-navy underline"
                              onClick={() => patchDraft(item.id, { interpretations: [...interpretations, ''] })}>
                              + Add interpretation
                            </button>
                          </div>
                        </div>

                        {/* Response */}
                        <div>
                          <label className="label mb-1 block">Attorney response (markdown)</label>
                          <textarea className="input w-full h-36 text-sm resize-y" placeholder="Draft the formal response here..."
                            value={response} onChange={e => patchDraft(item.id, { response: e.target.value })} />
                        </div>

                        {/* Save bar */}
                        <div className="flex items-center gap-3">
                          <button type="button" disabled={!isDirty || saving[item.id]}
                            className="btn-primary text-sm disabled:opacity-50 disabled:cursor-not-allowed"
                            onClick={() => saveItem(item)}>
                            {saving[item.id] ? 'Saving...' : 'Save changes'}
                          </button>
                          {isDirty && !saving[item.id] && (
                            <button type="button" className="text-sm text-text-secondary underline"
                              onClick={() => setDrafts(prev => { const n = { ...prev }; delete n[item.id]; return n })}>
                              Discard
                            </button>
                          )}
                          {saveError[item.id] && <span className="text-xs text-red-600">{saveError[item.id]}</span>}
                        </div>

                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}
    </div>
  )
}
