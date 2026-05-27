import { useEffect, useState, FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  getLead, getLeadActions,
  updateLeadStatus, assignLead, updateLeadPriority, setLeadFollowUp,
  toggleLeadAgent, addLeadNote, addLeadAction,
  getStaff,
} from '../../lib/api'
import type {
  LeadDetail, LeadAction, LeadStatus, LeadPriority, LeadActionType, DismissalReason, Staff,
} from '../../types'

const STATUS_OPTIONS: LeadStatus[] = [
  'new',
  'attempted',
  'contacted',
  'qualified',
  'disqualified',
  'consultation_scheduled',
  'consulted',
  'engaged',
  'lost',
  'nurture',
]

const DISMISSAL_REASONS: { value: DismissalReason; label: string }[] = [
  { value: 'subject_matter', label: 'Subject matter' },
  { value: 'income',         label: 'Income' },
  { value: 'spam',           label: 'Spam' },
  { value: 'other',          label: 'Other' },
]

const PRIORITY_OPTIONS: LeadPriority[] = ['low', 'normal', 'high']

const MANUAL_ACTIONS: { type: LeadActionType; label: string }[] = [
  { type: 'call_attempted',  label: 'Logged call attempt' },
  { type: 'call_connected',  label: 'Logged call' },
  { type: 'voicemail_left',  label: 'Left voicemail' },
  { type: 'email_sent',      label: 'Sent email' },
  { type: 'text_sent',       label: 'Sent text' },
]

const ACTION_LABELS: Record<LeadActionType, string> = {
  call_attempted: 'Call attempted',
  call_connected: 'Call connected',
  voicemail_left: 'Voicemail left',
  email_sent: 'Email sent',
  email_received: 'Email received',
  text_sent: 'Text sent',
  text_received: 'Text received',
  note: 'Note',
  status_change: 'Status changed',
  assigned: 'Assignment changed',
  priority_change: 'Priority changed',
  consultation_scheduled: 'Consultation scheduled',
  consultation_held: 'Consultation held',
  conflict_check_run: 'Conflict check run',
  converted: 'Converted to client',
  agent_escalated: 'Agent escalated',
  follow_up_set: 'Follow-up set',
}

function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

function staffName(staff: Staff[], id: number | null): string {
  if (id == null) return 'Unassigned'
  const s = staff.find(x => x.id === id)
  return s ? `${s.name.first_name} ${s.name.last_name}` : `Staff #${id}`
}

