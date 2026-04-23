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
}

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
