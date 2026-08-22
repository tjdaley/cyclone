import type { FullName } from './common'

export type ChildSex = 'male' | 'female' | 'other'
/** Mirrors CounselRole in app/db/models/pleading.py. 'prior_counsel' has substituted out. */
export type CounselRole = 'lead' | 'co_counsel' | 'local_counsel' | 'prior_counsel'
export type ClaimKind = 'claim' | 'defense' | 'affirmative_defense' | 'counterclaim'
export type DiscoveryLevel = 'level_1' | 'level_2' | 'level_3'

// ── Matter Children ──────────────────────────────────────────────────────────

export interface MatterChild {
  id: number
  matter_id: number
  name: FullName
  date_of_birth: string
  sex: ChildSex
  needs_support_after_majority: boolean
}

/** Mirrors MatterChildRequest in app/schemas/pleading.py */
export interface MatterChildPayload {
  name: FullName
  date_of_birth: string
  sex: ChildSex
  needs_support_after_majority?: boolean
}

/** Mirrors MatterChildUpdateRequest — every field optional. */
export type MatterChildUpdatePayload = Partial<MatterChildPayload>

// ── Opposing Counsel ─────────────────────────────────────────────────────────

export interface OpposingCounsel {
  id: number
  name: FullName
  firm_name: string | null
  street_address: string | null
  street_address_2: string | null
  city: string | null
  state: string | null
  postal_code: string | null
  email: string | null
  cell_phone: string | null
  telephone: string | null
  fax: string | null
  bar_state: string
  bar_number: string
  email_ccs: string[]
}

/** Mirrors OpposingCounselRequest in app/schemas/pleading.py */
export interface OpposingCounselPayload {
  name: FullName
  firm_name?: string | null
  street_address?: string | null
  street_address_2?: string | null
  city?: string | null
  state?: string | null
  postal_code?: string | null
  email?: string | null
  cell_phone?: string | null
  telephone?: string | null
  fax?: string | null
  bar_state: string
  bar_number: string
  email_ccs?: string[]
}

/** Mirrors MatterOpposingCounselResponse — the matter↔counsel association row. */
export interface MatterCounselLink {
  id: number
  matter_id: number
  opposing_counsel_id: number
  opposing_party_id: number | null
  role: CounselRole
  started_date: string | null
  ended_date: string | null
}

/** Mirrors MatterOpposingCounselLinkRequest in app/schemas/pleading.py */
export interface MatterCounselLinkPayload {
  opposing_counsel_id: number
  opposing_party_id?: number | null
  role?: CounselRole
  started_date?: string | null
  ended_date?: string | null
}

/** Mirrors MatterOpposingCounselUpdateRequest — association fields only. */
export interface MatterCounselLinkUpdatePayload {
  opposing_party_id?: number | null
  role?: CounselRole
  started_date?: string | null
  ended_date?: string | null
}

// ── Matter Pleadings ─────────────────────────────────────────────────────────

/** Mirrors PleadingStatus in app/db/models/pleading.py. A supplement is live. */
export type PleadingStatus = 'live' | 'superseded' | 'withdrawn' | 'inactive'

export interface MatterPleading {
  id: number
  matter_id: number
  opposing_party_id: number | null
  title: string
  filed_date: string | null
  served_date: string | null
  amends_pleading_id: number | null
  is_supplement: boolean
  status: PleadingStatus
  storage_path: string | null
  ingested_by_staff_id: number
}

/** Mirrors MatterPleadingUpdateRequest in app/schemas/pleading.py */
export interface MatterPleadingUpdatePayload {
  title?: string
  filed_date?: string | null
  served_date?: string | null
  amends_pleading_id?: number | null
  is_supplement?: boolean
  status?: PleadingStatus
  opposing_party_id?: number | null
}

// ── Matter Claims ────────────────────────────────────────────────────────────

export interface MatterClaim {
  id: number
  matter_pleading_id: number
  matter_id: number
  opposing_party_id: number | null
  kind: ClaimKind
  label: string
  narrative: string
  statute_rule_cited: string | null
}

/** Mirrors MatterClaimCreateRequest in app/schemas/pleading.py */
export interface MatterClaimPayload {
  matter_pleading_id: number
  kind: ClaimKind
  label: string
  narrative: string
  statute_rule_cited?: string | null
  opposing_party_id?: number | null
}

