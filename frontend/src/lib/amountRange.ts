/**
 * What is wrong with two amount boxes right now, if anything.
 *
 * The server (`util/amount_filter.py`) validates all of this again and is the
 * authority. Checking here means a half-typed or momentarily backwards range
 * shows a hint that goes away by itself, rather than sending a search whose 422
 * stays on screen. On the way to `25000` the box reads `2`, which is below a
 * lower limit of `5000` for as long as it takes to type the rest.
 *
 * A blank box is no limit in that direction, and is never a problem.
 */
const FIGURE = /^[+-]?\s*\$?\s*\d[\d,]*(\.\d{0,2})?$/

function side(text: string): string {
  return text.startsWith('-') ? '-' : text.startsWith('+') ? '+' : ''
}

/**
 * A key that sorts by size. Text rather than a number: money is a string end to
 * end here, and comparing two figures needs no arithmetic.
 */
function magnitudeKey(text: string): string {
  const [whole, cents = ''] = text.replace(/[^\d.]/g, '').split('.')
  return whole.replace(/^0+/, '').padStart(24, '0') + cents.padEnd(2, '0')
}

export function amountProblem(minDraft: string, maxDraft: string): string | null {
  const min = minDraft.trim()
  const max = maxDraft.trim()
  if ((min && !FIGURE.test(min)) || (max && !FIGURE.test(max))) {
    return 'Type a figure: 5000, -5000 for money out, or +5000 for money in.'
  }
  if (!min || !max) return null
  if (side(min) && side(max) && side(min) !== side(max)) {
    return 'One direction at a time: use the same sign in both boxes, or no sign at all.'
  }
  if (magnitudeKey(min) > magnitudeKey(max)) {
    return 'The first amount is larger than the second. Put the smaller figure first, whichever direction you are searching.'
  }
  return null
}
