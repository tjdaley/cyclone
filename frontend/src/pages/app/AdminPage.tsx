import { useEffect, useState, FormEvent, Fragment } from 'react'
import {
  createStaff, getStaff, updateStaff,
  getAttorneyResponders, setAttorneyResponders,
  setStaffRoles,
} from '../../lib/api'
import type { Staff, StaffCreatePayload } from '../../types'
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

  useEffect(() => {
    getStaff()
      .then(setStaff)
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load staff'))
      .finally(() => setLoading(false))
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
    </div>
  )
}
