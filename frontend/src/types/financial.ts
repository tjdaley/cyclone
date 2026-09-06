/**
 * Account statements — the spine of inventories and waste exhibits.
 *
 * Mirrors app/schemas/financial.py. Money arrives as a string from the API
 * (Postgres numeric), and should stay a string until formatted: parsing it to a
 * JS number reintroduces exactly the float error the schema avoids.
 */

export type AccountType =
  | 'checking' | 'savings' | 'brokerage' | 'credit_card'
  | 'retirement' | 'hsa' | 'loan' | 'other'

/** Characterization for the inventory. Argued, never extracted. */
export type PropertyCharacter =
  | 'community' | 'separate_petitioner' | 'separate_respondent' | 'mixed' | 'disputed'

export type StatementReviewStatus =
  | 'auto_accepted' | 'needs_review' | 'accepted' | 'rejected'

export type DateProvenance = 'printed' | 'derived' | 'unknown'

/**
 * Who holds an account.
 *
 * `opposing_party_id` alone could not say "joint" — and joint is the difference
 * between an asset one side keeps and an asset the court divides. That field
 * now names *which* other party: the sole owner, or the co-holder.
 */
export type AccountOwnership =
  | 'client_sole' | 'opposing_sole' | 'joint' | 'third_party' | 'unknown'

/**
 * One inference the extractor made, one finding about a statement, or one
 * correction a person made afterwards.
 *
 * The `by`/`at`/`from`/`to` half is present only on MANUAL_CORRECTION entries:
 * a corrected figure ends up in an exhibit, so the record carries who changed
 * it and what it said before.
 */
export interface ExtractionFlag {
  code: string
  severity: 'info' | 'warn'
  field_path: string | null
  note: string
  by?: string
  by_staff_id?: number
  at?: string
  from?: string | number | null
  to?: string | number | null
  reason?: string | null
}

export interface FinancialAccount {
  id: number
  matter_id: number
  institution: string
  account_type: AccountType
  account_number_last4: string | null
  account_number_masked: string | null
  name_on_account: string | null
  opposing_party_id: number | null
  ownership: AccountOwnership
  property_character: PropertyCharacter | null
  purpose: string | null
  notes: string | null
  /** The account this one succeeds - a reissued card, a bank migration, a rollover. */
  antecedent_account_id: number | null
  is_closed: boolean
}

export interface FinancialAccountUpdatePayload {
  institution?: string
  account_type?: AccountType
  account_number_last4?: string | null
  account_number_masked?: string | null
  name_on_account?: string | null
  opposing_party_id?: number | null
  ownership?: AccountOwnership
  property_character?: PropertyCharacter | null
  purpose?: string | null
  notes?: string | null
  antecedent_account_id?: number | null
  is_closed?: boolean
}

export interface AccountStatement {
  id: number
  financial_account_id: number
  matter_id: number
  period_start: string
  period_end: string
  /** Money as strings — see the note at the top of this file. */
  beginning_balance: string | null
  ending_balance: string | null
  computed_ending_balance: string | null
  reconciled: boolean
  /** Printed close minus computed. Recorded, never corrected away. */
  reconciliation_delta: string | null
  printed_totals: Record<string, unknown>
  flags: ExtractionFlag[]
  review_status: StatementReviewStatus
  storage_path: string | null
  source_job_id: string | null
  ingested_by_staff_id: number
  created_at: string
  /** The uploaded file's own name — storage renames it to the job id. */
  source_filename: string | null
  bates_first: string | null
  bates_last: string | null
}

