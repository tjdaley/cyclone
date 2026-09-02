import { useCallback, useEffect, useState } from 'react'
import { getUndisclosedAccounts } from '../../lib/api'
import { money, formatDate } from '../../lib/money'
import type { UndisclosedAccount } from '../../types'

/**
 * True for a numeric string that is zero, without parsing it.
 *
 * One direction is usually empty — an account that only received money has no
 * "out" — and a column of $0.00 reads as a figure rather than as absence.
 */
function isZero(value: string): boolean {
  return /^-?0*\.?0*$/.test(value)
}

/**
 * Accounts the production talks about but does not produce.
 *
 * Transfers name both sides. When one side is an account nobody filed a
 * statement for, the produced documents have identified the gap in themselves —
 * with dates, amounts, and a line to point at.
 *
 * The panel is deliberately a reading surface rather than a workspace. Every
 * row is a question to put to the other side, and the answer arrives as a
 * document, not as a click here. What it owes the reader is enough to write
 * the request: which account, how much moved, when, and the words on the page
 * that say so.
 */
export default function UndisclosedAccountsPanel({ matterId }: { matterId: number }) {
  const [rows, setRows] = useState<UndisclosedAccount[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setRows(await getUndisclosedAccounts(matterId))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not scan the transactions')
    } finally {
      setLoading(false)
    }
  }, [matterId])

  useEffect(() => { void load() }, [load])

  // The dagger is only worth explaining if something on screen carries one.
  const anyInferred = rows.some(r => r.institution_inferred)

  return (
    <div className="card p-5 space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-display text-navy">Referenced but not produced</h2>
          <p className="text-sm text-text-secondary mt-1 max-w-2xl">
            Accounts named in transfer descriptions on the statements we have, with no
            statement of their own on this matter.
          </p>
        </div>
        <button type="button" className="btn-secondary text-sm shrink-0"
          onClick={() => void load()} disabled={loading}>
          {loading ? 'Scanning…' : 'Rescan'}
        </button>
      </div>

      {error && (
        <div className="p-3 border border-red-300 bg-red-50 text-sm text-red-700 rounded">
          {error}
        </div>
      )}

      {!loading && !error && rows.length === 0 && (
        <p className="text-sm text-text-secondary py-4">
          Every account mentioned in a transfer is already on this matter. That is the
          result you want — though it only covers the statements produced so far.
        </p>
      )}

      {rows.length > 0 && (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-text-secondary border-b border-border">
                  <th className="py-2 pr-4 font-medium">Account</th>
                  <th className="py-2 pr-4 font-medium text-right">Lines</th>
                  <th className="py-2 pr-4 font-medium text-right">In</th>
                  <th className="py-2 pr-4 font-medium text-right">Out</th>
                  <th className="py-2 pr-4 font-medium">Activity</th>
                  <th className="py-2 font-medium">Seen on</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(row => {
                  const open = expanded === row.last4
                  return (
                    <tr key={row.last4}
                      className="border-b border-border last:border-0 align-top cursor-pointer hover:bg-off-white"
                      onClick={() => setExpanded(open ? null : row.last4)}>
                      <td className="py-2.5 pr-4">
                        <div className="font-medium text-navy">
                          {row.institution ?? 'Unknown institution'}
                          {' ····'}{row.last4}
                          {row.institution_inferred && (
                            <span className="text-gold ml-0.5" title="Institution inferred, not stated">†</span>
                          )}
                        </div>
                        <div className="text-xs text-text-secondary font-mono mt-0.5">
                          as printed: {row.reference}
                        </div>
                        {open && row.examples.length > 0 && (
                          <ul className="mt-2 space-y-1">
                            {row.examples.map((example, i) => (
                              <li key={i} className="text-xs font-mono text-text-secondary
                                bg-off-white border border-border rounded px-2 py-1">
                                {example}
                              </li>
                            ))}
                          </ul>
                        )}
                      </td>
                      <td className="py-2.5 pr-4 text-right tabular-nums">{row.mentions}</td>
                      <td className="py-2.5 pr-4 text-right tabular-nums text-success">
                        {isZero(row.money_in) ? '—' : money(row.money_in)}
                      </td>
                      <td className="py-2.5 pr-4 text-right tabular-nums text-danger">
                        {isZero(row.money_out) ? '—' : money(row.money_out)}
                      </td>
                      <td className="py-2.5 pr-4 whitespace-nowrap text-text-secondary">
                        {row.first_seen
                          ? `${formatDate(row.first_seen)} – ${formatDate(row.last_seen)}`
                          : '—'}
                      </td>
                      <td className="py-2.5 text-text-secondary">
                        {row.seen_on.join(', ')}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* The reasoning behind the list, stated where the list is read. An
              inference presented without its basis is just an assertion. */}
          <div className="text-xs text-text-secondary space-y-1 pt-1 border-t border-border">
            {anyInferred && (
              <p>
                <span className="text-gold">†</span>{' '}
                Institution inferred: the description named an account number but no bank,
                so it is assumed to be held at the institution whose statement it was
                printed on. Verify before relying on it.
              </p>
            )}
            <p>
              Direction is taken from the sign of each amount, not from the words
              &ldquo;to&rdquo; and &ldquo;from&rdquo; — a description often carries both.
              Accounts are matched on their last four digits.
            </p>
            <p>
              Click a row for the descriptions these figures came from. This is a list of
              questions to ask, not findings: confirm each against the statements before
              it goes into a request or a pleading.
            </p>
          </div>
        </>
      )}
    </div>
  )
}
