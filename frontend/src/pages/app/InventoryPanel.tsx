import { useEffect, useState } from 'react'
import { exportInventory, getInventory } from '../../lib/api'
import { money, isNegative, formatDate } from '../../lib/money'
import ExportButtons from '../../components/ExportButtons'
import StatementPdfButton from '../../components/StatementPdfButton'
import type { InventoryReport, InventoryRow } from '../../types'

/**
 * What each account was worth, and who holds it.
 *
 * The one-page answer to "what is in this estate?". Three things it says that a
 * list of accounts does not:
 *
 * - **A liability is negative**, because it reduces the estate. The statement
 *   itself prints a positive balance owed, and the stored figure is that one.
 * - **Every row speaks as of its own date.** There is no current balance in
 *   this system and there should not be: each figure traces to a statement
 *   somebody can pull, and the Bates number in the last column is how.
 * - **An account with no statement is listed without one**, outside the total.
 *   It is the row most worth seeing.
 */
export default function InventoryPanel({ matterId }: { matterId: number }) {
  const [report, setReport] = useState<InventoryReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [exhibitName, setExhibitName] = useState('Account Inventory')

  useEffect(() => {
    getInventory(matterId)
      .then(setReport)
      .catch(e => setError(e instanceof Error ? e.message : 'Could not build the inventory'))
  }, [matterId])

  if (error) return <p className="card p-5 text-sm text-red-700">{error}</p>
  if (!report) return <p className="card p-5 text-sm text-text-secondary">Loading…</p>

  return (
    <div className="space-y-4">
      {report.warnings.map((warning, i) => (
        <p key={i} className="rounded-md bg-amber-50 border border-amber-200 px-3 py-2 text-sm text-amber-900">
          {warning}
        </p>
      ))}

      <Section title="Assets" rows={report.assets} total={report.totals.assets}
        accountLabel="Account" />
      <Section title="Liabilities" rows={report.liabilities} total={report.totals.liabilities}
        accountLabel="Account" institutionLabel="Creditor" />

      <div className="card p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 className="font-semibold text-navy">Net</h2>
          <span className={`text-lg font-medium tabular-nums ${
            isNegative(report.totals.net) ? 'text-red-700' : 'text-text-primary'}`}>
            {money(report.totals.net)}
          </span>
        </div>
        <p className="text-xs text-text-secondary mt-1">
          {report.totals.accounts} account{report.totals.accounts === 1 ? '' : 's'} on this matter
          {report.totals.without_balance > 0 && (
            <>
              {' · '}
              <span className="text-amber-700">
                {report.totals.without_balance} with no statement on file, listed without a
                balance and not included in either total
              </span>
            </>
          )}
        </p>
        <p className="text-xs text-text-secondary mt-2">
          {report.legend.map(([letter, meaning]) => `${letter} = ${meaning}`).join(' · ')}
        </p>
      </div>

      <ExportButtons
        name={exhibitName}
        onNameChange={setExhibitName}
        count={report.totals.accounts}
        hint="CSV is the flat list for a spreadsheet; MD, DOCX and PDF carry the caption,
              the totals, the letter legend and the verification notice"
        onExport={format => exportInventory(
          matterId, format, exhibitName.trim() || 'Account Inventory')}
      />
    </div>
  )
}

function Section({ title, rows, total, institutionLabel = 'Institution' }: {
  title: string
  rows: InventoryRow[]
  total: string
  accountLabel?: string
  institutionLabel?: string
}) {
  if (rows.length === 0) return null
  return (
    <div className="card">
      <h2 className="font-semibold text-navy px-5 py-3 border-b border-border">{title}</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-text-secondary border-b border-border">
              <th className="px-5 py-2 font-medium">{institutionLabel}</th>
              <th className="px-3 py-2 font-medium">Account</th>
              <th className="px-3 py-2 font-medium text-center">Held by</th>
              <th className="px-3 py-2 font-medium">As of</th>
              <th className="px-3 py-2 font-medium text-right">Balance</th>
              <th className="px-3 py-2 font-medium">Bates</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(row => (
              <tr key={row.account_id} className="border-b border-border/60">
                <td className="px-5 py-2">
                  {row.institution}
                  {row.is_closed && (
                    <span className="ml-1 text-xs text-text-secondary">(closed)</span>
                  )}
                </td>
                <td className="px-3 py-2 font-mono text-xs">{row.account_number_last4 ?? '—'}</td>
                <td className="px-3 py-2 text-center">{row.held_by}</td>
                <td className="px-3 py-2 tabular-nums text-text-secondary">
                  {row.as_of ? formatDate(row.as_of) : '—'}
                </td>
                <td className={`px-3 py-2 text-right tabular-nums ${
                  row.balance === null ? 'text-amber-700'
                    : isNegative(row.balance) ? 'text-red-700' : 'text-text-primary'}`}>
                  {/* Never a zero. Nobody knows what this account holds, and a
                      figure there would be an assertion the record cannot
                      support. */}
                  {row.balance === null ? 'no statement' : money(row.balance)}
                </td>
                <td className="px-3 py-2 font-mono text-xs text-text-secondary">
                  <span className="inline-flex items-center gap-1.5">
                    {row.bates ?? '—'}
                    {row.statement_id !== null && (
                      <StatementPdfButton statementId={row.statement_id}
                        label="Show the statement this balance came from" />
                    )}
                  </span>
                </td>
              </tr>
            ))}
            <tr className="border-t-2 border-navy/20 font-medium">
              <td className="px-5 py-2">TOTAL</td>
              <td colSpan={3} />
              <td className={`px-3 py-2 text-right tabular-nums ${
                isNegative(total) ? 'text-red-700' : 'text-text-primary'}`}>
                {money(total)}
              </td>
              <td />
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  )
}
