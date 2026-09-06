/**
 * Types for the Financial Information Statement.
 *
 * Mirrors app/schemas/fis.py. Money arrives as a string and stays one — see
 * lib/money.ts — because these figures are sworn to and parsing them undoes the
 * exactness at the last step before they are printed.
 */

/** How often money moves in a category, for one person. */
export type FisRecurrence =
  | 'weekly' | 'biweekly' | 'semimonthly' | 'monthly'
  | 'quarterly' | 'semiannual' | 'annual' | 'irregular'

export const FIS_RECURRENCES: { value: FisRecurrence; label: string }[] = [
  { value: 'monthly', label: 'Monthly' },
  { value: 'semimonthly', label: 'Twice monthly' },
  { value: 'biweekly', label: 'Every two weeks' },
  { value: 'weekly', label: 'Weekly' },
  { value: 'quarterly', label: 'Quarterly' },
  { value: 'semiannual', label: 'Twice yearly' },
  { value: 'annual', label: 'Annually' },
  { value: 'irregular', label: 'As incurred' },
]

/**
 * Recurrences whose payment covers more than the month it falls in.
 *
 * These are computed from the trailing year rather than the window, which is
 * what stops an annual bill reading as $1,800/month over a two-month window.
 */
export const SUB_MONTHLY: FisRecurrence[] = ['quarterly', 'semiannual', 'annual']

export interface FisRequest {
  account_ids?: number[] | null
  start_year: number
  start_month: number
  end_year: number
  end_month: number
  client_id?: number | null
  opposing_party_id?: number | null
}

export interface FisLine {
  category_id: number
  parent_id: number | null
  label: string
  /** 0 for a top-level heading. The form is its indentation. */
  depth: number
  monthly: string
  window_total: string
  trailing_year_total: string
  transaction_count: number
  /** window | trailing_year | stated — how the figure was reached. */
  basis: string
  recurrence: FisRecurrence | null
  /** "paid annually", "as incurred". Absent on monthly lines. */
  legend: string | null
  note: string | null
  /** True when a compressed statement drops this row. */
  empty: boolean
}

export interface FisWindow {
  start: string
  end: string
  /** The denominator, and therefore a claim about coverage. */
  months: number
  trailing_start: string
}

export interface FisCoverageAccount {
  account_id: number
  label: string
  months_in_window: number
  months_held: number
  /** YYYY-MM with no statement covering them. */
  missing_months: string[]
}

export interface FisCoverage {
  complete: boolean
  accounts: FisCoverageAccount[]
}

export interface FisExcludedCategory {
  category_id: number
  label: string
  total: string
  transaction_count: number
}

export interface FisUncategorized {
  count: number
  total: string
  monthly: string
}

export interface FisStatement {
  window: FisWindow
  accounts: string[]
  lines: FisLine[]
  net_monthly: string
  uncategorized: FisUncategorized
  excluded: FisExcludedCategory[]
  coverage: FisCoverage
  warnings: string[]
}

export interface FisSetting {
  id: number
  client_id: number | null
  opposing_party_id: number | null
  category_id: number
  recurrence: FisRecurrence
  stated_annual_amount: string | null
  note: string | null
  /** True when this is the firm-wide default rather than this person's own. */
  is_default: boolean
}

export interface FisSettingPayload {
  category_id: number
  recurrence: FisRecurrence
  stated_annual_amount?: string | null
  note?: string | null
  client_id?: number | null
  opposing_party_id?: number | null
}

export interface FisExportRequest extends FisRequest {
  format: 'csv' | 'md' | 'docx' | 'pdf'
  exhibit_name: string
  /** Drop lines with no amount. The full form is what a court expects. */
  compressed: boolean
}

/** One transaction on the schedule, with everything needed to find it. */
export interface FisScheduleTransaction {
  id: number
  date: string | null
  description: string
  amount: string
  check_number: string | null
  account: string
  bates_number: string | null
  page: number | null
  /** The uploaded filename. Storage renames every upload to a job id. */
  document: string | null
  statement_id: number
  /** human | rule | similarity. Null means never categorized. */
  category_source: string | null
  category_rule_id: number | null
  /** Whether a person has confirmed an automatic assignment. */
  reviewed: boolean
}

export interface FisScheduleGroup {
  category_id: number | null
  label: string
  depth: number
  basis: string
  recurrence: FisRecurrence | null
  legend: string | null
  /** Taken from the statement, never recomputed — so the two cannot disagree. */
  monthly: string
  total: string
  /** window | trailing_year — which months these transactions cover. */
  span: string
  span_start: string
  span_end: string
  /** How these transactions become the figure on the statement, in prose. */
  derivation: string
  transactions: FisScheduleTransaction[]
}

export interface FisSchedule {
  window: FisWindow
  accounts: string[]
  groups: FisScheduleGroup[]
  warnings: string[]
}
