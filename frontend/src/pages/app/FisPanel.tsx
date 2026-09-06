import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  buildFis, buildFisSchedule, exportFis, getFisSettings, saveFisSetting,
  deleteFisSetting, categorizeTransactions, getTransactionCategories,
} from '../../lib/api'
import ExportButtons from '../../components/ExportButtons'
import FisSchedulePanel from './FisSchedulePanel'
import { money, formatDate } from '../../lib/money'
import { categoryLabel } from '../../lib/categories'
import type {
  FinancialAccount, FisStatement, FisLine, FisSetting, FisRecurrence,
  FisScheduleTransaction, TransactionCategory,
} from '../../types'
import { FIS_RECURRENCES, SUB_MONTHLY } from '../../types'

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

/** A default window: the twelve whole months ending with last month. */
function defaultWindow() {
  const now = new Date()
  const end = new Date(now.getFullYear(), now.getMonth() - 1, 1)
  const start = new Date(end.getFullYear(), end.getMonth() - 11, 1)
  return {
    startYear: start.getFullYear(), startMonth: start.getMonth() + 1,
    endYear: end.getFullYear(), endMonth: end.getMonth() + 1,
  }
}

function isZero(value: string): boolean {
  return /^-?0*\.?0*$/.test(value)
}

/**
 * The Financial Information Statement.
 *
 * Three panels, and the arrangement is the point. The window and accounts sit
 * at the top because they define whose statement this is and over what period.
 * The statement itself is on the left. Clicking any line opens the transactions
 * behind it on the right, where they can be re-filed — and the statement
 * recomputes, so classification and the document you get from it are the same
 * activity rather than two screens apart.
 *
 * The filter is deliberately narrower than the Transactions page. Whole months
 * only: "average monthly" is indefensible over three-and-a-bit months, so there
 * is no way to ask for one. No category filter either — the statement *is* the
 * category axis.
 */
