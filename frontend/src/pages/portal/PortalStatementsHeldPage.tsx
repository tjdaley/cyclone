import { useEffect, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { getPortalCompliance } from '../../lib/api'
import { formatDate } from '../../lib/money'
import type { ComplianceMatrix, PortalMatter } from '../../types'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/**
 * Which months have a statement, and which do not.
 *
 * The most useful screen in the portal, because it is the one piece of work
 * only the client can do: finding the months nobody has. It is the staff
 * compliance matrix, with the Bates numbers dropped — a client checking whether
 * March is there does not need the stamp — and worded as a list to go and find
 * rather than as a production deficiency.
 */
export default function PortalStatementsHeldPage() {
  const { matter } = useOutletContext<{ matter: PortalMatter }>()
  const [matrix, setMatrix] = useState<ComplianceMatrix | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getPortalCompliance(matter.id, 'client')
      .then(setMatrix)
      .catch(e => setError(e instanceof Error ? e.message : 'Could not load this'))
  }, [matter.id])

  if (error) {
    return <p className="rounded-md bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-800">
      {error}
    </p>
  }
  if (!matrix) {
    return <p className="text-text-secondary">Loading…</p>
  }

  const missing = matrix.accounts.reduce((n, a) => n + a.gaps.length, 0)

  return (
    <div className="space-y-4">
      <div className="card p-5">
        <h2 className="font-display text-xl text-navy">What is still needed</h2>
        <p className="text-text-secondary mt-1">
          A tick means your attorney's office has that month's statement. A blank means they do
          not — those are the ones worth looking for in your online banking or your files.
        </p>
        {missing > 0 && (
          <p className="mt-3 text-sm text-text-primary">
            There {missing === 1 ? 'is' : 'are'} <strong>{missing}</strong> gap
            {missing === 1 ? '' : 's'} listed below.
          </p>
        )}
      </div>

      {matrix.accounts.map(account => (
        <div key={account.account_id} className="card p-5">
          <h3 className="font-medium text-navy">
            {account.institution}
            {account.last4 && <span className="font-mono text-sm text-text-secondary"> ····{account.last4}</span>}
            <span className="ml-2 text-xs rounded-full bg-navy/5 px-2 py-0.5">{account.type_label}</span>
          </h3>

          <div className="overflow-x-auto mt-3">
            <table className="text-sm">
              <thead>
                <tr className="text-xs uppercase tracking-wide text-text-secondary">
                  <th className="px-2 py-1 text-left font-medium">Year</th>
                  {MONTHS.map(m => <th key={m} className="px-2 py-1 font-medium w-10">{m}</th>)}
                </tr>
              </thead>
              <tbody>
                {matrix.years.map(year => (
                  <tr key={year} className="border-t border-border/60">
                    <td className="px-2 py-1 tabular-nums text-text-secondary">{year}</td>
                    {MONTHS.map((_, index) => {
                      const key = `${year}-${String(index + 1).padStart(2, '0')}`
                      const held = (account.cells[key] ?? []).length > 0
                      const beforeOpening = account.opening_month !== null && key < account.opening_month
                      const afterClosing = account.closing_month !== null && key > account.closing_month
                      return (
                        <td key={key} className="px-2 py-1 text-center">
                          {held
                            ? <span className="text-green-700" title="Held">✓</span>
                            : beforeOpening || afterClosing
                              ? <span className="text-text-secondary/40" title="The account was not open">—</span>
                              : <span className="text-amber-600" title="Not held">·</span>}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {account.gaps.length > 0 && (
            <ul className="mt-3 space-y-1 text-sm text-text-primary">
              {account.gaps.map((gap, i) => (
                <li key={i}>
                  Nothing covers <strong>{formatDate(gap.start)} – {formatDate(gap.end)}</strong>
                  {' '}<span className="text-text-secondary">({gap.days} day{gap.days === 1 ? '' : 's'})</span>
                </li>
              ))}
            </ul>
          )}
          {account.nothing_before && (
            <p className="mt-2 text-sm text-text-secondary">
              Nothing is held before {formatDate(account.nothing_before)}.
            </p>
          )}
        </div>
      ))}

      {matrix.accounts.length === 0 && (
        <p className="card p-8 text-center text-text-secondary">
          No accounts have been added to your case yet.
        </p>
      )}
    </div>
  )
}
