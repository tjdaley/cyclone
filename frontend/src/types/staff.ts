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
}