/** Mirrors MatterClaimUpdateRequest — every field optional. */
export interface MatterClaimUpdatePayload {
  kind?: ClaimKind
  label?: string
  narrative?: string
  statute_rule_cited?: string | null
  opposing_party_id?: number | null
}

// ── Ingestion Preview ────────────────────────────────────────────────────────

export interface FieldDiff {
  current: unknown | null
  proposed: unknown | null
}

export interface ChildPreview {
  /** Set when this child is already on the matter; the commit updates that row. */
  existing_id: number | null
  name: FullName
  date_of_birth: string | null
  sex: ChildSex | null
  needs_support_after_majority: boolean
}

export interface OCPreview {
  /** Party this attorney represents, per the pleading. Review-only — not persisted. */
  represents: string | null
  name: FullName
  firm_name: string | null
  street_address: string | null
  street_address_2: string | null
  city: string | null
  state: string | null
  postal_code: string | null
  email: string | null
  cell_phone: string | null
  telephone: string | null
  fax: string | null
  bar_state: string | null
  bar_number: string | null
  email_ccs: string[]
}

export interface OCMatchPreview {
  existing_id: number
  existing: OpposingCounsel
  proposed: OCPreview
  diffs: Record<string, FieldDiff>
}

export interface ClaimPreview {
  kind: ClaimKind
  label: string
  narrative: string
  statute_rule_cited: string | null
  party_side: 'our_client' | 'opposing'
}

export interface PleadingPreview {
  title: string
  filed_date: string | null
  served_date: string | null
  is_supplement: boolean
  amends_pleading_title: string | null
}

/** An adverse party extracted from a pleading, pending attorney review. */
export interface OpposingPartyPreview {
  /** Set when the party is already on the matter; the commit reuses that row. */
  existing_id: number | null
  full_name: string
  relationship: string | null
}

export interface PleadingIngestPreview {
  matter_id: number
  raw_text: string
  pleading: PleadingPreview
  matter_field_updates: Record<string, FieldDiff>
  opposing_parties: OpposingPartyPreview[]
  new_children: ChildPreview[]
  opposing_counsel_matches: OCMatchPreview[]
  new_opposing_counsel: OCPreview[]
  claims: ClaimPreview[]
  warnings: string[]
}

// ── Commit payload ───────────────────────────────────────────────────────────

export interface ChildCommitEntry {
  /** Existing matter_children.id, or null to create a new row. */
  existing_id: number | null
  name: FullName
  date_of_birth: string
  sex: ChildSex
  needs_support_after_majority: boolean
}

export interface OCCommitEntry {
  existing_id: number | null
  /** Carried through the review form for display only; the backend ignores it. */
  represents?: string | null
  name: FullName
  firm_name: string | null
  street_address: string | null
  street_address_2: string | null
  city: string | null
  state: string | null
  postal_code: string | null
  email: string | null
  cell_phone: string | null
  telephone: string | null
  fax: string | null
  bar_state: string
  bar_number: string
  email_ccs: string[]
  opposing_party_id: number | null
  role: CounselRole
}

export interface ClaimCommitEntry {
  kind: ClaimKind
  label: string
  narrative: string
  statute_rule_cited: string | null
  opposing_party_id: number | null
}

/** An adverse party to create, or reuse when existing_id is set. */
export interface OpposingPartyCommitEntry {
  existing_id: number | null
  full_name: string
  relationship: string | null
}

export interface PleadingCommitRequest {
  matter_id: number
  raw_text: string
  title: string
  filed_date: string | null
  served_date: string | null
  opposing_party_id: number | null
  /** Filing party by name, for a party this same commit is creating (no id yet). */
  opposing_party_name?: string | null
  is_supplement: boolean
  amends_pleading_id: number | null
  matter_field_updates: Record<string, unknown>
  opposing_parties: OpposingPartyCommitEntry[]
  children: ChildCommitEntry[]
  opposing_counsel: OCCommitEntry[]
  claims: ClaimCommitEntry[]
}

export interface PleadingCommitResponse {
  pleading: MatterPleading
  opposing_parties_created: number
  children_created: number
  opposing_counsel_linked: number
  claims_created: number
}