export interface AccountTransaction {
  id: number
  statement_id: number
  financial_account_id: number
  line_no: number
  transaction_date: string | null
  posted_date: string | null
  date_provenance: DateProvenance
  description: string
  description_lines: string[]
  counterparty: string | null
  location: string | null
  /** Signed by its effect on the printed balance. */
  amount: string
  running_balance: string | null
  /** Free-text guess from extraction - a hint for whoever categorizes, never authoritative. */
  category: string | null
  /** The authoritative bucket, set by a human. */
  category_id: number | null
  physical_page_number: number | null
  bates_number: string | null
  /**
   * The check this was drawn on.
   *
   * A card purchase names the merchant; a check says only its number, so this
   * is what a discovery request asks about when money leaves without a payee.
   */
  check_number: string | null
  flags: ExtractionFlag[]
  /**
   * Set when somebody dropped this line from the statement.
   *
   * Hidden everywhere and excluded from every total, but kept: dropping a line
   * asserts it is not printed on the document, and that reaches an exhibit.
   */
  deleted_at: string | null
  deleted_by_staff_id: number | null
  deletion_reason: string | null
}

/**
 * The Bates run found across an uploaded document.
 *
 * Found by pattern over the page text, not read by the model — the numeric part
 * advances one per page, and nothing else on a statement behaves that way.
 */
export interface BatesSeriesSummary {
  /** Letters before the number, e.g. 'KF'. Empty for a bare numeric stamp. */
  prefix: string
  /** What the document prints between prefix and digits. */
  separator: string
  /** Zero-padded width of the numeric part. */
  digits: number
  first: string | null
  last: string | null
  pages_stamped: number
  /** Pages with no readable stamp. Their lines get no citation — never an interpolated one. */
  unstamped_pages: number[]
  /** Numbers missing from the run that no page in hand accounts for. */
  gaps: string[]
  confidence: 'high' | 'low'
}

/** What became of one statement found in an uploaded document. */
export interface StatementIngestOutcome {
  status: 'auto_accepted' | 'needs_review' | 'duplicate' | 'error'
  statement_id: number | null
  account_id: number | null
  institution: string | null
  period: [string, string] | null
  transactions: number | null
  reconciled: boolean | null
  delta: string | null
  bates_first: string | null
  bates_last: string | null
  bates_gaps: string[]
  error: string | null
}

export interface StatementIngestSummary {
  statements_found: number
  auto_accepted: number
  needs_review: number
  results: StatementIngestOutcome[]
  /** Null when the document is not a stamped production copy. */
  bates: BatesSeriesSummary | null
}

export interface StatementJobStatus {
  id: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  result: StatementIngestSummary | null
  error: string | null
}


// ── Classification ───────────────────────────────────────────────────────
//
// Two axes, deliberately not the same mechanism.
//
// A *category* is one per transaction, from a firm-wide hierarchy. It drives
// the Financial Information Statement — the personal income statement filed for
// a temporary orders hearing — where every line lands in exactly one bucket or
// the totals double-count.
//
// *Tags* are many-to-many and drive everything else: the Rule 1006 summaries
// behind waste, constructive fraud, and reimbursement claims. One line is
// routinely evidence in several exhibits at once.

export interface TransactionCategory {
  id: number
  description: string
  parent_id: number | null
  display_order: number
  /** Whether this bucket appears on the Financial Information Statement. */
  include_in_fis: boolean
  is_active: boolean
  /** Levels below the root — the picker indents by this. */
  depth: number
  /** Ancestors joined with ' > '. A leaf name alone is ambiguous: "Gas" is both a utility and a car expense. */
  path: string
}

export interface TransactionCategoryPayload {
  description?: string
  parent_id?: number | null
  display_order?: number
  include_in_fis?: boolean
  is_active?: boolean
}

export interface TransactionTag {
  id: number
  /** Null for a firm-wide tag; a matter id scopes it to that case. */
  matter_id: number | null
  label: string
  description: string | null
  color: string | null
  display_order: number
  is_active: boolean
  /** Lines carrying this tag — the size of the exhibit it would produce. */
  usage_count: number | null
}

export interface TransactionTagPayload {
  label?: string
  description?: string | null
  color?: string | null
  display_order?: number
  is_active?: boolean
}

// ── Search ───────────────────────────────────────────────────────────────