export default function LeadDetailPage() {
  const { sessionUuid } = useParams<{ sessionUuid: string }>()
  const [lead, setLead]         = useState<LeadDetail | null>(null)
  const [actions, setActions]   = useState<LeadAction[]>([])
  const [staff, setStaff]       = useState<Staff[]>([])
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState<string | null>(null)
  const [busy, setBusy]         = useState(false)

  // Note input
  const [noteText, setNoteText] = useState('')

  // Follow-up input
  const [followUpAt, setFollowUpAt]     = useState('')
  const [followUpNote, setFollowUpNote] = useState('')

  // Dismissal reason + note (when status is 'disqualified')
  const [dismissalReason, setDismissalReason] = useState<DismissalReason | ''>('')
  const [dismissalNote, setDismissalNote]     = useState('')

  useEffect(() => {
    if (!sessionUuid) return
    setLoading(true); setError(null)
    Promise.all([getLead(sessionUuid), getLeadActions(sessionUuid), getStaff()])
      .then(([d, a, s]) => {
        setLead(d); setActions(a); setStaff(s)
        setDismissalReason(d.dismissal_reason ?? '')
        setDismissalNote(d.dismissal_note ?? '')
      })
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [sessionUuid])

  async function refreshActions() {
    if (!sessionUuid) return
    const a = await getLeadActions(sessionUuid)
    setActions(a)
  }

  async function saveStatus(
    status: LeadStatus,
    reason: DismissalReason | '' ,
    note: string,
  ) {
    if (!sessionUuid) return
    setBusy(true)
    try {
      const isDQ = status === 'disqualified'
      const updated = await updateLeadStatus(sessionUuid, {
        status,
        dismissal_reason: isDQ ? (reason || null) : null,
        dismissal_note: isDQ ? (note.trim() || null) : null,
      })
      setLead(updated)
      if (!isDQ) { setDismissalReason(''); setDismissalNote('') }
      await refreshActions()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update status')
    } finally { setBusy(false) }
  }

  async function handleAssign(newAssigneeId: number | null) {
    if (!sessionUuid) return
    setBusy(true)
    try {
      const updated = await assignLead(sessionUuid, { staff_id: newAssigneeId })
      setLead(updated)
      await refreshActions()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to assign')
    } finally { setBusy(false) }
  }

  async function handlePriority(newPriority: LeadPriority) {
    if (!sessionUuid) return
    setBusy(true)
    try {
      const updated = await updateLeadPriority(sessionUuid, { priority: newPriority })
      setLead(updated)
      await refreshActions()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update priority')
    } finally { setBusy(false) }
  }

  async function handleFollowUp(e: FormEvent) {
    e.preventDefault()
    if (!sessionUuid) return
    setBusy(true)
    try {
      const iso = followUpAt ? new Date(followUpAt).toISOString() : null
      const updated = await setLeadFollowUp(sessionUuid, { next_action_at: iso, next_action_note: followUpNote || null })
      setLead(updated)
      setFollowUpAt(''); setFollowUpNote('')
      await refreshActions()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to set follow-up')
    } finally { setBusy(false) }
  }

  async function handleToggleAgent() {
    if (!lead || !sessionUuid) return
    setBusy(true)
    try {
      const updated = await toggleLeadAgent(sessionUuid, { agent_enabled: !lead.agent_enabled })
      setLead(updated)
      await refreshActions()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to toggle agent')
    } finally { setBusy(false) }
  }

  async function handleAddNote(e: FormEvent) {
    e.preventDefault()
    if (!sessionUuid || !noteText.trim()) return
    setBusy(true)
    try {
      await addLeadNote(sessionUuid, { body: noteText.trim() })
      setNoteText('')
      await refreshActions()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to add note')
    } finally { setBusy(false) }
  }

  async function handleManualAction(type: LeadActionType) {
    if (!sessionUuid) return
    setBusy(true)
    try {
      await addLeadAction(sessionUuid, { action_type: type })
      await refreshActions()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to log action')
    } finally { setBusy(false) }
  }

  if (loading) {
    return <div className="px-6 py-12 text-center text-text-secondary text-sm">Loading…</div>
  }
  if (error || !lead) {
    return (
      <div className="px-6 py-12 max-w-3xl mx-auto">
        <Link to="/app/leads" className="text-sm text-navy hover:underline">← Back to leads</Link>
        <div className="card p-6 mt-4 text-red-600">{error ?? 'Lead not found'}</div>
      </div>
    )
  }

  return (
    <div className="px-6 py-8 max-w-6xl mx-auto">
      <Link to="/app/leads" className="text-sm text-navy hover:underline">← Back to leads</Link>

      {/* Header */}
      <div className="mt-3 mb-6 flex flex-col md:flex-row md:items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-3xl text-navy">{lead.full_name ?? '(no name)'}</h1>
          <div className="text-text-secondary text-sm mt-1">
            {lead.email && <span>{lead.email}</span>}
            {lead.email && lead.telephone && <span className="mx-1">·</span>}
            {lead.telephone && <span>{lead.telephone}</span>}
            {lead.attorney_slug && <span className="ml-3 text-xs bg-gray-100 text-gray-700 rounded px-1.5 py-0.5">{lead.attorney_slug}</span>}
          </div>
          <div className="text-xs text-text-secondary mt-1">Captured {fmtDateTime(lead.captured_at)}</div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: lead context */}
        <div className="lg:col-span-2 space-y-6">
          <section className="card p-5">
            <h2 className="font-semibold text-navy mb-3">Source</h2>
            <dl className="text-sm grid grid-cols-2 gap-y-2 gap-x-4">
              <dt className="text-text-secondary">Lead source</dt>
              <dd className="text-navy">{lead.lead_source ?? '—'}</dd>

              <dt className="text-text-secondary">Page</dt>
              <dd className="text-navy">{lead.url_path ?? '—'}</dd>

              <dt className="text-text-secondary">Referrer</dt>
              <dd className="text-navy truncate" title={lead.referrer ?? ''}>{lead.referrer ?? '—'}</dd>

              <dt className="text-text-secondary">Location</dt>
              <dd className="text-navy">
                {[lead.city, lead.state, lead.country, lead.zip].filter(Boolean).join(', ') || '—'}
              </dd>

              <dt className="text-text-secondary">Audit score</dt>
              <dd className="text-navy">{lead.audit_score ?? '—'}</dd>
            </dl>
          </section>

          {lead.conflict_summary && (
            <section className="card p-5">
              <h2 className="font-semibold text-navy mb-3">Lead's summary</h2>
              <div className="prose prose-sm max-w-none prose-headings:text-navy prose-p:text-navy prose-li:text-navy prose-strong:text-navy prose-table:text-sm">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{lead.conflict_summary}</ReactMarkdown>
              </div>
            </section>
          )}

          {/* Activity timeline */}
          <section className="card p-5">
            <h2 className="font-semibold text-navy mb-4">Activity</h2>

            {/* Add note */}
            <form onSubmit={handleAddNote} className="mb-4 flex gap-2">
              <input
                className="input flex-1"
                placeholder="Add an internal note…"
                value={noteText}
                onChange={e => setNoteText(e.target.value)}
                disabled={busy}
              />
              <button className="btn-primary" disabled={busy || !noteText.trim()}>Add note</button>
            </form>

            {/* Manual action buttons */}
            <div className="flex flex-wrap gap-2 mb-5">
              {MANUAL_ACTIONS.map(a => (
                <button
                  key={a.type}
                  onClick={() => handleManualAction(a.type)}
                  disabled={busy}
                  className="text-xs px-2.5 py-1.5 rounded-full border border-border text-text-secondary hover:bg-off-white transition-colors"
                >
                  {a.label}
                </button>
              ))}
            </div>

            {actions.length === 0 && (
              <p className="text-sm text-text-secondary">No activity yet.</p>
            )}
            {actions.length > 0 && (
              <ul className="space-y-3">
                {actions.map(a => (
                  <li key={a.id} className="text-sm border-l-2 border-border pl-3">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="font-medium text-navy">{ACTION_LABELS[a.action_type] ?? a.action_type}</span>
                      <span className="text-xs text-text-secondary whitespace-nowrap">{fmtDateTime(a.created_at)}</span>
                    </div>
                    {a.body && <div className="text-text-secondary mt-1 whitespace-pre-wrap">{a.body}</div>}
                    {a.notes && <div className="text-text-secondary italic mt-1">{a.notes}</div>}
                    <div className="text-xs text-text-secondary mt-1">
                      {a.actor_type === 'ai_agent' ? 'AI agent' : a.actor_type === 'system' ? 'System' : staffName(staff, a.staff_id)}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>

        {/* Right: workflow controls */}
        <div className="space-y-6">
          <section className="card p-5">
            <h2 className="font-semibold text-navy mb-3">Status</h2>
            <select
              className="input"
              value={lead.status}
              onChange={e => saveStatus(e.target.value as LeadStatus, dismissalReason, dismissalNote)}
              disabled={busy}
            >
              {STATUS_OPTIONS.map(s => <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>)}
            </select>
            {lead.status === 'disqualified' && (
              <div className="mt-2 space-y-2">
                <div>
                  <label className="label">Reason</label>
                  <select
                    className="input mt-1"
                    value={dismissalReason}
                    disabled={busy}
                    onChange={e => {
                      const r = e.target.value as DismissalReason | ''
                      setDismissalReason(r)
                      saveStatus('disqualified', r, dismissalNote)
                    }}
                  >
                    <option value="">— select reason —</option>
                    {DISMISSAL_REASONS.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
                  </select>
                </div>
                {dismissalReason === 'other' && (
                  <div>
                    <label className="label">Detail</label>
                    <input
                      className="input mt-1"
                      placeholder="What disqualified this lead?"
                      value={dismissalNote}
                      disabled={busy}
                      onChange={e => setDismissalNote(e.target.value)}
                      onBlur={() => saveStatus('disqualified', dismissalReason, dismissalNote)}
                    />
                  </div>
                )}
              </div>
            )}
          </section>

          <section className="card p-5">
            <h2 className="font-semibold text-navy mb-3">Assigned to</h2>
            <select
              className="input"
              value={lead.assigned_staff_id ?? ''}
              onChange={e => handleAssign(e.target.value ? Number(e.target.value) : null)}
              disabled={busy}
            >
              <option value="">— unassigned —</option>
              {staff.map(s => (
                <option key={s.id} value={s.id}>{s.name.first_name} {s.name.last_name}</option>
              ))}
            </select>
          </section>

          <section className="card p-5">
            <h2 className="font-semibold text-navy mb-3">Priority</h2>
            <div className="flex rounded-lg border border-border overflow-hidden text-sm">
              {PRIORITY_OPTIONS.map(p => (
                <button
                  key={p}
                  onClick={() => handlePriority(p)}
                  disabled={busy}
                  className={`flex-1 px-3 py-2 capitalize font-medium transition-colors ${
                    lead.priority === p ? 'bg-navy text-white' : 'bg-white text-text-secondary hover:bg-off-white'
                  }`}
                >
                  {p}
                </button>
              ))}
            </div>
          </section>

          <section className="card p-5">
            <h2 className="font-semibold text-navy mb-3">Follow-up</h2>
            {lead.next_action_at && (
              <div className="text-sm mb-3">
                <div className="text-navy font-medium">{fmtDateTime(lead.next_action_at)}</div>
                {lead.next_action_note && <div className="text-text-secondary">{lead.next_action_note}</div>}
              </div>
            )}
            <form onSubmit={handleFollowUp} className="space-y-2">
              <input
                type="datetime-local"
                className="input"
                value={followUpAt}
                onChange={e => setFollowUpAt(e.target.value)}
              />
              <input
                className="input"
                placeholder="Note (optional)"
                value={followUpNote}
                onChange={e => setFollowUpNote(e.target.value)}
              />
              <button className="btn-primary w-full" disabled={busy}>
                {lead.next_action_at ? 'Update follow-up' : 'Set follow-up'}
              </button>
            </form>
          </section>

          <section className="card p-5">
            <h2 className="font-semibold text-navy mb-3">AI agent</h2>
            <p className="text-sm text-text-secondary mb-3">
              When enabled, the AI agent will respond to inbound emails from this lead with
              welcome, scheduling, and fee information. It escalates to you on legal questions.
            </p>
            <button
              onClick={handleToggleAgent}
              disabled={busy}
              className={lead.agent_enabled ? 'btn-secondary w-full' : 'btn-primary w-full'}
            >
              {lead.agent_enabled ? 'Disable agent' : 'Enable agent'}
            </button>
            {lead.agent_handoff_reason && (
              <p className="text-xs text-amber-700 mt-3">
                Escalated: {lead.agent_handoff_reason}
              </p>
            )}
          </section>
        </div>
      </div>
    </div>
  )
}
