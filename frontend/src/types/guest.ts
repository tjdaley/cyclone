/**
 * Financial-professional guest access (052). Mirrors app/schemas/guest.py.
 *
 * The guest-facing shapes are their own, not staff types with fields hidden:
 * no account notes or purpose, no statement flags, no tags but the guest's own.
 */
import type { AccountOwnership, AccountType, PropertyCharacter } from './financial'

export type GuestDesignation = 'testifying_expert' | 'consulting_expert' | 'non_expert'
export type ConsentMethod = 'phone' | 'in_person' | 'email' | 'letter' | 'other'
/** As of now: `expired` is an active grant past its date, decided by the server. */
export type GuestGrantStatus = 'pending_consent' | 'active' | 'expired' | 'revoked'

// ── Staff side ──────────────────────────────────────────────────────────────

export interface GuestConsentRecord {
  method: ConsentMethod
  consent_date: string
  note: string | null
  attested_by: string
  created_at: string
}

export interface GuestGrant {
  id: number
  guest_id: number
  full_name: string
  email: string
  organization: string | null
  profession: string | null
  designation: GuestDesignation
  purpose: string | null
  status: GuestGrantStatus
  expires_at: string
  requested_by: string | null
  activated_at: string | null
  revoked_at: string | null
  revoked_by: string | null
  revoke_reason: string | null
  consents: GuestConsentRecord[]
  documents_provided: number
  last_signed_in_at: string | null
  created_at: string
}

export interface GuestInvitePayload {
  full_name: string
  email: string
  organization?: string | null
  profession?: string | null
  designation: GuestDesignation
  purpose?: string | null
  expires_in_days: number
}

export interface GuestConsentPayload {
  method: ConsentMethod
  consent_date: string
  note?: string | null
}

export interface GuestLedgerEntry {
  statement_id: number
  account_label: string | null
  period_start: string | null
  period_end: string | null
  source_filename: string | null
  bates_first: string | null
  bates_last: string | null
  first_available_at: string
  removed_at: string | null
}

// ── Guest side ──────────────────────────────────────────────────────────────

export interface GuestMatter {
  id: number
  name: string
  designation: GuestDesignation
  expires_at: string
  purpose_reminder: string
}

export interface GuestAccount {
  id: number
  institution: string
  account_type: AccountType
  account_number_last4: string | null
  name_on_account: string | null
  ownership: AccountOwnership
  property_character: PropertyCharacter | null
  is_closed: boolean
  currency: string
}

export interface GuestStatement {
  id: number
  financial_account_id: number
  period_start: string
  period_end: string
  beginning_balance: string | null
  ending_balance: string | null
  reconciled: boolean
  bates_first: string | null
  bates_last: string | null
  source_filename: string | null
  has_pdf: boolean
}

export interface GuestTransaction {
  id: number
  statement_id: number
  financial_account_id: number
  transaction_date: string | null
  posted_date: string | null
  description: string
  counterparty: string | null
  location: string | null
  amount: string
  running_balance: string | null
  check_number: string | null
  category_id: number | null
  physical_page_number: number | null
  bates_number: string | null
  institution: string | null
  account_last4: string | null
  currency: string
  /** This guest's own tags only. */
  tag_ids: number[]
}

export interface GuestTransactionPage {
  total: number
  items: GuestTransaction[]
  sum_amount: string
}

export interface GuestTag {
  id: number
  label: string
  description: string | null
  color: string | null
  is_active: boolean
}

export interface GuestCategory {
  id: number
  description: string
  parent_id: number | null
}