export default function FisPanel({ matterId, accounts }: {
  matterId: number
  accounts: FinancialAccount[]
}) {
  const initial = defaultWindow()
  const [startYear, setStartYear] = useState(initial.startYear)
  const [startMonth, setStartMonth] = useState(initial.startMonth)
  const [endYear, setEndYear] = useState(initial.endYear)
  const [endMonth, setEndMonth] = useState(initial.endMonth)
  const [accountIds, setAccountIds] = useState<number[]>([])

  const [statement, setStatement] = useState<FisStatement | null>(null)
  const [settings, setSettings] = useState<FisSetting[]>([])
  const [categories, setCategories] = useState<TransactionCategory[]>([])
  const [compressed, setCompressed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [selected, setSelected] = useState<FisLine | null>(null)
  const [rows, setRows] = useState<FisScheduleTransaction[]>([])
  const [rowsBusy, setRowsBusy] = useState(false)
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [editingSchedule, setEditingSchedule] = useState(false)
  const [exhibitName, setExhibitName] = useState('Financial Information Statement')
  // Statement or the detail behind it. A toggle rather than a separate tab,
  // because the two must be built from the SAME selection: a schedule computed
  // over a different period would not back the document it claims to back, and
  // nobody would notice until it mattered.
  const [view, setView] = useState<'statement' | 'detail'>('statement')

  const settingByCategory = useMemo(
    () => new Map(settings.map(s => [s.category_id, s])), [settings],
  )

  const load = useCallback(async () => {
    setBusy(true)
    setError(null)
    try {
      const result = await buildFis(matterId, {
        account_ids: accountIds.length ? accountIds : null,
        start_year: startYear, start_month: startMonth,
        end_year: endYear, end_month: endMonth,
      })
      setStatement(result)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not build the statement')
    } finally {
      setBusy(false)
    }
  }, [matterId, accountIds, startYear, startMonth, endYear, endMonth])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    void (async () => {
      try {
        setSettings(await getFisSettings())
        setCategories(await getTransactionCategories())
      } catch { /* the statement still renders without them */ }
    })()
  }, [])

  /** Open the transactions behind one line. */
  const openLine = useCallback(async (line: FisLine) => {
    setSelected(line)
    setPicked(new Set())
    setEditingSchedule(false)
    setRowsBusy(true)
    try {
      // Deliberately the schedule endpoint rather than a transaction search.
      // The search returns the amount as the statement printed it, and on a
      // credit card that is positive for a purchase — so the pane contradicted
      // the line above it. Reading both from one source means they cannot
      // disagree, and it costs an extra pass over the transactions.
      const result = await buildFisSchedule(matterId, {
        account_ids: accountIds.length ? accountIds : null,
        start_year: startYear, start_month: startMonth,
        end_year: endYear, end_month: endMonth,
      }, [line.category_id])
      setRows(result.groups[0]?.transactions ?? [])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the transactions')
    } finally {
      setRowsBusy(false)
    }
  }, [matterId, accountIds, statement])

  async function refile(categoryId: number | null) {
    if (!picked.size) return
    setRowsBusy(true)
    try {
      await categorizeTransactions(matterId, [...picked], categoryId)
      setPicked(new Set())
      // Both, in order: the statement is what changed, and the open line is
      // what the user is looking at while it changes.
      await load()
      if (selected) await openLine(selected)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not re-file those transactions')
    } finally {
      setRowsBusy(false)
    }
  }

  const visible = useMemo(
    () => (statement?.lines ?? []).filter(l => !compressed || !l.empty),
    [statement, compressed],
  )

  return (
    <div className="space-y-4">
      {/* ── Window and accounts ── */}
      <div className="card p-4 space-y-3">
        <div className="flex flex-wrap items-end gap-3">
          <MonthPicker label="From" year={startYear} month={startMonth}
            onChange={(y, m) => { setStartYear(y); setStartMonth(m) }} />
          <MonthPicker label="Through" year={endYear} month={endMonth}
            onChange={(y, m) => { setEndYear(y); setEndMonth(m) }} />
          <div className="text-xs text-text-secondary pb-2">
            Whole months only — an average monthly figure over part of a month cannot be
            explained on the stand.
          </div>
        </div>

        <div>
          <span className="label">Accounts</span>
          <div className="flex flex-wrap gap-2 mt-1">
            <button type="button"
              className={`text-xs rounded-full px-3 py-1 border ${
                accountIds.length === 0
                  ? 'bg-navy text-white border-navy'
                  : 'bg-white text-text-secondary border-border hover:border-navy'}`}
              onClick={() => setAccountIds([])}>
              All {accounts.length}
            </button>
            {accounts.map(account => {
              const on = accountIds.includes(account.id)
              return (
                <button key={account.id} type="button"
                  className={`text-xs rounded-full px-3 py-1 border ${
                    on ? 'bg-navy text-white border-navy'
                       : 'bg-white text-text-secondary border-border hover:border-navy'}`}
                  onClick={() => setAccountIds(prev =>
                    on ? prev.filter(id => id !== account.id) : [...prev, account.id])}>
                  {account.institution}
                  {account.account_number_last4 ? ` ····${account.account_number_last4}` : ''}
                </button>
              )
            })}
          </div>
          <p className="text-xs text-text-secondary mt-1.5">
            The accounts decide whose statement this is.
          </p>
        </div>
      </div>

      <div className="flex gap-1 border-b border-border" role="tablist">
        {([['statement', 'Statement'], ['detail', 'Transactions by category']] as const)
          .map(([id, label]) => (
            <button key={id} type="button" role="tab" aria-selected={view === id}
              className={`px-4 py-2 text-sm border-b-2 -mb-px transition-colors ${
                view === id
                  ? 'border-navy text-navy font-medium'
                  : 'border-transparent text-text-secondary hover:text-navy'}`}
              onClick={() => setView(id)}>
              {label}
            </button>
          ))}
      </div>

      {error && (
        <div className="card p-3 border border-red-300 bg-red-50 text-sm text-red-700">{error}</div>
      )}

      {view === 'detail' && (
        <FisSchedulePanel
          matterId={matterId}
          exhibitName={exhibitName}
          request={{
            account_ids: accountIds.length ? accountIds : null,
            start_year: startYear, start_month: startMonth,
            end_year: endYear, end_month: endMonth,
          }} />
      )}

      {view === 'statement' && (<>
      {/* Everything that would make a figure below wrong. Above the statement,
          because a warning under a total is a warning nobody read. */}
      {statement && statement.warnings.length > 0 && (
        <div className="card p-3 border border-amber-300 bg-amber-50 text-sm text-amber-900 space-y-1">
          <p className="font-medium">Before relying on these figures:</p>
          <ul className="list-disc ml-5 space-y-0.5">
            {statement.warnings.map((warning, i) => <li key={i}>{warning}</li>)}
          </ul>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {/* ── The statement ── */}
        <div className="card p-4">
          <div className="flex items-baseline justify-between gap-3 mb-3">
            <div>
              <h2 className="font-display text-lg text-navy">Financial Information Statement</h2>
              {statement && (
                <p className="text-xs text-text-secondary">
                  Average monthly amounts:{' '}
                  {formatDate(statement.window.start)} – {formatDate(statement.window.end)}
                  {' · '}{statement.window.months} month{statement.window.months === 1 ? '' : 's'}
                </p>
              )}
            </div>
            <label className="text-xs text-text-secondary flex items-center gap-1.5 shrink-0">
              <input type="checkbox" checked={compressed}
                onChange={e => setCompressed(e.target.checked)} />
              Compressed
            </label>
          </div>

          {busy && <p className="text-sm text-text-secondary py-4">Computing…</p>}

          {!busy && statement && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <tbody>
                  {visible.map(line => (
                    <StatementRow key={line.category_id} line={line}
                      active={selected?.category_id === line.category_id}
                      onClick={() => void openLine(line)} />
                  ))}

                  <tr className="border-t-2 border-navy">
                    <td className="py-2 font-semibold text-navy">NET CASH FLOW PER MONTH</td>
                    <td className="py-2 text-right font-semibold tabular-nums text-navy">
                      {money(statement.net_monthly)}
                    </td>
                  </tr>
                </tbody>
              </table>

              {/* Money that is real and is in no line above. Outside the form
                  proper, because it is not a category — it is unfinished work. */}
              {statement.uncategorized.count > 0 && (
                <button type="button"
                  className="w-full mt-3 p-2 rounded border border-amber-300 bg-amber-50 text-left
                             text-xs text-amber-900 hover:bg-amber-100"
                  onClick={() => void openUncategorized()}>
                  <span className="font-medium">
                    {statement.uncategorized.count} transaction
                    {statement.uncategorized.count === 1 ? '' : 's'} not yet filed
                  </span>
                  {' — '}{money(statement.uncategorized.total)} in the window, in no line above.
                  Click to file them.
                </button>
              )}

              {statement.excluded.length > 0 && (
                <div className="mt-3 text-xs text-text-secondary">
                  <p className="font-medium text-text-primary">Excluded from this statement</p>
                  <ul className="mt-1 space-y-0.5">
                    {statement.excluded.map(row => (
                      <li key={row.category_id} className="flex justify-between gap-3">
                        <span>{row.label} ({row.transaction_count})</span>
                        <span className="tabular-nums">{money(row.total)}</span>
                      </li>
                    ))}
                  </ul>
                  <p className="mt-1">
                    Money that moved without being income or expense. Listed so it reads as
                    set aside rather than missing.
                  </p>
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── The transactions behind one line ── */}
        <div className="card p-4">
          {!selected && (
            <p className="text-sm text-text-secondary py-8 text-center">
              Click a line to see the transactions behind it, and to re-file them.
            </p>
          )}

          {selected && (
            <>
              <div className="flex items-baseline justify-between gap-3 mb-1">
                <h2 className="font-display text-lg text-navy">{selected.label}</h2>
                <span className="text-sm tabular-nums font-medium">{money(selected.monthly)}/mo</span>
              </div>

              <LineBasis line={selected}
                setting={settingByCategory.get(selected.category_id)}
                windowMonths={statement?.window.months ?? 0}
                onEdit={() => setEditingSchedule(v => !v)}
                editing={editingSchedule} />

              {editingSchedule && (
                <ScheduleEditor
                  line={selected}
                  setting={settingByCategory.get(selected.category_id)}
                  onSaved={async () => {
                    setSettings(await getFisSettings())
                    setEditingSchedule(false)
                    await load()
                  }}
                  onError={setError} />
              )}

              {picked.size > 0 && (
                <div className="my-3 p-2 rounded bg-navy/5 border border-navy/20 flex flex-wrap
                                items-center gap-2 text-sm">
                  <span className="font-medium">{picked.size} selected</span>
                  <select className="input text-sm py-1" defaultValue=""
                    aria-label="Re-file as"
                    onChange={e => {
                      const value = e.target.value
                      e.target.value = ''
                      void refile(value ? Number(value) : null)
                    }}>
                    <option value="">Re-file as…</option>
                    {categories.map(c => (
                      <option key={c.id} value={c.id}>{categoryLabel(c)}</option>
                    ))}
                  </select>
                  <button type="button" className="text-xs text-navy underline"
                    onClick={() => setPicked(new Set())}>Clear</button>
                </div>
              )}

              {rowsBusy && <p className="text-sm text-text-secondary py-3">Loading…</p>}

              {!rowsBusy && rows.length === 0 && (
                <p className="text-sm text-text-secondary py-3">
                  No transactions in this window.
                  {selected.basis === 'trailing_year' && ' The figure comes from the trailing year.'}
                  {selected.basis === 'stated' && ' The figure was entered, not derived.'}
                </p>
              )}

              {!rowsBusy && rows.length > 0 && (
                <div className="overflow-x-auto max-h-[28rem] overflow-y-auto">
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 bg-white">
                      <tr className="text-left text-xs uppercase tracking-wide text-text-secondary
                                     border-b border-border">
                        <th className="py-1.5 pr-2 w-8"></th>
                        <th className="py-1.5 pr-3 font-medium">Date</th>
                        <th className="py-1.5 pr-3 font-medium">Description</th>
                        <th className="py-1.5 font-medium text-right">Amount</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map(row => (
                        <tr key={row.id} className="border-b border-border last:border-0">
                          <td className="py-1.5 pr-2">
                            <input type="checkbox" checked={picked.has(row.id)}
                              aria-label={`Select transaction ${row.id}`}
                              onChange={e => setPicked(prev => {
                                const next = new Set(prev)
                                if (e.target.checked) next.add(row.id); else next.delete(row.id)
                                return next
                              })} />
                          </td>
                          <td className="py-1.5 pr-3 whitespace-nowrap text-text-secondary">
                            {formatDate(row.date)}
                          </td>
                          <td className="py-1.5 pr-3">{row.description}</td>
                          <td className="py-1.5 text-right tabular-nums">{money(row.amount)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* The compressed toggle above governs the export too: what you are
          looking at is what downloads. */}
      {statement && (
        <div className="card p-4">
          <ExportButtons
            name={exhibitName}
            onNameChange={setExhibitName}
            count={visible.length}
            hint={compressed
              ? 'Condensed — lines with no amount omitted'
              : 'Full form — every line, including blanks'}
            onExport={format => exportFis(
              matterId,
              {
                account_ids: accountIds.length ? accountIds : null,
                start_year: startYear, start_month: startMonth,
                end_year: endYear, end_month: endMonth,
              },
              format,
              exhibitName.trim() || 'Financial Information Statement',
              compressed,
            )}
          />
        </div>
      )}

      </>)}

      {view === 'statement' && (<>
      {/* ── Coverage ── */}
      {statement && statement.coverage.accounts.length > 0 && (
        <div className="card p-4">
          <h3 className="font-medium text-navy text-sm">Statement coverage</h3>
          <p className="text-xs text-text-secondary mt-0.5 mb-2">
            Every figure divides by {statement.window.months} months. This is whether we
            actually hold {statement.window.months} months of records.
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <tbody>
                {statement.coverage.accounts.map(account => (
                  <tr key={account.account_id} className="border-b border-border last:border-0">
                    <td className="py-1.5 pr-3">{account.label}</td>
                    <td className="py-1.5 pr-3 tabular-nums whitespace-nowrap">
                      {account.months_held} of {account.months_in_window}
                    </td>
                    <td className="py-1.5 text-xs text-text-secondary">
                      {account.missing_months.length === 0
                        ? <span className="text-success">complete</span>
                        : `missing ${account.missing_months.join(', ')}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      </>)}
    </div>
  )

  /** The uncategorized bucket opens like a line, but has no category to filter on. */
  async function openUncategorized() {
    setSelected({
      category_id: -1, parent_id: null, label: 'Not yet filed', depth: 0,
      monthly: statement?.uncategorized.monthly ?? '0.00',
      window_total: statement?.uncategorized.total ?? '0.00',
      trailing_year_total: '0.00',
      transaction_count: statement?.uncategorized.count ?? 0,
      basis: 'window', recurrence: null, legend: null, note: null, empty: false,
    })
    setPicked(new Set())
    setEditingSchedule(false)
    setRowsBusy(true)
    try {
      // No category filter, then take the unfiled group — the schedule reports
      // it as a group of its own with category_id null.
      const result = await buildFisSchedule(matterId, {
        account_ids: accountIds.length ? accountIds : null,
        start_year: startYear, start_month: startMonth,
        end_year: endYear, end_month: endMonth,
      })
      setRows(result.groups.find(g => g.category_id === null)?.transactions ?? [])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the transactions')
    } finally {
      setRowsBusy(false)
    }
  }
}

/** One line of the form. Indentation is the hierarchy, as on the paper form. */
function StatementRow({ line, active, onClick }: {
  line: FisLine
  active: boolean
  onClick: () => void
}) {
  const heading = line.depth === 0
  const blank = isZero(line.monthly)
  return (
    <tr
      className={`border-b border-border cursor-pointer ${
        active ? 'bg-navy/10' : 'hover:bg-off-white'}`}
      onClick={onClick}>
      <td className={`py-1.5 ${heading ? 'font-semibold text-navy' : ''}`}
        style={{ paddingLeft: `${line.depth * 1.25}rem` }}>
        {line.label}
        {line.legend && (
          <span className="ml-1.5 text-xs font-normal text-text-secondary">({line.legend})</span>
        )}
        {line.basis === 'stated' && (
          <span className="ml-1.5 text-xs font-normal text-gold" title="Entered, not derived">
            entered
          </span>
        )}
      </td>
      <td className={`py-1.5 text-right tabular-nums whitespace-nowrap ${
        blank ? 'text-text-secondary' : line.monthly.startsWith('-') ? 'text-danger' : ''}`}>
        {blank ? '' : money(line.monthly)}
      </td>
    </tr>
  )
}

/**
 * Where the selected line's figure came from.
 *
 * Shown because a reader who cannot tell a derived figure from a supplied one
 * cannot check either, and the difference is exactly what gets asked about.
 */
function LineBasis({ line, setting, windowMonths, onEdit, editing }: {
  line: FisLine
  setting?: FisSetting
  windowMonths: number
  onEdit: () => void
  editing: boolean
}) {
  if (line.category_id < 0) return null

  const explanation =
    line.basis === 'stated'
      ? 'Entered by hand as an annual figure, divided by 12. The transactions are not used.'
      : line.basis === 'trailing_year'
        ? 'Totalled over the twelve months ending with the window and divided by 12, '
          + 'because a payment this infrequent covers months outside the window.'
        : `Totalled over the window and divided by ${windowMonths} months.`

  return (
    <div className="text-xs text-text-secondary border-b border-border pb-2 mb-2">
      <div className="flex items-start justify-between gap-3">
        <p className="flex-1">
          {explanation}
          {line.note && <> {line.note}</>}
        </p>
        <button type="button" className="text-navy underline shrink-0"
          onClick={onEdit}>
          {editing ? 'Cancel' : setting ? 'Change schedule' : 'Set schedule'}
        </button>
      </div>
      {setting?.is_default && (
        <p className="mt-1 italic">
          Using the firm-wide default for this category.
        </p>
      )}
    </div>
  )
}

/**
 * Set how often a category is paid, for this person.
 *
 * The recurrence is arithmetic before it is a label: quarterly and annual lines
 * are computed from the trailing year, which is what stops a single tax bill
 * reading as $1,800 a month over a two-month window.
 */
function ScheduleEditor({ line, setting, onSaved, onError }: {
  line: FisLine
  setting?: FisSetting
  onSaved: () => Promise<void>
  onError: (message: string) => void
}) {
  const [recurrence, setRecurrence] = useState<FisRecurrence>(setting?.recurrence ?? 'monthly')
  const [stated, setStated] = useState(setting?.stated_annual_amount ?? '')
  const [note, setNote] = useState(setting?.note ?? '')
  const [busy, setBusy] = useState(false)

  async function save() {
    setBusy(true)
    try {
      await saveFisSetting({
        category_id: line.category_id,
        recurrence,
        stated_annual_amount: stated.trim() || null,
        note: note.trim() || null,
      })
      await onSaved()
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Could not save the schedule')
    } finally {
      setBusy(false)
    }
  }

  async function clear() {
    if (!setting || setting.is_default) return
    setBusy(true)
    try {
      await deleteFisSetting(setting.id)
      await onSaved()
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Could not clear the schedule')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="border border-navy/30 rounded p-3 mb-3 space-y-2 bg-off-white/50">
      <div className="grid gap-2 sm:grid-cols-2">
        <div>
          <label className="label" htmlFor="fis-recurrence">Paid</label>
          <select id="fis-recurrence" className="input text-sm w-full" value={recurrence}
            onChange={e => setRecurrence(e.target.value as FisRecurrence)}>
            {FIS_RECURRENCES.map(r => (
              <option key={r.value} value={r.value}>{r.label}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="fis-stated">Annual amount (optional)</label>
          <input id="fis-stated" className="input text-sm w-full font-mono" value={stated}
            placeholder="-3600.00"
            onChange={e => setStated(e.target.value)} />
        </div>
      </div>

      <div>
        <label className="label" htmlFor="fis-note">Note</label>
        <input id="fis-note" className="input text-sm w-full" value={note}
          placeholder="escrowed with the mortgage"
          onChange={e => setNote(e.target.value)} />
      </div>

      <p className="text-xs text-text-secondary">
        {SUB_MONTHLY.includes(recurrence)
          ? 'Computed from the twelve months ending with the window, so the figure holds still '
            + 'as the window grows — and finds a payment made before it.'
          : 'Computed from the window, divided by its months.'}
        {' '}An annual amount entered here overrides that entirely; make it negative for an
        expense.
      </p>

      <div className="flex items-center gap-3">
        <button type="button" className="btn-primary text-sm" disabled={busy}
          onClick={() => void save()}>{busy ? 'Saving…' : 'Save schedule'}</button>
        {setting && !setting.is_default && (
          <button type="button" className="text-xs text-danger underline" disabled={busy}
            onClick={() => void clear()}>Revert to the firm default</button>
        )}
      </div>
    </div>
  )
}

/** A month and a year, because the window is whole months by construction. */
function MonthPicker({ label, year, month, onChange }: {
  label: string
  year: number
  month: number
  onChange: (year: number, month: number) => void
}) {
  const thisYear = new Date().getFullYear()
  const years = Array.from({ length: 12 }, (_, i) => thisYear - i)
  return (
    <div>
      <span className="label">{label}</span>
      <div className="flex gap-1 mt-1">
        <select className="input text-sm" value={month} aria-label={`${label} month`}
          onChange={e => onChange(year, Number(e.target.value))}>
          {MONTHS.map((name, i) => <option key={name} value={i + 1}>{name}</option>)}
        </select>
        <select className="input text-sm" value={year} aria-label={`${label} year`}
          onChange={e => onChange(Number(e.target.value), month)}>
          {years.map(y => <option key={y} value={y}>{y}</option>)}
        </select>
      </div>
    </div>
  )
}