export interface TransactionSearchFilter {
  account_ids?: number[] | null
  date_from?: string | null
  date_to?: string | null
  category_ids?: number[] | null
  /** Expand each category to its descendants. On by default. */
  include_subcategories?: boolean
  uncategorized?: boolean
  tag_ids?: number[] | null
  /** Require every listed tag rather than any of them. */
  tag_match_all?: boolean
  untagged?: boolean
  text?: string | null
  /** One check, by number. */
  check_number?: string | null
  /** Every check on the account and nothing else. */
  checks_only?: boolean
  /** Show lines somebody dropped. Off by default. */
  include_deleted?: boolean
  limit?: number
  offset?: number
}

/** A transaction plus the context a result row displays. */
export interface TransactionSearchRow extends AccountTransaction {
  tag_ids: number[]
  institution: string | null
  account_last4: string | null
}

export interface TransactionSearchResult {
  /** Every matching line, not just this page — how big the exhibit is. */
  total: number
  items: TransactionSearchRow[]
  /** Signed total of the rows on this page, as a string. */
  sum_amount: string
}

export interface BulkResult {
  /** Rows actually altered; re-applying an existing tag is a no-op. */
  changed: number
}



// ── Merging two rows that are one account ────────────────────────────────

/** One reason a merge is unsafe, or worth a second look. */
export interface MergeConflict {
  code: 'SAME_ACCOUNT' | 'DIFFERENT_MATTER' | 'PERIOD_OVERLAP'
      | 'BATES_OVERLAP' | 'LAST4_MISMATCH' | 'TYPE_MISMATCH'
  /** True when force cannot override it. */
  blocking: boolean
  detail: string
}

export interface AccountMergePreview {
  source_account_id: number
  target_account_id: number
  source_label: string
  target_label: string
  statements_to_move: number
  transactions_to_move: number
  conflicts: MergeConflict[]
  can_merge: boolean
  needs_force: boolean
}

export interface AccountMergeResult {
  statements_moved: number
  transactions_moved: number
  target: FinancialAccount
}

/** A field a person may correct on an ingested line. */
export interface TransactionCorrectionPayload {
  description?: string
  transaction_date?: string | null
  posted_date?: string | null
  counterparty?: string | null
  location?: string | null
  amount?: string
  running_balance?: string | null
  bates_number?: string | null
  check_number?: string | null
  physical_page_number?: number | null
  /** Why the change was made. Kept on the flag. */
  reason?: string
}

export interface TransactionCorrectionResult {
  transaction: AccountTransaction
  /** Re-reconciled when an amount changed; null when the edit did not touch the arithmetic. */
  statement: AccountStatement | null
}

/** What a rejection removed. */
export interface StatementRejectResult {
  statement_id: number
  financial_account_id: number
  transactions_deleted: number
  account_deleted: boolean
  /** Why the emptied account survived, when it did. */
  account_kept_reason: string | null
}

/**
 * The outcome of reviewing a statement.
 *
 * Accepting returns the statement. Rejecting deletes it, so there is no
 * statement to return and `discarded` says what went instead.
 */
export interface StatementReviewResult {
  statement: AccountStatement | null
  discarded: StatementRejectResult | null
}

/** What deleting an account would take with it. */
export interface AccountDeletePreview {
  account_id: number
  account_label: string
  statements: number
  transactions: number
  /** Each statement's period, oldest first. */
  periods: string[]
  /** Reasons to stop and look. Warnings, not blocks. */
  warnings: string[]
}

/**
 * An account the produced transactions name but no produced statement covers.
 *
 * Money amounts arrive as strings and stay strings — see `lib/money.ts`. The
 * account itself is an inference from a printed reference, so `institution`
 * comes with `institution_inferred` rather than standing as fact.
 */
