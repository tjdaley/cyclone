/**
 * Money formatting for figures that arrive from the API as strings.
 *
 * The API sends amounts as strings precisely so exact cents survive the round
 * trip from Postgres `numeric`. Running one through `Number()` to format it
 * undoes that at the last step, in the one place where the value is about to
 * be read into evidence — so these functions never parse.
 */

/** Format a numeric string as US currency. Returns an em dash for null. */
export function money(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  const negative = value.startsWith('-')
  const [whole, fraction = ''] = value.replace(/^[-+]/, '').split('.')
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  const cents = `${fraction}00`.slice(0, 2)
  return `${negative ? '-' : ''}$${grouped}.${cents}`
}

/** True when a numeric string is below zero — a debit, or a card payment. */
export function isNegative(value: string | null | undefined): boolean {
  return typeof value === 'string' && value.startsWith('-')
}

/** Format an ISO date for display. Returns an em dash for null. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric',
  })
}
