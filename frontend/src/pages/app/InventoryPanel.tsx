import { useEffect, useState } from 'react'
import {
  deleteCurrencyRate, exportInventory, getInventory, setCurrencyRate, setInventoryExclusion,
} from '../../lib/api'
import { money, isNegative, formatDate, formatTimestampDate } from '../../lib/money'
import ExportButtons from '../../components/ExportButtons'
import StatementPdfButton from '../../components/StatementPdfButton'
import type {
  CurrencyRate, DownloadedFile, ExportFormat, InventoryReport, InventoryRow,
} from '../../types'

/**
 * Where an inventory comes from, for a caller that is not staff (052).
 *
 * A financial professional sees the same grid through their own endpoints, and
 * cannot exclude an account or set a rate — so a source also makes the panel
 * read-only. Staff pass nothing and get the staff routes.
 */
export interface InventorySource {
  load: () => Promise<InventoryReport>
  exportInventory: (format: ExportFormat, exhibitName: string) => Promise<DownloadedFile>
  openPdf: (statementId: number, page?: number | null) => Promise<void>
}

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
 * - **An excluded account is listed in grey**, outside the totals by somebody's
 *   decision — a stale or closed account whose last balance should not move the
 *   net. The toggle is a small icon at the end of the row rather than a column
 *   of checkboxes: the grid's plainness is most of what makes it readable.
 */
