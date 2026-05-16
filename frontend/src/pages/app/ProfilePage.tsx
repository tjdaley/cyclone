import { useEffect, useState, FormEvent } from 'react'
import { useAuth } from '../../context/AuthContext'
import { getStaff, updateStaff } from '../../lib/api'
import type { Staff } from '../../types'
import StaffForm, {
  StaffFormState, EMPTY_STAFF_FORM,
  staffToFormState, formStateToPayload, staffFormCanSubmit,
} from '../../components/StaffForm'

/**
 * Self-edit page. Any staff member can edit their own record here. Role and
 * slug are read-only — only an admin can change those, via /app/admin.
 */
export default function ProfilePage() {
  const { profile } = useAuth()
  const myStaffId = profile?.staff_id ?? null

  const [me, setMe]             = useState<Staff | null>(null)
  const [form, setForm]         = useState<StaffFormState>(EMPTY_STAFF_FORM)
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState<string | null>(null)
  const [saving, setSaving]     = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [savedAt, setSavedAt]   = useState<Date | null>(null)

  useEffect(() => {
    if (myStaffId === null) {
      setLoading(false)
      setError('No staff record linked to this account.')
      return
    }
    getStaff()
      .then(list => {
        const own = list.find(s => s.id === myStaffId)
        if (!own) { setError('Your staff record could not be found.'); return }
        setMe(own)
        setForm(staffToFormState(own))
      })
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load profile'))
      .finally(() => setLoading(false))
  }, [myStaffId])

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (myStaffId === null) return
    setSaving(true); setSaveError(null)
    try {
      const payload = formStateToPayload(form)
      const updated = await updateStaff(myStaffId, payload as unknown as Record<string, unknown>)
      setMe(updated)
      setForm(staffToFormState(updated))
      setSavedAt(new Date())
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Failed to save profile')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return <div className="px-6 py-12 text-center text-text-secondary text-sm">Loading…</div>
  }

  if (error || !me) {
    return (
      <div className="px-6 py-12 max-w-3xl mx-auto">
        <div className="card p-6 text-red-600">{error ?? 'Profile not available'}</div>
      </div>
    )
  }

  return (
    <div className="px-6 py-8 max-w-4xl mx-auto">
      <div className="mb-8">
        <h1 className="font-display text-3xl text-navy">My profile</h1>
        <p className="text-text-secondary mt-1">
          Update your contact info, billing rate, and agent integration settings.
          Role and slug can only be changed by an administrator.
        </p>
        {savedAt && (
          <p className="text-sm text-green-700 mt-2">
            Saved at {savedAt.toLocaleTimeString()}
          </p>
        )}
      </div>

      <div className="card p-5">
        <StaffForm
          value={form}
          onChange={setForm}
          onSubmit={handleSubmit}
          submitLabel="Save profile"
          submitting={saving}
          canSubmit={staffFormCanSubmit(form)}
          error={saveError}
          disabledFields={new Set(['role', 'slug'])}
        />
      </div>
    </div>
  )
}
