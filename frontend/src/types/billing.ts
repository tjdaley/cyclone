export type EntryType = 'time' | 'expense' | 'flat_fee'
export type BillingCycleStatus = 'open' | 'closed'

/** Mirrors BillingEntryResponse in app/schemas/billing.py */
export interface BillingEntry {
  id: number
  matter_id: number
  staff_id: number
  billing_cycle_id: number | null
  entry_type: EntryType
  entry_date: string
  invoice_date: string
  hours: number | null
  rate: number | null
  amount: number | null
  description: string
  billable: boolean
  billed: boolean
}

/** Mirrors NLBillingParseResponse in app/schemas/billing.py */
export interface ParsedBillingPreview {
  entry_type: string
  description: string
  hours: number | null
  rate: number | null
  amount: number | null
  invoice_date: string | null
  billable: boolean
  confidence: string
}
