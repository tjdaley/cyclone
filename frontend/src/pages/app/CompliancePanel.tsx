import { useCallback, useEffect, useState } from 'react'
import { getComplianceMatrix, exportComplianceMatrix } from '../../lib/api'
import ExportButtons from '../../components/ExportButtons'
import { formatDate } from '../../lib/money'
import StatementPdfButton from '../../components/StatementPdfButton'
import type { ComplianceAccount, ComplianceMatrix } from '../../types'

/**
 * The two obligations, and the unscoped view.
 *
 * There is no single compliance report. Opposing counsel propounds on us and
 * sets how far back WE must go; we propound on them and sets how far back THEY
 * must go. Same grid, different accounts, different look-back — so the report
 * has to say which one it is measuring, or a reader will take a clean report
 * for the wrong side as proof of nothing missing.
 */
const SIDES: { id: 'client' | 'opposing' | undefined; label: string; hint: string }[] = [
  { id: 'client', label: 'We produce',
    hint: 'Accounts our client is responsible for, against the look-back opposing counsel propounded' },
  { id: 'opposing', label: 'They produce',
    hint: 'Accounts the other side is responsible for, against the look-back we propounded' },
  { id: undefined, label: 'All accounts',
    hint: 'Every account on the matter, over the range the statements themselves cover' },
]

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function monthKey(year: number, month: number): string {
  return `${year}-${String(month + 1).padStart(2, '0')}`
}

/**
 * Whether a month falls outside the account's known life.
 *
 * Everything before an opening statement and after a closing one is not a hole
 * — it is time the account did not exist. Shown muted rather than blank so the
 * reader can tell "we know there is nothing here" from "nobody has said".
 */
function outsideLife(account: ComplianceAccount, key: string): boolean {
  if (account.opening_month && key < account.opening_month) return true
  if (account.closing_month && key > account.closing_month) return true
  return false
}

/**
 * The grid a firm otherwise builds by hand in a workbook, one tab per account
 * type — accounts down, months across, a Bates number in every filled cell.
 *
 * Its value is entirely in the blanks, which is why two things here are not
 * what a pivot table would give you:
 *
 * A **blank is not always a gap**. An account opened in March has no January
 * statement and never will, so the months outside its life are muted rather
 * than left to read as missing. That is what stops a motion to compel documents
 * nobody has — and what answers one filed against us.
 *
 * A **filled month is not a covered month**. Statement periods run 4 March to 5
 * April, so the grid can only say which months have a statement. The coverage
 * line beneath each row says which *days* nothing accounts for, and that is the
 * sentence that belongs in a motion.
 */
