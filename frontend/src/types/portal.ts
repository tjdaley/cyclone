/**
 * The client portal — mirrors app/schemas/portal.py.
 *
 * Deliberately not the staff types with fields removed. The same facts are
 * worded differently for a client (`complete` rather than `reconciled` and a
 * list of flags), and a separate shape keeps that difference explicit.
 *
 * Money is a STRING here as everywhere: exact from Postgres `numeric` to the
 * screen, never through Number().
 */
import type { AccountType } from './financial'

export interface PortalMatter {
  id: number
  matter_name: string
  matter_number: string | null
  /** False during hearing prep. The portal says so rather than hiding controls. */
  tagging_enabled: boolean
}

export interface PortalAccount {
  id: number
  institution: string
  account_type: AccountType
  account_number_last4: string | null
  name_on_account: string | null
  is_closed: boolean
  statements: number
}

export interface PortalStatement {
  id: number
  period_start: string
  period_end: string
  beginning_balance: string | null
  ending_balance: string | null
  transactions: number
  /** False means the firm is still checking it — never phrased as the client's problem. */
  complete: boolean
}

export interface PortalTag {
  id: number
  label: string
  description: string | null
  color: string | null
}

export interface PortalTransaction {
  id: number
  statement_id: number
  financial_account_id: number
  transaction_date: string | null
  description: string
  counterparty: string | null
  amount: string
  check_number: string | null
  physical_page_number: number | null
  bates_number: string | null
  institution: string | null
  account_last4: string | null
  tag_ids: number[]
  /** Reviewed and removed by the firm. Shown greyed; cannot be re-applied. */
  cleared_tag_ids: number[]
}

export interface PortalTransactionPage {
  total: number
  items: PortalTransaction[]
}

export interface PortalTagResult {
  outcome: 'applied' | 'already' | 'removed' | 'absent'
  tag_ids: number[]
  cleared_tag_ids: number[]
}

/** A client's tag, as the staff review screen sees it. */
export interface ClientTagReview {
  link_id: number
  transaction_id: number
  tag_id: number
  tag_label: string
  matter_id: number
  transaction_date: string | null
  description: string
  amount: string
  institution: string | null
  account_last4: string | null
  tagged_at: string
  cleared_at: string | null
}

/** Filters the portal's transaction list offers. Fewer than staff get, by design. */
export interface PortalTransactionFilter {
  account_id?: number | null
  text?: string | null
  amount_min?: string | null
  amount_max?: string | null
  tag_id?: number | null
  untagged?: boolean
  limit?: number
  offset?: number
}