export interface UndisclosedAccount {
  /** Last four digits — the identity, and how two spellings merge into one. */
  last4: string
  /** The longest form of the number seen, for quoting back to its line. */
  reference: string
  institution: string | null
  /** True when the institution was assumed from the statement, not read off it. */
  institution_inferred: boolean
  mentions: number
  money_in: string
  money_out: string
  /** money_in − money_out. Negative means the matter funded this account. */
  net: string
  first_seen: string | null
  last_seen: string | null
  /** Produced accounts whose statements name this one. */
  seen_on: string[]
  /** Up to three descriptions, verbatim, so a finding traces to its page. */
  examples: string[]
}

/**
 * How an export is rendered.
 *
 * `csv` is the clean extraction — header row and data, nothing else, so it can
 * go straight into a spreadsheet or a model. The other three are full exhibits
 * carrying the case caption and the Rule 1006 verification notice.
 */
export type ExportFormat = 'csv' | 'md' | 'docx' | 'pdf'

/**
 * A file the server built for us: the bytes, the name it chose, and anything it
 * needs the user told. Mirrors DownloadedFile in lib/api.ts, re-declared here so
 * components import their types from `types` like everything else.
 */
export interface DownloadedFile {
  blob: Blob
  filename: string
  warnings: string[]
}

/**
 * A bank the wires name that this matter has no account at.
 *
 * Separate from UndisclosedAccount because a wire prints the sending
 * INSTITUTION and never the sending account — there is no number to key on.
 */
export interface ReferencedInstitution {
  institution: string
  /** Its routing number. Checksummed, so a reliable identity where a name is not. */
  aba: string | null
  wires: number
  /** Wires where sender and receiver are the same person. This is the finding. */
  same_party_wires: number
  money_in: string
  money_out: string
  net: string
  first_seen: string | null
  last_seen: string | null
  seen_on: string[]
  examples: string[]
}

/**
 * A payee the matter pays that no produced statement accounts for.
 *
 * Keyed on the payee, not on a number, because a payment to a card issuer
 * almost never prints one. Whether the payee is a creditor comes from outside
 * the description — the category its payments are filed under, or a standing
 * ruling — so `reason` travels with every row. "American Express" and "the
 * City of Lewisville" arrive here looking identical.
 */
export interface Creditor {
  payee: string
  /** What to call it on a motion; the scraped payee is a fragment. */
  creditor_name: string | null
  /** credit_card | loan | mortgage | line_of_credit | other. */
  creditor_type: string | null
  /** liability_category | classified | unreviewed. */
  reason: string
  /** The ruling that put it here, so the UI can offer to change it. */
  classification_id: number | null
  payments: number
  money_out: string
  /** Digits a payment happened to print. Usually empty — hence a payee report. */
  last4: string[]
  first_seen: string | null
  last_seen: string | null
  seen_on: string[]
  examples: string[]
}

/** Everything the production names but does not contain, by how it was found. */
export interface UndisclosedReport {
  accounts: UndisclosedAccount[]
  institutions: ReferencedInstitution[]
  creditors: Creditor[]
  /** Payees nobody has ruled on. A work queue, never a finding, never exported. */
  candidates: Creditor[]
}

/**
 * What a re-import discarded, and the job now reading the document again.
 *
 * `statements_discarded` can exceed one: a retry acts on the DOCUMENT, and a
 * combined statement holds several accounts. Re-reading recreates all of them,
 * so the siblings go too or each account ends up filed twice.
 */
export interface StatementRetryResult {
  job_id: string
  statements_discarded: number
  transactions_discarded: number
  accounts_deleted: number
  source_filename: string | null
}

/** What the firm has decided about a payee. */
export interface PayeeClassification {
  id: number
  matter_id: number | null
  pattern: string
  classification: string
  creditor_name: string | null
  creditor_type: string | null
  note: string | null
  is_active: boolean
  decided_by_staff_id: number | null
  is_firm_wide: boolean
}

export interface PayeeClassificationPayload {
  pattern: string
  classification: 'creditor' | 'not_creditor'
  matter_id?: number | null
  creditor_name?: string | null
  creditor_type?: string | null
  note?: string | null
  is_active?: boolean
}