export default function CompliancePanel({ matterId }: { matterId: number }) {
  const [matrix, setMatrix] = useState<ComplianceMatrix | null>(null)
  const [side, setSide] = useState<'client' | 'opposing' | undefined>('client')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [exhibitName, setExhibitName] = useState('Statements Produced and Not Produced')

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setMatrix(await getComplianceMatrix(matterId, side))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not build the matrix')
    } finally {
      setLoading(false)
    }
  }, [matterId, side])

  useEffect(() => { void load() }, [load])

  if (loading && !matrix) {
    return <div className="card p-5 text-sm text-text-secondary">Building the matrix…</div>
  }
  if (error) {
    return <div className="card p-5 text-sm text-red-700 border border-red-300 bg-red-50">{error}</div>
  }
  if (!matrix) return null

  // Grouped under the same headings as the workbook tabs this replaces. The
  // backend already sorted by kind and then by name, so a change of type_label
  // is a group boundary.
  const groups: { label: string; accounts: ComplianceAccount[] }[] = []
  for (const account of matrix.accounts) {
    const last = groups[groups.length - 1]
    if (last && last.label === account.type_label) last.accounts.push(account)
    else groups.push({ label: account.type_label, accounts: [account] })
  }

  const anyOpening = matrix.accounts.some(a => a.opening_month)
  const anyClosing = matrix.accounts.some(a => a.closing_month)

  return (
    <div className="card p-5 space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-display text-navy">Statements held</h2>
          <p className="text-sm text-text-secondary mt-1 max-w-3xl">
            {matrix.totals.statements} statement{matrix.totals.statements === 1 ? '' : 's'} across
            {' '}{matrix.totals.accounts} account{matrix.totals.accounts === 1 ? '' : 's'}, by the
            month each one closes in.
          </p>
        </div>
        <button type="button" className="btn-secondary text-sm shrink-0"
          onClick={() => void load()} disabled={loading}>
          {loading ? 'Rebuilding…' : 'Rebuild'}
        </button>
      </div>

      {/* WHICH OBLIGATION THIS MEASURES. Without it, a clean report for one
          side reads as proof that nothing is missing at all — and the two are
          bounded by different dates from different requests. */}
      <div className="flex flex-wrap gap-1 border-b border-border">
        {SIDES.map(option => (
          <button key={option.label} type="button" title={option.hint}
            className={`px-3 py-1.5 text-sm border-b-2 -mb-px ${
              side === option.id
                ? 'border-navy text-navy font-medium'
                : 'border-transparent text-text-secondary hover:text-navy'}`}
            onClick={() => setSide(option.id)}>
            {option.label}
          </button>
        ))}
      </div>

      <ScopeBanner matrix={matrix} side={side} />

      {matrix.accounts.length === 0 && (
        <p className="text-sm text-text-secondary py-4">
          {side
            ? 'No accounts are marked as this side’s to produce. Set "Who produces" on the '
              + 'Accounts tab — until somebody does, an account appears on neither report.'
            : 'No accounts on this matter yet. Import a statement and the matrix builds itself.'}
        </p>
      )}

      {groups.map(group => (
        <div key={group.label} className="space-y-4">
          <h3 className="text-sm font-medium text-navy uppercase tracking-wide">{group.label}</h3>

          {group.accounts.map(account => (
            <div key={account.account_id} className="border border-border rounded p-3 space-y-2">
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="font-medium text-navy">{account.label}</span>
                {account.is_closed && (
                  <span className="text-xs text-text-secondary">closed</span>
                )}
                <span className="text-xs text-text-secondary tabular-nums">
                  {account.statements} statement{account.statements === 1 ? '' : 's'}
                </span>
              </div>

              <div className="overflow-x-auto">
                <table className="text-sm border-collapse">
                  <thead>
                    <tr className="text-xs uppercase tracking-wide text-text-secondary">
                      <th className="py-1 pr-3 text-left font-medium">Year</th>
                      {MONTHS.map(m => (
                        <th key={m} className="py-1 px-2 font-medium text-center w-20">{m}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {matrix.years.map(year => (
                      <tr key={year} className="border-t border-border">
                        <td className="py-1 pr-3 font-medium text-navy tabular-nums">{year}</td>
                        {MONTHS.map((_, month) => {
                          const key = monthKey(year, month)
                          const held = account.cells[key] ?? []
                          const dead = held.length === 0 && outsideLife(account, key)
                          const opening = held.some(c => c.boundary === 'opening')
                          const closing = held.some(c => c.boundary === 'closing')
                          return (
                            <td key={key}
                              className={`py-1 px-2 text-center align-middle border-l border-border ${
                                opening ? 'bg-green-100'
                                : closing ? 'bg-red-100'
                                : dead ? 'bg-gray-100'
                                : held.length ? '' : 'bg-amber-50'}`}>
                              {held.length === 0 ? (
                                <span className="text-text-secondary/50">{dead ? '—' : ''}</span>
                              ) : (
                                held.map(cell => (
                                  <span key={cell.statement_id}
                                    className="inline-flex items-center gap-1 whitespace-nowrap">
                                    <span className="font-mono text-xs"
                                      title={`${formatDate(cell.period_start)} – ${formatDate(cell.period_end)}`}>
                                      {cell.bates ?? '✓'}
                                    </span>
                                    {cell.boundary === 'opening' && <span title="Opening statement">†</span>}
                                    {cell.boundary === 'closing' && <span title="Closing statement">‡</span>}
                                    {cell.has_pdf && (
                                      <StatementPdfButton statementId={cell.statement_id} />
                                    )}
                                  </span>
                                ))
                              )}
                            </td>
                          )
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* THE SENTENCE THAT GOES IN THE MOTION. The grid says which
                  months have a statement; this says which days nothing covers,
                  which is a different and smaller claim. */}
              <Coverage account={account} />
            </div>
          ))}
        </div>
      ))}

      {/* The attachment to a motion to compel. Landscape in every format that
          has an orientation — fourteen columns in portrait wrap the Bates
          numbers, and a grid nobody can scan is no grid. */}
      {matrix.accounts.length > 0 && (
        <ExportButtons
          name={exhibitName}
          onNameChange={setExhibitName}
          count={matrix.totals.accounts}
          hint="CSV is the flat grid for a spreadsheet; MD, DOCX and PDF are landscape exhibits
                with the caption, the gap list and the verification notice"
          onExport={format => exportComplianceMatrix(
            matterId, format,
            exhibitName.trim() || 'Statements Produced and Not Produced',
            side)}
        />
      )}

      <div className="text-xs text-text-secondary space-y-1 pt-2 border-t border-border">
        {anyOpening && (
          <p>
            <span className="px-1 bg-green-100">†</span>{' '}
            Opening statement — the first this account had, so nothing is missing before it.
          </p>
        )}
        {anyClosing && (
          <p>
            <span className="px-1 bg-red-100">‡</span>{' '}
            Closing statement — the last, so nothing is missing after it.
          </p>
        )}
        <p>
          <span className="px-1 bg-gray-100">—</span>{' '}
          Outside the account&rsquo;s life, from the markers above. Not a gap.
          {' '}<span className="px-1 bg-amber-50">&nbsp;&nbsp;</span>{' '}
          No statement produced for that month.
        </p>
        <p>
          A cell is placed by the month its statement <strong>closes</strong> in. Periods do not
          follow calendar months, so a filled month is not necessarily a covered one — the
          coverage line under each account is what names the days actually missing.
        </p>
        <p>
          Mark a statement as opening or closing from the Accounts tab. Until somebody does,
          the report assumes statements exist on both sides, which is the cautious answer.
        </p>
      </div>
    </div>
  )
}

/**
 * What this report is measured against, said before the grid rather than after.
 *
 * A gap report with no look-back can only make open-ended claims — "anything
 * before 4 December 2019" — which no request for production can use. With one,
 * every hole has two ends. The banner says which of those two reports the
 * reader is looking at, because they are not interchangeable.
 */
function ScopeBanner({ matrix, side }: {
  matrix: ComplianceMatrix
  side: 'client' | 'opposing' | undefined
}) {
  const { scope, totals } = matrix
  const whose = side === 'client' ? 'our client' : side === 'opposing' ? 'the other side' : null

  return (
    <div className="space-y-1">
      {whose && (
        scope.bounded ? (
          <p className="text-sm text-navy">
            Measured against everything {whose} must produce from{' '}
            <strong>{formatDate(scope.since!)}</strong> through{' '}
            <strong>{formatDate(scope.through!)}</strong>.
          </p>
        ) : (
          <p className="text-sm text-amber-800">
            No look-back date recorded for {whose}, so this report can only say what falls
            between the statements produced — not what is missing from either end. Set it on the
            matter ({side === 'client'
              ? 'the date opposing counsel’s request reaches back to'
              : 'the date our own request reaches back to'}) and every gap gets two ends.
          </p>
        )
      )}
      {totals.unassigned > 0 && (
        <p className="text-sm text-amber-800">
          {totals.unassigned} account{totals.unassigned === 1 ? '' : 's'} on this matter
          {totals.unassigned === 1 ? ' has' : ' have'} no one marked as responsible for
          producing {totals.unassigned === 1 ? 'it' : 'them'}, so {totals.unassigned === 1
            ? 'it appears' : 'they appear'} on neither report. Set &ldquo;Who produces&rdquo;
          on the Accounts tab.
        </p>
      )}
    </div>
  )
}

/** The days nothing accounts for, in the words a request for production uses. */
function Coverage({ account }: { account: ComplianceAccount }) {
  const { gaps, nothing_before, nothing_after, statements } = account

  if (statements === 0) {
    return (
      <p className="text-sm text-amber-800">
        No statements produced for this account at all.
      </p>
    )
  }
  if (gaps.length === 0 && !nothing_before && !nothing_after) {
    return (
      <p className="text-sm text-success">
        Complete — every day from the opening statement to the closing one is accounted for.
      </p>
    )
  }

  return (
    <div className="text-sm text-amber-800 space-y-0.5">
      <p className="font-medium">Not accounted for:</p>
      <ul className="list-disc list-inside space-y-0.5">
        {nothing_before && (
          <li>
            anything before {formatDate(nothing_before)} — the earliest statement produced,
            and it is not marked as the account&rsquo;s first
          </li>
        )}
        {gaps.map(gap => (
          <li key={gap.start} className="tabular-nums">
            {formatDate(gap.start)} – {formatDate(gap.end)}{' '}
            <span className="text-text-secondary">
              ({gap.days} day{gap.days === 1 ? '' : 's'})
            </span>
          </li>
        ))}
        {nothing_after && (
          <li>
            anything after {formatDate(nothing_after)} — the latest statement produced,
            and it is not marked as the account&rsquo;s last
          </li>
        )}
      </ul>
    </div>
  )
}
