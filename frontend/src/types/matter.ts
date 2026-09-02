/** Mirrors MatterStatus enum in db/models/matter.py */
export type MatterStatus =
  | 'intake'
  | 'conflict_review'
  | 'active'
  | 'closed'
  | 'archived'

/** Mirrors MatterType enum in db/models/matter.py */
export type MatterType =
  | 'divorce'
  | 'child_custody'
  | 'modification'
  | 'enforcement'
  | 'cps'
  | 'probate'
  | 'estate_planning'
  | 'civil'
  | 'other'

/**
 * Mirrors RateCard in db/models/matter.py.
 *
 * Per-role rates for a matter. Used as the second tier of rate resolution
 * after matter_rate_overrides and before staff default_billing_rate.
 * Admins do not bill time and are intentionally absent.
 */
export interface RateCard {
  attorney: number | null
  paralegal: number | null
}

/** Mirrors MatterResponse in app/schemas/matter.py */
export interface Matter {
  id: number
  client_id: number
  short_name: string | null
  matter_name: string
  matter_type: MatterType
  status: MatterStatus
  billing_review_staff_id: number | null
  rate_card: RateCard
  retainer_amount: number
  refresh_trigger_pct: number
  is_pro_bono: boolean
  fee_agreement_signed_date: string | null
  opened_date: string | null
  closed_date: string | null
  state: string
  county: string
  court_name: string | null
  matter_number: string | null
  discovery_level: 'level_1' | 'level_2' | 'level_3' | null
  notes: string | null
  /** Formal style of the case, as written on a filing. Heads every exhibit. */
  case_style: string | null
  /** Which side our client is on, in caption vocabulary. Titles every exhibit. */
  client_alignment: ClientAlignment | null
}

/**
 * Caption vocabulary for our client's side of the case.
 *
 * Stored underscored, written hyphenated — the backend owns that mapping, so a
 * label here is display only. See ClientAlignment in app/db/models/matter.py.
 */
export type ClientAlignment =
  | 'petitioner' | 'respondent' | 'counter_petitioner' | 'counter_respondent'
  | 'intervenor' | 'plaintiff' | 'defendant' | 'applicant' | 'movant' | 'other'

export const CLIENT_ALIGNMENTS: { value: ClientAlignment; label: string }[] = [
  { value: 'petitioner', label: 'Petitioner' },
  { value: 'respondent', label: 'Respondent' },
  { value: 'counter_petitioner', label: 'Counter-Petitioner' },
  { value: 'counter_respondent', label: 'Counter-Respondent' },
  { value: 'intervenor', label: 'Intervenor' },
  { value: 'plaintiff', label: 'Plaintiff' },
  { value: 'defendant', label: 'Defendant' },
  { value: 'applicant', label: 'Applicant' },
  { value: 'movant', label: 'Movant' },
  { value: 'other', label: 'Other' },
]

/** Mirrors MatterCreateRequest in app/schemas/matter.py */
export interface MatterCreatePayload {
  client_id: number
  short_name?: string
  matter_name: string
  matter_type: string
  billing_review_staff_id?: number
  rate_card?: Partial<RateCard>
  retainer_amount?: number
  refresh_trigger_pct?: number
  is_pro_bono?: boolean
  fee_agreement_signed_date?: string | null
  opened_date?: string | null
  closed_date?: string | null
  state?: string
  county: string
  court_name?: string | null
  matter_number?: string | null
  notes?: string
}

/** Mirrors MatterRateOverrideResponse in app/schemas/matter.py */
export interface RateOverride {
  id: number
  matter_id: number
  staff_id: number
  rate: number
}

/** Roles a staff member can hold on a matter — matches the matter_staff CHECK constraint. */
export type MatterStaffRole = 'originating' | 'billing_reviewer' | 'assigned'

/** Mirrors MatterStaffResponse in app/schemas/matter.py */
export interface MatterStaff {
  id: number
  matter_id: number
  staff_id: number
  role: MatterStaffRole
  split_pct: number | null
}

/** Mirrors MatterStaffRequest in app/schemas/matter.py */
export interface MatterStaffPayload {
  staff_id: number
  role: MatterStaffRole
  split_pct?: number | null
}

/** Mirrors MatterStaffUpdateRequest — staff_id is not updatable. */
export interface MatterStaffUpdatePayload {
  role?: MatterStaffRole
  split_pct?: number | null
}

/** Mirrors OpposingPartyResponse in app/schemas/matter.py */
export interface OpposingParty {
  id: number
  matter_id: number
  full_name: string
  relationship: string | null
}
