import { FormEvent } from 'react'
import type { BarAdmission, StaffRole } from '../types'

const ROLES: StaffRole[] = ['attorney', 'paralegal', 'admin']

export interface StaffFormState {
  courtesy_title: string
  first_name: string
  middle_name: string
  last_name: string
  suffix: string
  auth_email: string
  email: string
  telephone: string
  role: StaffRole
  slug: string
  office_id: string
  default_billing_rate: string
  calendly_url: string
  agent_signature: string
  telegram_id: string
  bar_admissions: BarAdmission[]
}

export const EMPTY_STAFF_FORM: StaffFormState = {
  courtesy_title: '',
  first_name: '',
  middle_name: '',
  last_name: '',
  suffix: '',
  auth_email: '',
  email: '',
  telephone: '',
  role: 'attorney',
  slug: '',
  office_id: '1',
  default_billing_rate: '',
  calendly_url: '',
  agent_signature: '',
  telegram_id: '',
  bar_admissions: [{ state: '', bar_number: '' }],
}

interface Props {
  value: StaffFormState
  onChange: (next: StaffFormState) => void
  onSubmit: (e: FormEvent) => void
  onCancel?: () => void
  submitLabel: string
  submitting: boolean
  canSubmit: boolean
  error?: string | null
  /** Fields the user is not allowed to change. Renders read-only. */
  disabledFields?: ReadonlySet<keyof StaffFormState>
}

