import { FullName } from './common'

/** Mirrors StaffRole enum in db/models/staff.py */
export type StaffRole = 'attorney' | 'paralegal' | 'admin'

export interface BarAdmission {
  bar_number: string
  state: string
}

/** Mirrors StaffResponse in app/schemas/staff.py */
export interface Staff {
  id: number
  supabase_uid: string | null
  auth_email: string | null
  role: StaffRole
  name: FullName
  office_id: number
  email: string
  telephone: string
  slug: string
  bar_admissions: BarAdmission[]
  default_billing_rate: number | null
  calendly_url: string | null
  agent_signature: string | null
  telegram_id: string | null
  /** Auth roles from user_roles (sorted). May be empty if the staff has no auth rows yet. */
  roles: string[]
}

/** Mirrors ResponderSetResponse in app/routers/attorney_lead_responder.py */
export interface AttorneyResponderSet {
  attorney_staff_id: number
  responder_staff_ids: number[]
}

/** Mirrors StaffRolesResponse in app/schemas/staff.py */
export interface StaffRoleSet {
  staff_id: number
  roles: string[]
}

/** Allowed values when editing a staff member's auth roles. */
export const ASSIGNABLE_STAFF_ROLES: StaffRole[] = ['attorney', 'paralegal', 'admin']

/** Mirrors StaffCreateRequest in app/schemas/staff.py */
export interface StaffCreatePayload {
  auth_email?: string | null
  role: StaffRole
  name: FullName
  office_id: number
  email: string
  telephone: string
  slug: string
  bar_admissions?: BarAdmission[]
  default_billing_rate?: number | null
  calendly_url?: string | null
  agent_signature?: string | null
  telegram_id?: string | null
}
