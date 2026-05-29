import { useEffect, useState, FormEvent, Fragment } from 'react'
import { Link } from 'react-router-dom'
import {
  createStaff, getStaff, updateStaff,
  getAttorneyResponders, setAttorneyResponders,
  setStaffRoles,
  getKbArticles, createKbArticle, updateKbArticle, deleteKbArticle,
  getEditedRuns,
} from '../../lib/api'
import type { Staff, StaffCreatePayload, KbArticle, EditedRunSummary } from '../../types'
import { ASSIGNABLE_STAFF_ROLES } from '../../types/staff'
import StaffForm, {
  EMPTY_STAFF_FORM, StaffFormState,
  staffToFormState, formStateToPayload, staffFormCanSubmit,
} from '../../components/StaffForm'

function fullName(s: Staff) {
  return `${s.name.first_name} ${s.name.last_name}`
}

const ROLE_COLOR: Record<string, string> = {
  attorney:  'bg-navy/10 text-navy',
  paralegal: 'bg-blue-100 text-blue-800',
  admin:     'bg-purple-100 text-purple-800',
}

export default function AdminPage() {
  const [staff, setStaff]     = useState<Staff[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState<string | null>(null)

  // Create
  const [showCreate, setShowCreate]     = useState(false)
  const [createForm, setCreateForm]     = useState<StaffFormState>(EMPTY_STAFF_FORM)
  const [creating, setCreating]         = useState(false)
  const [createError, setCreateError]   = useState<string | null>(null)

  // Edit
  const [editId, setEditId]       = useState<number | null>(null)
  const [editForm, setEditForm]   = useState<StaffFormState>(EMPTY_STAFF_FORM)
  const [saving, setSaving]       = useState(false)
  const [editError, setEditError] = useState<string | null>(null)

  // Responders
  const [responderAttorneyId, setResponderAttorneyId] = useState<number | null>(null)
  const [responderIds, setResponderIds]               = useState<Set<number>>(new Set())
  const [loadingResponders, setLoadingResponders]     = useState(false)
  const [savingResponders, setSavingResponders]       = useState(false)
  const [responderError, setResponderError]           = useState<string | null>(null)

  // User roles (auth) — initial values come from staff.roles on the list response,
  // so the column populates without per-row fetches.
  const [rolesStaffId, setRolesStaffId] = useState<number | null>(null)
  const [stagedRoles, setStagedRoles]   = useState<Set<string>>(new Set())
  const [savingRoles, setSavingRoles]   = useState(false)
  const [rolesError, setRolesError]     = useState<string | null>(null)

  // Knowledge base
  const [kbArticles, setKbArticles]   = useState<KbArticle[]>([])
  const [kbLoading, setKbLoading]     = useState(true)
  const [kbError, setKbError]         = useState<string | null>(null)

  // Recent draft edits (HITL tuning signal)
  const [editedRuns, setEditedRuns]   = useState<EditedRunSummary[]>([])
  const [editsLoading, setEditsLoading] = useState(true)
  const [editsError, setEditsError]   = useState<string | null>(null)
  const [expandedRunId, setExpandedRunId] = useState<number | null>(null)
  const [kbEditId, setKbEditId]       = useState<number | 'new' | null>(null)
  const [kbDraft, setKbDraft]         = useState<{ topic: string; subtopic: string; body_md: string; active: boolean; sort_order: string }>(
    { topic: '', subtopic: '', body_md: '', active: true, sort_order: '0' }
  )
  const [kbSaving, setKbSaving]       = useState(false)
  const [kbEditError, setKbEditError] = useState<string | null>(null)

  useEffect(() => {
    getStaff()
      .then(setStaff)
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load staff'))
      .finally(() => setLoading(false))
    getKbArticles()
      .then(setKbArticles)
      .catch(e => setKbError(e instanceof Error ? e.message : 'Failed to load KB articles'))
      .finally(() => setKbLoading(false))
    getEditedRuns(20)
      .then(setEditedRuns)
      .catch(e => setEditsError(e instanceof Error ? e.message : 'Failed to load draft edits'))
      .finally(() => setEditsLoading(false))
  }, [])

  const linked   = staff.filter(s => s.supabase_uid).length
  const unlinked = staff.filter(s => !s.supabase_uid).length
  const attorneys        = staff.filter(s => s.role === 'attorney')
  const nonAttorneyStaff = staff.filter(s => s.role !== 'attorney')

  function openEdit(s: Staff) {
    if (editId === s.id) { setEditId(null); return }
    setEditId(s.id)
    setEditForm(staffToFormState(s))
    setEditError(null)
    setShowCreate(false)
  }

  async function handleCreate(e: FormEvent) {
    e.preventDefault()
    setCreating(true); setCreateError(null)
    try {
      const payload = formStateToPayload(createForm) as StaffCreatePayload
      const created = await createStaff(payload)
      setStaff(prev => [...prev, created])
      setShowCreate(false)
      setCreateForm(EMPTY_STAFF_FORM)
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'Failed to create staff member')
    } finally {
      setCreating(false)
    }
  }

  async function handleEdit(e: FormEvent) {
    e.preventDefault()
    if (editId === null) return
    setSaving(true); setEditError(null)
    try {
      const payload = formStateToPayload(editForm)
      const updated = await updateStaff(editId, payload as unknown as Record<string, unknown>)
      setStaff(prev => prev.map(s => s.id === editId ? updated : s))
      setEditId(null)
    } catch (err) {
      setEditError(err instanceof Error ? err.message : 'Failed to update staff member')
    } finally {
      setSaving(false)
    }
  }

  function openResponderEditor(attorneyId: number) {
    if (responderAttorneyId === attorneyId) { setResponderAttorneyId(null); return }
    setResponderAttorneyId(attorneyId)
    setResponderError(null)
    setLoadingResponders(true)
    getAttorneyResponders(attorneyId)
      .then(set => setResponderIds(new Set(set.responder_staff_ids)))
      .catch(e => setResponderError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoadingResponders(false))
  }

  function toggleResponder(id: number) {
    setResponderIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  async function handleSaveResponders() {
    if (responderAttorneyId === null) return
    setSavingResponders(true); setResponderError(null)
    try {
      await setAttorneyResponders(responderAttorneyId, Array.from(responderIds))
      setResponderAttorneyId(null)
    } catch (e) {
      setResponderError(e instanceof Error ? e.message : 'Failed to save responders')
    } finally {
      setSavingResponders(false)
    }
  }

  function openRolesEditor(s: Staff) {
    if (rolesStaffId === s.id) { setRolesStaffId(null); return }
    setRolesStaffId(s.id)
    setRolesError(null)
    setStagedRoles(new Set(s.roles))
  }

  function toggleStagedRole(role: string) {
    setStagedRoles(prev => {
      const next = new Set(prev)
      if (next.has(role)) next.delete(role); else next.add(role)
      return next
    })
  }

  async function handleSaveRoles() {
    if (rolesStaffId === null) return
    setSavingRoles(true); setRolesError(null)
    try {
      const result = await setStaffRoles(rolesStaffId, Array.from(stagedRoles))
      setStaff(prev => prev.map(s => s.id === rolesStaffId ? { ...s, roles: result.roles } : s))
      setRolesStaffId(null)
    } catch (e) {
      setRolesError(e instanceof Error ? e.message : 'Failed to save roles')
    } finally {
      setSavingRoles(false)
    }
  }

  function openKbEditor(article: KbArticle | 'new') {
    setKbEditError(null)
    if (article === 'new') {
      if (kbEditId === 'new') { setKbEditId(null); return }
      setKbEditId('new')
      setKbDraft({ topic: '', subtopic: '', body_md: '', active: true, sort_order: '0' })
    } else {
      if (kbEditId === article.id) { setKbEditId(null); return }
      setKbEditId(article.id)
      setKbDraft({
        topic: article.topic,
        subtopic: article.subtopic ?? '',
        body_md: article.body_md,
        active: article.active,
        sort_order: String(article.sort_order),
      })
    }
  }

  async function handleSaveKb() {
    if (!kbDraft.topic.trim() || !kbDraft.body_md.trim()) {
      setKbEditError('Topic and body are required.')
      return
    }
    setKbSaving(true); setKbEditError(null)
    try {
      const payload = {
        topic: kbDraft.topic.trim(),
        subtopic: kbDraft.subtopic.trim() || null,
        body_md: kbDraft.body_md,
        active: kbDraft.active,
        sort_order: parseInt(kbDraft.sort_order, 10) || 0,
      }
      if (kbEditId === 'new') {
        const created = await createKbArticle(payload)
        setKbArticles(prev => [...prev, created])
      } else if (typeof kbEditId === 'number') {
        const updated = await updateKbArticle(kbEditId, payload)
        setKbArticles(prev => prev.map(a => a.id === kbEditId ? updated : a))
      }
      setKbEditId(null)
    } catch (e) {
      setKbEditError(e instanceof Error ? e.message : 'Failed to save article')
    } finally {
      setKbSaving(false)
    }
  }

  async function handleDeleteKb(articleId: number) {
    if (!confirm('Delete this KB article? The agent will stop seeing it on the next invocation.')) return
    try {
      await deleteKbArticle(articleId)
      setKbArticles(prev => prev.filter(a => a.id !== articleId))
      if (kbEditId === articleId) setKbEditId(null)
    } catch (e) {
      setKbEditError(e instanceof Error ? e.message : 'Failed to delete article')
    }
  }

  return (
    <div className="px-6 py-8 max-w-5xl mx-auto">
      <div className="mb-8 flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl text-navy">Administration</h1>
          <p className="text-text-secondary mt-1">Manage staff accounts and firm settings.</p>
        </div>
        <button className="btn-primary" onClick={() => {
          setShowCreate(v => !v)
          setEditId(null)
          if (!showCreate) { setCreateForm(EMPTY_STAFF_FORM); setCreateError(null) }
        }}>
          {showCreate ? 'Cancel' : 'New staff member'}
        </button>
      </div>

      {/* Stat strip */}
      <div className="grid grid-cols-3 gap-4 mb-8">
        <div className="card p-5">
          <p className="text-xs text-text-secondary uppercase tracking-widest font-semibold mb-1">Total staff</p>
          <p className="font-display text-3xl text-navy">{loading ? '—' : staff.length}</p>
        </div>
        <div className="card p-5">
          <p className="text-xs text-text-secondary uppercase tracking-widest font-semibold mb-1">Linked accounts</p>
          <p className="font-display text-3xl text-green-700">{loading ? '—' : linked}</p>
        </div>
        <div className="card p-5">
          <p className="text-xs text-text-secondary uppercase tracking-widest font-semibold mb-1">Pending login</p>
          <p className="font-display text-3xl text-amber-600">{loading ? '—' : unlinked}</p>
        </div>
      </div>

      {/* Create form */}
      {showCreate && (
        <div className="card p-5 mb-8">
          <h2 className="font-semibold text-navy mb-4">New staff member</h2>
          <StaffForm
            value={createForm}
            onChange={setCreateForm}
            onSubmit={handleCreate}
            onCancel={() => { setShowCreate(false); setCreateForm(EMPTY_STAFF_FORM) }}
            submitLabel="Create staff member"
            submitting={creating}
            canSubmit={staffFormCanSubmit(createForm)}
            error={createError}
          />
        </div>
      )}

      {/* Staff table */}
      <div className="card overflow-hidden">
        <div className="px-5 py-4 border-b border-border">
          <h2 className="font-semibold text-navy">Staff members</h2>
        </div>

        {loading && <div className="px-5 py-10 text-center text-text-secondary text-sm">Loading…</div>}
        {error && <div className="px-5 py-10 text-center text-red-600 text-sm">{error}</div>}
        {!loading && !error && staff.length === 0 && (
          <div className="px-5 py-10 text-center text-text-secondary text-sm">No staff members found.</div>
        )}
        {!loading && !error && staff.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-off-white">
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Name</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide hidden md:table-cell">Auth email</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Role</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Account</th>
                <th className="text-right px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide hidden lg:table-cell">Billing rate</th>
              </tr>
            </thead>
            <tbody>
              {staff.map(s => (
                <>
                  <tr key={s.id}
                    onClick={() => openEdit(s)}
                    className="border-b border-border last:border-0 hover:bg-off-white/60 transition-colors cursor-pointer">
                    <td className="px-5 py-3 font-medium text-navy">{fullName(s)}</td>
                    <td className="px-5 py-3 text-text-secondary hidden md:table-cell">
                      {s.auth_email ?? <span className="text-xs text-text-secondary/50 italic">not set</span>}
                    </td>
                    <td className="px-5 py-3">
                      <span className={`text-xs rounded-full px-2.5 py-1 font-medium capitalize ${ROLE_COLOR[s.role] ?? 'bg-gray-100 text-gray-600'}`}>
                        {s.role}
                      </span>
                    </td>
                    <td className="px-5 py-3">
                      {s.supabase_uid ? (
                        <span className="inline-flex items-center gap-1 text-xs text-green-700">
                          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                          </svg>
                          Linked
                        </span>
                      ) : (
                        <span className="text-xs text-amber-600">Pending first login</span>
                      )}
                    </td>
                    <td className="px-5 py-3 text-right text-text-secondary hidden lg:table-cell">
                      {s.default_billing_rate != null
                        ? `$${s.default_billing_rate.toFixed(0)}/hr`
                        : <span className="text-xs italic text-text-secondary/50">—</span>
                      }
                    </td>
                  </tr>
                  {editId === s.id && (
                    <tr key={`edit-${s.id}`}>
                      <td colSpan={5} className="px-5 py-4 bg-off-white/50 border-b border-border">
                        <div onClick={e => e.stopPropagation()}>
                          <h3 className="font-semibold text-navy text-sm mb-4">Edit {fullName(s)}</h3>
                          <StaffForm
                            value={editForm}
                            onChange={setEditForm}
                            onSubmit={handleEdit}
                            onCancel={() => setEditId(null)}
                            submitLabel="Save changes"
                            submitting={saving}
                            canSubmit={staffFormCanSubmit(editForm)}
                            error={editError}
                          />
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Lead responders */}
      <div className="card overflow-hidden mt-8">
        <div className="px-5 py-4 border-b border-border">
          <h2 className="font-semibold text-navy">Lead responders</h2>
          <p className="text-sm text-text-secondary mt-1">
            Pick which support staff handle each attorney's PNCs. Responders receive
            escalation notifications by email + Telegram and can see leads assigned
            to that attorney. The <code className="font-mono text-xs bg-white rounded px-1 py-0.5 border border-border">www</code> Firm
            attorney's responders handle unattributed leads.
          </p>
        </div>

        {!loading && attorneys.length === 0 && (
          <div className="px-5 py-10 text-center text-text-secondary text-sm">
            No attorneys yet. Create a staff member with role=attorney first.
          </div>
        )}

        {!loading && attorneys.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-off-white">
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Attorney</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide hidden md:table-cell">Slug</th>
                <th className="text-right px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Action</th>
              </tr>
            </thead>
            <tbody>
              {attorneys.map(a => (
                <Fragment key={a.id}>
                  <tr onClick={() => openResponderEditor(a.id)}
                      className="border-b border-border last:border-0 hover:bg-off-white/60 transition-colors cursor-pointer">
                    <td className="px-5 py-3 font-medium text-navy">{fullName(a)}</td>
                    <td className="px-5 py-3 text-text-secondary hidden md:table-cell">
                      <span className="text-xs bg-gray-100 text-gray-700 rounded px-1.5 py-0.5 font-mono">{a.slug}</span>
                    </td>
                    <td className="px-5 py-3 text-right text-xs text-text-secondary">
                      {responderAttorneyId === a.id ? 'Editing…' : 'Click to edit'}
                    </td>
                  </tr>
                  {responderAttorneyId === a.id && (
                    <tr>
                      <td colSpan={3} className="px-5 py-4 bg-off-white/50 border-b border-border">
                        <div onClick={e => e.stopPropagation()}>
                          <h3 className="font-semibold text-navy text-sm mb-3">Responders for {fullName(a)}</h3>

                          {loadingResponders && (
                            <p className="text-sm text-text-secondary">Loading…</p>
                          )}

                          {!loadingResponders && nonAttorneyStaff.length === 0 && (
                            <p className="text-sm text-text-secondary">
                              No non-attorney staff to choose from. Create paralegal or admin staff first.
                            </p>
                          )}

                          {!loadingResponders && nonAttorneyStaff.length > 0 && (
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-3">
                              {nonAttorneyStaff.map(s => (
                                <label key={s.id} className="flex items-center gap-2 text-sm cursor-pointer">
                                  <input
                                    type="checkbox"
                                    checked={responderIds.has(s.id)}
                                    onChange={() => toggleResponder(s.id)}
                                    className="rounded border-border"
                                  />
                                  <span className="text-navy">
                                    {fullName(s)}
                                    <span className="text-xs text-text-secondary capitalize ml-1">({s.role})</span>
                                  </span>
                                </label>
                              ))}
                            </div>
                          )}

                          {responderError && <p className="text-sm text-red-600 mb-2">{responderError}</p>}

                          <div className="flex gap-2 pt-2 border-t border-border">
                            <button
                              onClick={handleSaveResponders}
                              disabled={savingResponders || loadingResponders}
                              className="btn-primary"
                            >
                              {savingResponders ? 'Saving…' : 'Save responders'}
                            </button>
                            <button
                              onClick={() => setResponderAttorneyId(null)}
                              className="btn-secondary"
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* User roles (auth) */}
      <div className="card overflow-hidden mt-8">
        <div className="px-5 py-4 border-b border-border">
          <h2 className="font-semibold text-navy">User roles</h2>
          <p className="text-sm text-text-secondary mt-1">
            Grant or revoke a staff member's auth roles. Each ticked role is its own
            row in <code className="font-mono text-xs bg-white rounded px-1 py-0.5 border border-border">user_roles</code> —
            a single person can hold multiple (e.g. attorney + admin). This is the
            access-control surface; <code className="font-mono text-xs bg-white rounded px-1 py-0.5 border border-border">staff.role</code> is
            still managed in the staff edit form above for billing/display.
          </p>
        </div>

        {!loading && staff.length === 0 && (
          <div className="px-5 py-10 text-center text-text-secondary text-sm">No staff yet.</div>
        )}

        {!loading && staff.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-off-white">
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Staff</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide hidden md:table-cell">Slug</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Roles</th>
                <th className="text-right px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Action</th>
              </tr>
            </thead>
            <tbody>
              {staff.map(s => (
                  <Fragment key={s.id}>
                    <tr onClick={() => openRolesEditor(s)}
                        className="border-b border-border last:border-0 hover:bg-off-white/60 transition-colors cursor-pointer">
                      <td className="px-5 py-3 font-medium text-navy">{fullName(s)}</td>
                      <td className="px-5 py-3 text-text-secondary hidden md:table-cell">
                        <span className="text-xs bg-gray-100 text-gray-700 rounded px-1.5 py-0.5 font-mono">{s.slug}</span>
                      </td>
                      <td className="px-5 py-3">
                        {s.roles.length === 0 ? (
                          <span className="text-xs text-amber-700">no auth roles</span>
                        ) : (
                          <span className="flex flex-wrap gap-1">
                            {s.roles.map(r => (
                              <span key={r} className={`text-xs rounded-full px-2 py-0.5 font-medium capitalize ${ROLE_COLOR[r] ?? 'bg-gray-100 text-gray-600'}`}>
                                {r}
                              </span>
                            ))}
                          </span>
                        )}
                      </td>
                      <td className="px-5 py-3 text-right text-xs text-text-secondary">
                        {rolesStaffId === s.id ? 'Editing…' : 'Click to edit'}
                      </td>
                    </tr>
                    {rolesStaffId === s.id && (
                      <tr>
                        <td colSpan={4} className="px-5 py-4 bg-off-white/50 border-b border-border">
                          <div onClick={e => e.stopPropagation()}>
                            <h3 className="font-semibold text-navy text-sm mb-3">Auth roles for {fullName(s)}</h3>

                            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mb-3">
                              {ASSIGNABLE_STAFF_ROLES.map(r => (
                                <label key={r} className="flex items-center gap-2 text-sm cursor-pointer">
                                  <input
                                    type="checkbox"
                                    checked={stagedRoles.has(r)}
                                    onChange={() => toggleStagedRole(r)}
                                    className="rounded border-border"
                                  />
                                  <span className="text-navy capitalize">{r}</span>
                                </label>
                              ))}
                            </div>

                            {rolesError && <p className="text-sm text-red-600 mb-2">{rolesError}</p>}

                            <div className="flex gap-2 pt-2 border-t border-border">
                              <button
                                onClick={handleSaveRoles}
                                disabled={savingRoles}
                                className="btn-primary"
                              >
                                {savingRoles ? 'Saving…' : 'Save roles'}
                              </button>
                              <button
                                onClick={() => setRolesStaffId(null)}
                                className="btn-secondary"
                              >
                                Cancel
                              </button>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Recent draft edits — autonomy-graduation eval data */}
      <div className="card overflow-hidden mt-8">
        <div className="px-5 py-4 border-b border-border">
          <h2 className="font-semibold text-navy">Recent draft edits</h2>
          <p className="text-sm text-text-secondary mt-1">
            Drafts staff edited before sending. The agent's explanation
            (filled in by a background job) tells you what changed and why
            it might matter for prompt tuning. Edit rate trending toward
            zero on a class of message is your signal that the agent can
            handle it autonomously.
          </p>
        </div>

        {editsLoading && <div className="px-5 py-10 text-center text-text-secondary text-sm">Loading…</div>}
        {editsError && <div className="px-5 py-10 text-center text-red-600 text-sm">{editsError}</div>}
        {!editsLoading && !editsError && editedRuns.length === 0 && (
          <div className="px-5 py-10 text-center text-text-secondary text-sm">
            No edited drafts yet. When staff edits a draft before sending, it'll show up here within a poll cycle.
          </div>
        )}

        {!editsLoading && !editsError && editedRuns.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-off-white">
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Lead</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Edited</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Explanation</th>
                <th className="text-right px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Action</th>
              </tr>
            </thead>
            <tbody>
              {editedRuns.map(r => (
                <Fragment key={r.id}>
                  <tr className="border-b border-border last:border-0 hover:bg-off-white/60 transition-colors">
                    <td className="px-5 py-3">
                      <Link to={`/app/leads/${r.foreign_session_uuid}`} className="font-medium text-navy hover:underline">
                        {r.lead_name ?? '(unnamed)'}
                      </Link>
                      {r.lead_email && <div className="text-xs text-text-secondary">{r.lead_email}</div>}
                    </td>
                    <td className="px-5 py-3 text-text-secondary whitespace-nowrap">{fmtDate(r.updated_at)}</td>
                    <td className="px-5 py-3 text-text-secondary">
                      {r.edit_explanation ?? (
                        <span className="text-xs italic text-text-secondary/60">explanation pending…</span>
                      )}
                    </td>
                    <td className="px-5 py-3 text-right">
                      <button
                        onClick={() => setExpandedRunId(expandedRunId === r.id ? null : r.id)}
                        className="text-xs text-navy hover:underline"
                      >
                        {expandedRunId === r.id ? 'Hide diff' : 'View diff'}
                      </button>
                    </td>
                  </tr>
                  {expandedRunId === r.id && (
                    <tr>
                      <td colSpan={4} className="px-5 py-4 bg-off-white/50 border-b border-border">
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                          <div>
                            <h4 className="text-xs uppercase tracking-widest font-semibold text-text-secondary mb-2">AI draft</h4>
                            <div className="rounded border border-border bg-white p-3 text-xs whitespace-pre-wrap font-mono text-text-primary max-h-80 overflow-y-auto">
                              {r.draft_body || '(empty)'}
                            </div>
                          </div>
                          <div>
                            <h4 className="text-xs uppercase tracking-widest font-semibold text-text-secondary mb-2">What actually went out</h4>
                            <div className="rounded border border-green-200 bg-green-50/30 p-3 text-xs whitespace-pre-wrap font-mono text-text-primary max-h-80 overflow-y-auto">
                              {r.sent_body || '(empty)'}
                            </div>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Knowledge base */}
      <div className="card overflow-hidden mt-8">
        <div className="px-5 py-4 border-b border-border flex items-start justify-between gap-3">
          <div>
            <h2 className="font-semibold text-navy">Knowledge base</h2>
            <p className="text-sm text-text-secondary mt-1">
              Articles the CRM agent draws on when composing replies to PNCs.
              Keep entries short and factual (hours, locations, meeting types,
              fee outlines, practice areas, geography). Markdown is supported.
              Inactive articles are excluded from the agent's prompt without
              being deleted.
            </p>
          </div>
          <button className="btn-primary whitespace-nowrap" onClick={() => openKbEditor('new')}>
            {kbEditId === 'new' ? 'Cancel' : 'New article'}
          </button>
        </div>

        {kbEditId === 'new' && (
          <div className="px-5 py-4 bg-off-white/50 border-b border-border">
            <h3 className="font-semibold text-navy text-sm mb-3">New KB article</h3>
            <KbEditorFields draft={kbDraft} setDraft={setKbDraft} />
            {kbEditError && <p className="text-sm text-red-600 mb-2">{kbEditError}</p>}
            <div className="flex gap-2 pt-2 border-t border-border">
              <button onClick={handleSaveKb} disabled={kbSaving} className="btn-primary">
                {kbSaving ? 'Saving…' : 'Create article'}
              </button>
              <button onClick={() => setKbEditId(null)} className="btn-secondary">Cancel</button>
            </div>
          </div>
        )}

        {kbLoading && <div className="px-5 py-10 text-center text-text-secondary text-sm">Loading…</div>}
        {kbError && <div className="px-5 py-10 text-center text-red-600 text-sm">{kbError}</div>}
        {!kbLoading && !kbError && kbArticles.length === 0 && (
          <div className="px-5 py-10 text-center text-text-secondary text-sm">
            No KB articles yet. Click "New article" to add the first one (operating hours, fees, practice areas, etc.).
          </div>
        )}

        {!kbLoading && !kbError && kbArticles.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-off-white">
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide w-12">#</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Topic</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide hidden md:table-cell">Subtopic</th>
                <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Status</th>
                <th className="text-right px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Action</th>
              </tr>
            </thead>
            <tbody>
              {kbArticles.map(a => (
                <Fragment key={a.id}>
                  <tr onClick={() => openKbEditor(a)}
                      className="border-b border-border last:border-0 hover:bg-off-white/60 transition-colors cursor-pointer">
                    <td className="px-5 py-3 text-text-secondary text-xs">{a.sort_order}</td>
                    <td className="px-5 py-3 font-medium text-navy">{a.topic}</td>
                    <td className="px-5 py-3 text-text-secondary hidden md:table-cell">{a.subtopic ?? '—'}</td>
                    <td className="px-5 py-3">
                      {a.active
                        ? <span className="text-xs rounded-full px-2 py-0.5 font-medium bg-green-100 text-green-800">active</span>
                        : <span className="text-xs rounded-full px-2 py-0.5 font-medium bg-gray-100 text-gray-600">inactive</span>}
                    </td>
                    <td className="px-5 py-3 text-right text-xs text-text-secondary">
                      {kbEditId === a.id ? 'Editing…' : 'Click to edit'}
                    </td>
                  </tr>
                  {kbEditId === a.id && (
                    <tr>
                      <td colSpan={5} className="px-5 py-4 bg-off-white/50 border-b border-border">
                        <div onClick={e => e.stopPropagation()}>
                          <h3 className="font-semibold text-navy text-sm mb-3">Edit {a.topic}{a.subtopic ? ` › ${a.subtopic}` : ''}</h3>
                          <KbEditorFields draft={kbDraft} setDraft={setKbDraft} />
                          {kbEditError && <p className="text-sm text-red-600 mb-2">{kbEditError}</p>}
                          <div className="flex gap-2 pt-2 border-t border-border">
                            <button onClick={handleSaveKb} disabled={kbSaving} className="btn-primary">
                              {kbSaving ? 'Saving…' : 'Save changes'}
                            </button>
                            <button onClick={() => setKbEditId(null)} className="btn-secondary">Cancel</button>
                            <button onClick={() => handleDeleteKb(a.id)}
                                    className="ml-auto text-sm text-red-600 hover:underline">
                              Delete
                            </button>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

interface KbDraftState {
  topic: string
  subtopic: string
  body_md: string
  active: boolean
  sort_order: string
}

function KbEditorFields({ draft, setDraft }: {
  draft: KbDraftState
  setDraft: (next: KbDraftState) => void
}) {
  const set = <K extends keyof KbDraftState>(k: K, v: KbDraftState[K]) =>
    setDraft({ ...draft, [k]: v })
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="md:col-span-2">
          <label className="label">Topic *</label>
          <input className="input mt-1" placeholder="e.g. Office, Fees, Practice areas"
            value={draft.topic} onChange={e => set('topic', e.target.value)} />
        </div>
        <div>
          <label className="label">Sort order</label>
          <input className="input mt-1" type="number" min="0" value={draft.sort_order}
            onChange={e => set('sort_order', e.target.value)} />
        </div>
      </div>
      <div>
        <label className="label">Subtopic</label>
        <input className="input mt-1" placeholder="optional, e.g. Hours, Locations"
          value={draft.subtopic} onChange={e => set('subtopic', e.target.value)} />
      </div>
      <div>
        <label className="label">Body (Markdown) *</label>
        <textarea className="input mt-1 font-mono text-xs" rows={10}
          value={draft.body_md} onChange={e => set('body_md', e.target.value)}
          placeholder="**Office hours**&#10;- Mon–Fri 8:30 AM – 5:30 PM Central&#10;- Closed weekends and federal holidays" />
      </div>
      <label className="flex items-center gap-2 text-sm cursor-pointer">
        <input type="checkbox" checked={draft.active} onChange={e => set('active', e.target.checked)}
          className="rounded border-border" />
        <span className="text-navy">Active (included in the agent's prompt)</span>
      </label>
    </div>
  )
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}