export default function StaffForm({
  value, onChange, onSubmit, onCancel,
  submitLabel, submitting, canSubmit, error,
  disabledFields = new Set(),
}: Props) {
  const set = <K extends keyof StaffFormState>(k: K, v: StaffFormState[K]) =>
    onChange({ ...value, [k]: v })

  const isDisabled = (k: keyof StaffFormState) => disabledFields.has(k)

  function setBarRow(i: number, field: keyof BarAdmission, v: string) {
    onChange({
      ...value,
      bar_admissions: value.bar_admissions.map((b, idx) =>
        idx === i ? { ...b, [field]: v } : b),
    })
  }

  function addBarRow() {
    onChange({ ...value, bar_admissions: [...value.bar_admissions, { state: '', bar_number: '' }] })
  }

  function removeBarRow(i: number) {
    onChange({ ...value, bar_admissions: value.bar_admissions.filter((_, idx) => idx !== i) })
  }

  return (
    <form onSubmit={onSubmit} className="space-y-5">

      <fieldset className="space-y-2">
        <legend className="text-xs uppercase tracking-widest font-semibold text-text-secondary mb-1">Name</legend>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <div>
            <label className="label">Title</label>
            <input className="input mt-1" placeholder="Mr." value={value.courtesy_title}
              onChange={e => set('courtesy_title', e.target.value)} />
          </div>
          <div className="col-span-2 md:col-span-1">
            <label className="label">First *</label>
            <input className="input mt-1" value={value.first_name}
              onChange={e => set('first_name', e.target.value)} />
          </div>
          <div>
            <label className="label">Middle</label>
            <input className="input mt-1" placeholder="J." value={value.middle_name}
              onChange={e => set('middle_name', e.target.value)} />
          </div>
          <div className="col-span-2 md:col-span-1">
            <label className="label">Last *</label>
            <input className="input mt-1" value={value.last_name}
              onChange={e => set('last_name', e.target.value)} />
          </div>
          <div>
            <label className="label">Suffix</label>
            <input className="input mt-1" placeholder="Jr." value={value.suffix}
              onChange={e => set('suffix', e.target.value)} />
          </div>
        </div>
      </fieldset>

      <fieldset className="space-y-2">
        <legend className="text-xs uppercase tracking-widest font-semibold text-text-secondary mb-1">Login & contact</legend>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <label className="label">Sign-in email</label>
            <input className="input mt-1" type="email" placeholder="user@gmail.com" value={value.auth_email}
              onChange={e => set('auth_email', e.target.value)} />
            <p className="text-xs text-text-secondary mt-1">Address used to log in (Google).</p>
          </div>
          <div>
            <label className="label">Work email *</label>
            <input className="input mt-1" type="email" value={value.email}
              onChange={e => set('email', e.target.value)} />
          </div>
          <div>
            <label className="label">Telephone *</label>
            <input className="input mt-1" value={value.telephone}
              onChange={e => set('telephone', e.target.value)} />
          </div>
        </div>
      </fieldset>

      <fieldset className="space-y-2">
        <legend className="text-xs uppercase tracking-widest font-semibold text-text-secondary mb-1">Role & access</legend>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <div>
            <label className="label">Role *</label>
            <select className="input mt-1" value={value.role}
              disabled={isDisabled('role')}
              onChange={e => set('role', e.target.value as StaffRole)}>
              {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
            </select>
            {isDisabled('role') && <p className="text-xs text-text-secondary mt-1">Only an admin can change this.</p>}
          </div>
          <div>
            <label className="label">Slug *</label>
            <input className="input mt-1" placeholder="e.g. jdoe" value={value.slug}
              disabled={isDisabled('slug')}
              onChange={e => set('slug', e.target.value)} />
            {isDisabled('slug') && <p className="text-xs text-text-secondary mt-1">Only an admin can change this.</p>}
          </div>
          <div>
            <label className="label">Office ID *</label>
            <input className="input mt-1" type="number" min="1" value={value.office_id}
              onChange={e => set('office_id', e.target.value)} />
          </div>
          <div>
            <label className="label">Default rate ($/hr)</label>
            <input className="input mt-1" type="number" step="0.01" min="0"
              placeholder="250" value={value.default_billing_rate}
              onChange={e => set('default_billing_rate', e.target.value)} />
          </div>
        </div>
      </fieldset>

      <fieldset className="space-y-2">
        <legend className="text-xs uppercase tracking-widest font-semibold text-text-secondary mb-1">Bar admissions</legend>
        {value.bar_admissions.map((b, i) => (
          <div key={i} className="flex gap-2 items-end">
            <div className="flex-1">
              <label className="label">State</label>
              <input className="input mt-1" placeholder="Texas" value={b.state}
                onChange={e => setBarRow(i, 'state', e.target.value)} />
            </div>
            <div className="flex-1">
              <label className="label">Bar number</label>
              <input className="input mt-1" value={b.bar_number}
                onChange={e => setBarRow(i, 'bar_number', e.target.value)} />
            </div>
            {value.bar_admissions.length > 1 && (
              <button type="button" onClick={() => removeBarRow(i)}
                className="text-sm text-red-600 hover:underline px-2 py-2">Remove</button>
            )}
          </div>
        ))}
        <button type="button" onClick={addBarRow}
          className="text-sm text-navy hover:underline">+ Add another admission</button>
        <p className="text-xs text-text-secondary">Leave both fields blank to skip.</p>
      </fieldset>

      <fieldset className="space-y-2">
        <legend className="text-xs uppercase tracking-widest font-semibold text-text-secondary mb-1">CRM &amp; agent integration</legend>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <label className="label">Calendly URL</label>
            <input className="input mt-1" placeholder="https://calendly.com/..." value={value.calendly_url}
              onChange={e => set('calendly_url', e.target.value)} />
            <p className="text-xs text-text-secondary mt-1">Shared by the AI agent when leads ask to schedule.</p>
          </div>
          <div>
            <label className="label">Agent signature</label>
            <input className="input mt-1" placeholder="Best, Tom" value={value.agent_signature}
              onChange={e => set('agent_signature', e.target.value)} />
            <p className="text-xs text-text-secondary mt-1">Sign-off on outbound agent messages.</p>
          </div>
          <div>
            <label className="label">Telegram chat ID</label>
            <input className="input mt-1" placeholder="123456789" value={value.telegram_id}
              onChange={e => set('telegram_id', e.target.value)} />
            <p className="text-xs text-text-secondary mt-1">Agent uses this for quick escalations.</p>
          </div>
        </div>
      </fieldset>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <div className="flex gap-3 pt-2 border-t border-border">
        <button type="submit" className="btn-primary" disabled={submitting || !canSubmit}>
          {submitting ? 'Saving…' : submitLabel}
        </button>
        {onCancel && (
          <button type="button" className="btn-secondary" onClick={onCancel}>Cancel</button>
        )}
      </div>
    </form>
  )
}

/** Build a StaffFormState from an existing Staff record for edit forms. */
export function staffToFormState(s: import('../types').Staff): StaffFormState {
  return {
    courtesy_title: s.name.courtesy_title ?? '',
    first_name:     s.name.first_name,
    middle_name:    s.name.middle_name ?? '',
    last_name:      s.name.last_name,
    suffix:         s.name.suffix ?? '',
    auth_email:     s.auth_email ?? '',
    email:          s.email,
    telephone:      s.telephone,
    role:           s.role,
    slug:           s.slug,
    office_id:      String(s.office_id),
    default_billing_rate: s.default_billing_rate != null ? String(s.default_billing_rate) : '',
    calendly_url:   s.calendly_url ?? '',
    agent_signature: s.agent_signature ?? '',
    telegram_id:    s.telegram_id ?? '',
    bar_admissions: s.bar_admissions.length > 0 ? s.bar_admissions : [{ state: '', bar_number: '' }],
  }
}

/** Build the API payload from a StaffFormState. */
export function formStateToPayload(f: StaffFormState) {
  const cleanedBars = f.bar_admissions
    .map(b => ({ state: b.state.trim(), bar_number: b.bar_number.trim() }))
    .filter(b => b.state && b.bar_number)
  return {
    role: f.role,
    name: {
      courtesy_title: f.courtesy_title.trim() || null,
      first_name: f.first_name.trim(),
      middle_name: f.middle_name.trim() || null,
      last_name: f.last_name.trim(),
      suffix: f.suffix.trim() || null,
    },
    office_id: parseInt(f.office_id, 10) || 1,
    email: f.email.trim(),
    telephone: f.telephone.trim(),
    slug: f.slug.trim(),
    auth_email: f.auth_email.trim() || null,
    bar_admissions: cleanedBars,
    default_billing_rate: f.default_billing_rate ? parseFloat(f.default_billing_rate) : null,
    calendly_url: f.calendly_url.trim() || null,
    agent_signature: f.agent_signature.trim() || null,
    telegram_id: f.telegram_id.trim() || null,
  }
}

export function staffFormCanSubmit(f: StaffFormState): boolean {
  return Boolean(
    f.first_name.trim()
    && f.last_name.trim()
    && f.email.trim()
    && f.telephone.trim()
    && f.slug.trim()
    && f.office_id,
  )
}