export default function InventoryPanel({ matterId, source }: {
  matterId: number
  /** Read-only, through someone else's endpoints. Staff omit it. */
  source?: InventorySource
}) {
  const readOnly = source !== undefined
  const load = () => (source ? source.load() : getInventory(matterId))
  const [report, setReport] = useState<InventoryReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [exhibitName, setExhibitName] = useState('Account Inventory')
  /** The account whose exclusion is being saved, so only its icon says so. */
  const [toggling, setToggling] = useState<number | null>(null)

  useEffect(() => {
    load()
      .then(setReport)
      .catch(e => setError(e instanceof Error ? e.message : 'Could not build the inventory'))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [matterId, source])

  /**
   * Flip one account in or out of the totals. From the grid no reason is
   * recorded — the server stamps who and when — and the editor on the Accounts
   * tab is where one can be given. The report is re-read rather than patched,
   * because the totals are the server's arithmetic, not ours.
   */
  async function toggle(row: InventoryRow) {
    setToggling(row.account_id)
    try {
      await setInventoryExclusion(row.account_id, { excluded: !row.excluded })
      setReport(await getInventory(matterId))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not change the account')
    } finally { setToggling(null) }
  }

  if (error) return <p className="card p-5 text-sm text-red-700">{error}</p>
  if (!report) return <p className="card p-5 text-sm text-text-secondary">Loading…</p>

  return (
    <div className="space-y-4">
      {report.warnings.map((warning, i) => (
        <p key={i} className="rounded-md bg-amber-50 border border-amber-200 px-3 py-2 text-sm text-amber-900">
          {warning}
        </p>
      ))}

      {/* Only on a matter that holds another currency: an all-dollar
          inventory looks exactly as it always did. */}
      {report.currencies.length > 0 && readOnly && report.rates.length > 0 && (
        <p className="card p-4 text-xs text-text-secondary">
          Converted at {report.rates.map(r => `${r.usd_per_unit} USD/${r.currency} (as of ${
            formatDate(r.as_of)}${r.source ? `, ${r.source}` : ''})`).join('; ')}.
        </p>
      )}
      {report.currencies.length > 0 && !readOnly && (
        <CurrencyRates matterId={matterId} currencies={report.currencies} rates={report.rates}
          onChanged={async () => setReport(await getInventory(matterId))} />
      )}

      <Section title="Assets" rows={report.assets} total={report.totals.assets}
        accountLabel="Account" toggling={toggling} onToggle={readOnly ? undefined : toggle}
        openPdf={source?.openPdf} />
      <Section title="Liabilities" rows={report.liabilities} total={report.totals.liabilities}
        accountLabel="Account" institutionLabel="Creditor" toggling={toggling}
        onToggle={readOnly ? undefined : toggle} openPdf={source?.openPdf} />

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
        {report.totals.unconverted > 0 && (
          <p className="text-xs text-amber-700 mt-1">
            {report.totals.unconverted} account{report.totals.unconverted === 1 ? '' : 's'} in a
            currency with no rate {report.totals.unconverted === 1 ? 'is' : 'are'} shown as printed
            and not included in the totals.
            {!readOnly && <> Set a rate above to include {report.totals.unconverted === 1 ? 'it' : 'them'}.</>}
          </p>
        )}
        {report.totals.excluded > 0 && (
          <p className="text-xs text-text-secondary mt-1">
            <span className="inline-block w-3 h-3 align-middle rounded-sm bg-gray-100 border border-gray-300 mr-1.5" />
            {report.totals.excluded} account{report.totals.excluded === 1 ? '' : 's'} in grey{' '}
            {report.totals.excluded === 1 ? 'is' : 'are'} listed but not included in the totals.
          </p>
        )}
      </div>

      <ExportButtons
        name={exhibitName}
        onNameChange={setExhibitName}
        count={report.totals.accounts}
        hint="CSV is the flat list for a spreadsheet; MD, DOCX and PDF carry the caption,
              the totals, the letter legend and the verification notice"
        onExport={format => (source
          ? source.exportInventory(format, exhibitName.trim() || 'Account Inventory')
          : exportInventory(matterId, format, exhibitName.trim() || 'Account Inventory'))}
      />
    </div>
  )
}

function Section({ title, rows, total, institutionLabel = 'Institution', toggling, onToggle, openPdf }: {
  title: string
  rows: InventoryRow[]
  total: string
  accountLabel?: string
  institutionLabel?: string
  toggling: number | null
  /** Absent when read-only: there is then no control to show at all. */
  onToggle?: (row: InventoryRow) => void
  openPdf?: (statementId: number, page?: number | null) => Promise<void>
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
              <th className="px-2 py-2 w-8"><span className="sr-only">Exclude from totals</span></th>
            </tr>
          </thead>
          <tbody>
            {rows.map(row => (
              <tr key={row.account_id} className={`group border-b border-border/60 ${
                row.excluded ? 'bg-gray-50 text-gray-400' : ''}`}>
                <td className="px-5 py-2">
                  {row.institution}
                  {row.currency !== 'USD' && (
                    <span className="ml-1.5 text-xs font-mono text-sky-800">{row.currency}</span>
                  )}
                  {row.is_closed && (
                    <span className="ml-1 text-xs text-text-secondary">(closed)</span>
                  )}
                </td>
                <td className="px-3 py-2 font-mono text-xs">{row.account_number_last4 ?? '—'}</td>
                <td className="px-3 py-2 text-center">{row.held_by}</td>
                <td className={`px-3 py-2 tabular-nums ${row.excluded ? '' : 'text-text-secondary'}`}>
                  {row.as_of ? formatDate(row.as_of) : '—'}
                </td>
                <td className={`px-3 py-2 text-right tabular-nums ${
                  row.excluded ? ''
                    : row.balance === null ? 'text-amber-700'
                    : isNegative(row.balance) ? 'text-red-700' : 'text-text-primary'}`}>
                  {/* Never a zero. Nobody knows what this account holds, and a
                      figure there would be an assertion the record cannot
                      support. */}
                  {row.balance !== null ? money(row.balance)
                    : row.native_balance !== null ? money(row.native_balance, row.currency)
                    : 'no statement'}
                  {/* The conversion is said in words, under the figure it made. */}
                  {row.native_balance !== null && (
                    <span className="block text-xs text-text-secondary font-normal"
                      title={row.conversion_note ?? undefined}>
                      {row.balance !== null
                        ? `${money(row.native_balance, row.currency)} @ ${row.usd_per_unit}`
                        : 'no rate — not in total'}
                    </span>
                  )}
                </td>
                <td className={`px-3 py-2 font-mono text-xs ${row.excluded ? '' : 'text-text-secondary'}`}>
                  <span className="inline-flex items-center gap-1.5">
                    {row.bates ?? '—'}
                    {row.statement_id !== null && (
                      <StatementPdfButton statementId={row.statement_id} opener={openPdf}
                        label="Show the statement this balance came from" />
                    )}
                  </span>
                </td>
                <td className="px-2 py-2 text-center">
                  {onToggle && (
                    <ExcludeToggle row={row} busy={toggling === row.account_id}
                      disabled={toggling !== null} onToggle={() => onToggle(row)} />
                  )}
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
              <td colSpan={2} />
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  )
}

/**
 * The in/out-of-totals control: a circle-slash at the end of the row.
 *
 * Faint until the row is hovered, so an inventory nobody has excluded anything
 * from looks exactly as it did; solid on an excluded row, so the grey has a
 * visible cause. The tooltip says what clicking will do and, once excluded,
 * who did it and when.
 */
function ExcludeToggle({ row, busy, disabled, onToggle }: {
  row: InventoryRow
  busy: boolean
  disabled: boolean
  onToggle: () => void
}) {
  const tip = busy ? 'Saving…'
    : row.excluded
      ? ['Excluded from totals' + (row.exclusion_label ? ` — ${row.exclusion_label}` : '') + '.',
         row.excluded_by
           ? `Excluded by ${row.excluded_by}` +
             (row.excluded_at ? ` on ${formatTimestampDate(row.excluded_at)}` : '') + '.'
           : '',
         'Click to include it in the totals again.'].filter(Boolean).join(' ')
      : 'Included in totals. Click to exclude it — the account stays listed, in grey.'
  return (
    <button type="button" onClick={onToggle} disabled={disabled}
      title={tip} aria-label={tip} aria-pressed={row.excluded}
      className={`inline-flex items-center justify-center w-6 h-6 rounded
        hover:bg-gray-200 disabled:cursor-not-allowed ${
        busy ? 'animate-pulse text-navy'
          : row.excluded ? 'text-gray-500'
          : 'text-gray-300 opacity-0 group-hover:opacity-100 focus:opacity-100'}`}>
      <svg viewBox="0 0 20 20" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="1.8"
        aria-hidden="true">
        <circle cx="10" cy="10" r="7" />
        <line x1="5" y1="15" x2="15" y2="5" />
      </svg>
    </button>
  )
}

/**
 * The rate each foreign currency is converted at, for this matter's inventory.
 *
 * Typed in by a person, with the date it is as of and where it came from — a
 * converted figure on a schedule is only as good as a rate somebody can cite,
 * and the exhibit prints all three beside the account. Dollars per ONE unit of
 * the currency, which is how the annotation reads ("0.0036 USD/PKR").
 */
function CurrencyRates({ matterId, currencies, rates, onChanged }: {
  matterId: number
  currencies: string[]
  rates: CurrencyRate[]
  onChanged: () => Promise<void>
}) {
  return (
    <div className="card p-5">
      <h2 className="font-semibold text-navy">Conversion rates</h2>
      <p className="text-xs text-text-secondary mt-1">
        This matter holds accounts in {currencies.join(', ')}. Their statements stay in that
        currency; the inventory shows each balance in dollars at the rate you set here, and says
        so on the row. Without a rate the balance is listed as printed and left out of the totals.
      </p>
      <div className="mt-3 space-y-2">
        {currencies.map(code => (
          <RateRow key={code} matterId={matterId} currency={code}
            rate={rates.find(r => r.currency === code)} onChanged={onChanged} />
        ))}
      </div>
    </div>
  )
}

function RateRow({ matterId, currency, rate, onChanged }: {
  matterId: number
  currency: string
  rate: CurrencyRate | undefined
  onChanged: () => Promise<void>
}) {
  const today = new Date().toISOString().slice(0, 10)
  const [value, setValue] = useState(rate?.usd_per_unit ?? '')
  const [asOf, setAsOf] = useState(rate?.as_of ?? today)
  const [source, setSource] = useState(rate?.source ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // A rate is a string end to end, like every figure here: checked by shape,
  // never parsed, so what is typed is exactly what is stored.
  const valid = /^\d*\.?\d+$/.test(value.trim()) && !/^0*\.?0*$/.test(value.trim())
  const changed = value.trim() !== (rate?.usd_per_unit ?? '') || asOf !== (rate?.as_of ?? '')
    || source.trim() !== (rate?.source ?? '')

  async function save() {
    setBusy(true); setError(null)
    try {
      await setCurrencyRate(matterId, currency, {
        usd_per_unit: value.trim(), as_of: asOf, source: source.trim() || null,
      })
      await onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the rate')
    } finally { setBusy(false) }
  }

  async function remove() {
    setBusy(true); setError(null)
    try {
      await deleteCurrencyRate(matterId, currency)
      setValue('')
      await onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not remove the rate')
    } finally { setBusy(false) }
  }

  return (
    <div className="flex flex-wrap items-end gap-3 text-sm">
      <span className="font-mono font-medium w-12 pb-2">{currency}</span>
      <div>
        <label className="label" htmlFor={`rate-${currency}`}>USD per 1 {currency}</label>
        <input id={`rate-${currency}`} className="input mt-1 w-36 font-mono" inputMode="decimal"
          placeholder="0.0036" value={value} onChange={e => setValue(e.target.value)} />
      </div>
      <div>
        <label className="label" htmlFor={`asof-${currency}`}>As of</label>
        <input id={`asof-${currency}`} type="date" className="input mt-1" value={asOf}
          onChange={e => setAsOf(e.target.value)} />
      </div>
      <div className="flex-1 min-w-48">
        <label className="label" htmlFor={`src-${currency}`}>Source</label>
        <input id={`src-${currency}`} className="input mt-1" value={source}
          placeholder="State Bank of Pakistan, interbank rate"
          onChange={e => setSource(e.target.value)} />
      </div>
      <button type="button" className="btn-primary" disabled={busy || !valid || !asOf || !changed}
        title={!valid ? 'Dollars per one unit, e.g. 0.0036' : undefined}
        onClick={save}>{busy ? 'Saving…' : 'Save'}</button>
      {rate && (
        <button type="button" className="text-xs text-red-700 underline pb-2 disabled:opacity-40"
          disabled={busy} onClick={remove}>Remove</button>
      )}
      {rate?.entered_by && (
        <span className="basis-full text-xs text-text-secondary">
          Set by {rate.entered_by}.
        </span>
      )}
      {error && <span className="basis-full text-xs text-red-700">{error}</span>}
    </div>
  )
}
