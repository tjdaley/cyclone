import { FullName } from './common'

/** Mirrors ClientStatus enum in db/models/client.py */
export type ClientStatus =
  | 'prospect'
  | 'pending_conflict_check'
  | 'conflict_flagged'
  | 'active'
  | 'inactive'

/** Mirrors ClientResponse in app/schemas/client.py */
export interface Client {
  id: number
  name: FullName
  auth_email: string
  email: string
  telephone: string
  referral_type: string
  referral_source: string
  referred_to_staff_id: number | null
  prior_counsel: string | null
  status: ClientStatus
  ok_to_rehire: boolean
  ending_ar_balance: number
  notes: string | null
}

/** Mirrors ClientCreateRequest in app/schemas/client.py */
export interface ClientCreatePayload {
  name: {
    first_name: string
    last_name: string
    courtesy_title?: string
    middle_name?: string
    suffix?: string
  }
  auth_email: string
  email: string
  telephone: string
  referral_type: string
  referral_source: string
  referred_to_staff_id?: number | null
  prior_counsel?: string
  notes?: string
}

/** Single hit row from POST /api/v1/clients/conflict-check */
export interface ConflictHit {
  id: number
  full_name: string
  role: string
  matter_caption: string
}
