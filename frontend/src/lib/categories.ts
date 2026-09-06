import type { TransactionCategory } from '../types'

/**
 * Indent for one level of the chart of accounts.
 *
 * Non-breaking spaces, because HTML collapses runs of ordinary whitespace and
 * an `<option>` cannot be styled per level. Indenting with plain spaces looks
 * right in the source and renders as a flat list, which is what this replaced.
 */
const STEP = '    '

/**
 * A category as it should read in a picker.
 *
 * The chart nests three deep — Electricity under Utilities under Housing — and
 * a leaf name alone is ambiguous: "Gas" is a utility and a car expense, "Other"
 * appears under both Pets and the catch-all, and "Miscellaneous" three times.
 * The indentation is what tells them apart at a glance; `path` is the fallback
 * for anywhere a tooltip can carry it.
 *
 * The API returns categories in reading order — depth-first, siblings by
 * display_order — so rendering them in the order given, indented by `depth`,
 * reproduces the form.
 */
export function categoryLabel(category: TransactionCategory, options?: {
  /** Mark the buckets that never reach the Financial Information Statement. */
  markOffStatement?: boolean
}): string {
  const suffix = options?.markOffStatement && !category.include_in_fis
    ? ' (not on FIS)'
    : ''
  return STEP.repeat(category.depth) + category.description + suffix
}
