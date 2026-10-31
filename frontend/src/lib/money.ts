/**
 * Money formatting for figures that arrive from the API as strings.
 *
 * The API sends amounts as strings precisely so exact cents survive the round
 * trip from Postgres `numeric`. Running one through `Number()` to format it
 * undoes that at the last step, in the one place where the value is about to
 * be read into evidence — so these functions never parse.
 */

/**
 * Format a numeric string as currency. Returns an em dash for null.
 *
 * Dollars by default. Any other ISO code prints the code instead of a dollar
 * sign — `PKR 10,000,000.00` — because a statement in rupees shown with a "$"
 * makes a household look like millionaires. Nothing is converted here.
 */
export function money(value: string | null | undefined, currency?: string | null): string {
  if (value === null || value === undefined || value === '') return '—'
  const negative = value.startsWith('-')
  const [whole, fraction = ''] = value.replace(/^[-+]/, '').split('.')
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  const cents = `${fraction}00`.slice(0, 2)
  const symbol = !currency || currency.toUpperCase() === 'USD' ? '$' : `${currency.toUpperCase()} `
  return `${negative ? '-' : ''}${symbol}${grouped}.${cents}`
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

/**
 * The local calendar day of a timestamp — when somebody did something.
 *
 * Not `formatDate`, which takes a bare `YYYY-MM-DD` and would read a full
 * timestamp as an invalid date.
 */
export function formatTimestampDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const when = new Date(iso)
  if (Number.isNaN(when.getTime())) return '—'
  return when.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

/**
 * A spend figure, from the report's rounded and precise strings.
 *
 * `money()` keeps two places, so a month of fractions of a cent reads $0.00 --
 * which says FREE, and is wrong. When the rounded figure is zero and the
 * precise one is not, this says "<$0.01" instead. Decided on the strings; no
 * parsing.
 */
export function cost(rounded: string, precise: string): string {
  const nonZero = /[1-9]/.test(precise.replace(/^[-+]/, ''))
  const roundedZero = !/[1-9]/.test(rounded.replace(/^[-+]/, ''))
  if (nonZero && roundedZero) return '<$0.01'
  return money(rounded)
}

/**
 * A unit price, with every significant decimal kept and at least two.
 *
 * Model prices run below a cent per million tokens. Truncated to cents like
 * `money()`, "$0.075" would display as "$0.07" -- a wrong price on the page
 * where prices are entered and checked.
 */
export function unitPrice(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '\u2014'
  const [whole, fraction = ''] = value.replace(/^[-+]/, '').split('.')
  const trimmed = fraction.replace(/0+$/, '').padEnd(2, '0')
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `${value.startsWith('-') ? '-' : ''}$${grouped}.${trimmed}`
}
