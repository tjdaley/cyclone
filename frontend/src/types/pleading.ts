import type { FullName } from './common'

export type ChildSex = 'male' | 'female' | 'other'
export type CounselRole = 'lead' | 'co_counsel' | 'local_counsel'
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

// ── Matter Pleadings ─────────────────────────────────────────────────────────

export interface MatterPleading {
  id: number
  matter_id: number
  opposing_party_id: number | null
  title: string
  filed_date: string | null
  served_date: string | null
  amends_pleading_id: number | null
  is_supplement: boolean
  storage_path: string | null
  ingested_by_staff_id: number
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

// ── Ingestion Preview ────────────────────────────────────────────────────────

export interface FieldDiff {
  current: unknown | null
  proposed: unknown | null
}

export interface ChildPreview {
  name: FullName
  date_of_birth: string | null
  sex: ChildSex | null
  needs_support_after_majority: boolean
}

export interface OCPreview {
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

export interface PleadingIngestPreview {
  matter_id: number
  raw_text: string
  pleading: PleadingPreview
  matter_field_updates: Record<string, FieldDiff>
  new_children: ChildPreview[]
  opposing_counsel_matches: OCMatchPreview[]
  new_opposing_counsel: OCPreview[]
  claims: ClaimPreview[]
  warnings: string[]
}

// ── Commit payload ───────────────────────────────────────────────────────────

export interface ChildCommitEntry {
  name: FullName
  date_of_birth: string
  sex: ChildSex
  needs_support_after_majority: boolean
}

export interface OCCommitEntry {
  existing_id: number | null
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

export interface PleadingCommitRequest {
  matter_id: number
  raw_text: string
  title: string
  filed_date: string | null
  served_date: string | null
  opposing_party_id: number | null
  is_supplement: boolean
  amends_pleading_id: number | null
  matter_field_updates: Record<string, unknown>
  children: ChildCommitEntry[]
  opposing_counsel: OCCommitEntry[]
  claims: ClaimCommitEntry[]
}

export interface PleadingCommitResponse {
  pleading: MatterPleading
  children_created: number
  opposing_counsel_linked: number
  claims_created: number
}
