/**
 * Prices and spend — mirrors app/schemas/costs.py.
 *
 * Money is a STRING here, as everywhere in this app: exact from Postgres
 * `numeric` to the screen, and never through Number().
 */

/** Which normalized usage a price applies to. Must match service_rates' CHECK. */
export type RateMeter = 'input_uncached' | 'input_cache_read' | 'input_cache_write' | 'output'

/** How a spend report is broken down. Must match llm_cost_report's list. */
export type CostGroup = 'firm' | 'vendor_model' | 'profile' | 'matter' | 'staff' | 'source'

export interface ServiceRate {
  id: number
  provider: string
  model: string
  meter: RateMeter
  unit_count: number
  /** Exact decimal string, six places. */
  price: string
  currency: string
  effective_from: string
  effective_to: string | null
  in_force: boolean
  source_url: string | null
  note: string | null
  created_at: string
}

export interface SetServiceRatePayload {
  provider: string
  model: string
  meter: RateMeter
  /** A string, so the figure reaches the API exactly as typed. */
  price: string
  unit_count?: number
  effective_from: string
  source_url?: string | null
  note?: string | null
}

/** PATCH: send only what changes. Dates are not correctable here. */
export interface CorrectServiceRatePayload {
  price?: string
  source_url?: string | null
  note?: string | null
}

export interface CostReportRow {
  month: string
  group_key: string
  group_label: string
  calls: number
  failed_calls: number
  no_usage_calls: number
  unpriced_calls: number
  input_uncached_tokens: number | null
  input_cache_read_tokens: number | null
  input_cache_write_tokens: number | null
  output_tokens: number | null
  priced_cost: string
  priced_cost_precise: string
}

export interface CostReport {
  date_from: string
  date_to: string
  group: CostGroup
  rows: CostReportRow[]
  total_calls: number
  total_unpriced_calls: number
  total_no_usage_calls: number
  total_priced_cost: string
  total_priced_cost_precise: string
  /** False whenever a call is unpriced: the total is then a floor, not a total. */
  complete: boolean
}

export interface UnpricedMeter {
  vendor: string
  /** The model to enter a price under: the one the vendor says served the calls. */
  model: string
  /** What the catalog asked for. Differs from `model` when the vendor answered under a versioned name. */
  requested_models: string[]
  meter: RateMeter
  calls: number
  tokens: number
  /** A price must start on or before this to cover every affected call. */
  first_seen: string
  last_seen: string
}
