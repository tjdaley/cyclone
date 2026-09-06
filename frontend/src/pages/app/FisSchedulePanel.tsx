import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  buildFisSchedule, exportFisSchedule, categorizeTransactions,
  markTransactionsReviewed, getTransactionCategories,
} from '../../lib/api'
import { money, formatDate } from '../../lib/money'
import { categoryLabel } from '../../lib/categories'
import ExportButtons from '../../components/ExportButtons'
import type {
  FisRequest, FisSchedule, FisScheduleGroup, FisScheduleTransaction,
  TransactionCategory,
} from '../../types'

/**
 * The transactions behind the statement, grouped by category.
 *
 * Two jobs, and the arrangement serves both. Online it is the review pass that
 * finds the line filed under the wrong heading — and, now that rules file at
 * ingest, the queue for checking what the rules did. In court it is the answer
 * to "what exactly is in Miscellaneous?", which is why every group ends with the
 * arithmetic that produced its figure on the statement.
 *
 * It shares the statement's window and accounts rather than offering its own.
 * A schedule computed over a different period would not back the document it
 * claims to back, and nobody would notice until it mattered.
 */
export default function FisSchedulePanel({ matterId, request, exhibitName }: {
  matterId: number
  /** The same selection the statement was built from. */
  request: FisRequest
  exhibitName: string
}) {
  const [schedule, setSchedule] = useState<FisSchedule | null>(null)
  const [categories, setCategories] = useState<TransactionCategory[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [open, setOpen] = useState<Set<string>>(new Set())
  const [unreviewedOnly, setUnreviewedOnly] = useState(false)
  // Seeded from the statement's own name, so the pair reads as a pair when both
  // are handed up: "Petitioner's FIS" and "Petitioner's FIS — Supporting Detail".
  const [name, setName] = useState(
    exhibitName ? `${exhibitName} — Supporting Detail` : 'Schedule of Transactions by Category')

  const load = useCallback(async () => {
    setBusy(true)
    setError(null)
    try {
      setSchedule(await buildFisSchedule(matterId, request))
      setPicked(new Set())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not build the schedule')
    } finally {
      setBusy(false)
    }
  }, [matterId, request])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    void (async () => {
      try { setCategories(await getTransactionCategories()) } catch { /* optional */ }
    })()
  }, [])

  /** A line a rule filed that nobody has confirmed. */
  const isPending = (line: FisScheduleTransaction) =>
    (line.category_source === 'rule' || line.category_source === 'similarity') && !line.reviewed

  const groups = useMemo(() => {
    const all = schedule?.groups ?? []
    if (!unreviewedOnly) return all
    return all
      .map(g => ({ ...g, transactions: g.transactions.filter(isPending) }))
      .filter(g => g.transactions.length > 0)
  }, [schedule, unreviewedOnly])

  const pendingCount = useMemo(
    () => (schedule?.groups ?? []).reduce(
      (n, g) => n + g.transactions.filter(isPending).length, 0),
    [schedule],
  )

  async function act(run: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await run()
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That did not work')
      setBusy(false)
    }
  }

  const key = (group: FisScheduleGroup) => String(group.category_id ?? 'unfiled')

  return (
    <div className="space-y-4">
      {error && (
        <div className="card p-3 border border-red-300 bg-red-50 text-sm text-red-700">{error}</div>
      )}

      {schedule && schedule.warnings.length > 0 && (
        <div className="card p-3 border border-amber-300 bg-amber-50 text-sm text-amber-900 space-y-1">
          <ul className="list-disc ml-5 space-y-0.5">
            {schedule.warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </div>
      )}

      <div className="card p-4 space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <div>
            <h2 className="font-display text-lg text-navy">Transactions by category</h2>
            {schedule && (
              <p className="text-xs text-text-secondary">
                {formatDate(schedule.window.start)} – {formatDate(schedule.window.end)}
                {' · '}{schedule.groups.length} group{schedule.groups.length === 1 ? '' : 's'}
              </p>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-3">
            {/* The queue the rules created. Shown as a count so the size of the
                remaining work is legible without opening anything. */}
            {pendingCount > 0 && (
              <label className="text-xs flex items-center gap-1.5 text-amber-900
                                bg-amber-50 border border-amber-300 rounded px-2 py-1">
                <input type="checkbox" checked={unreviewedOnly}
                  onChange={e => setUnreviewedOnly(e.target.checked)} />
                {pendingCount} filed by rule, unchecked
              </label>
            )}
            <button type="button" className="btn-secondary text-xs px-3 py-1"
              disabled={busy} onClick={() => void load()}>
              {busy ? 'Loading…' : 'Refresh'}
            </button>
          </div>
        </div>

        {picked.size > 0 && (
          <div className="p-2 rounded bg-navy/5 border border-navy/20 flex flex-wrap
                          items-center gap-2 text-sm">
            <span className="font-medium">{picked.size} selected</span>
            <select className="input text-sm py-1" defaultValue="" aria-label="Re-file as"
              onChange={e => {
                const value = e.target.value
                e.target.value = ''
                if (value) {
                  void act(() => categorizeTransactions(matterId, [...picked], Number(value)))
                }
              }}>
              <option value="">Re-file as…</option>
              {categories.map(c => (
                <option key={c.id} value={c.id}>{categoryLabel(c)}</option>
              ))}
            </select>
            {/* Confirming leaves the category alone. Agreeing with a rule is not
                the same act as filing a line. */}
            <button type="button" className="btn-secondary text-xs px-3 py-1" disabled={busy}
              onClick={() => void act(() => markTransactionsReviewed(matterId, [...picked]))}>
              Mark checked
            </button>
            <button type="button" className="text-xs text-navy underline"
              onClick={() => setPicked(new Set())}>Clear</button>
          </div>
        )}

        {busy && !schedule && <p className="text-sm text-text-secondary py-4">Building…</p>}

        {schedule && groups.length === 0 && (
          <p className="text-sm text-text-secondary py-4">
            {unreviewedOnly
              ? 'Nothing is waiting to be checked.'
              : 'No transactions in this period for the selected accounts.'}
          </p>
        )}

        <div className="space-y-3">
          {groups.map(group => {
            const id = key(group)
            const expanded = open.has(id)
            const ids = group.transactions.map(t => t.id)
            const allPicked = ids.length > 0 && ids.every(i => picked.has(i))
            return (
              <div key={id} className="border border-border rounded">
                <button type="button"
                  className="w-full flex flex-wrap items-baseline gap-2 px-3 py-2 text-left
                             hover:bg-off-white"
                  onClick={() => setOpen(prev => {
                    const next = new Set(prev)
                    if (next.has(id)) next.delete(id); else next.add(id)
                    return next
                  })}>
                  <span className="font-medium text-navy">{group.label}</span>
                  {group.legend && (
                    <span className="text-xs text-text-secondary">({group.legend})</span>
                  )}
                  <span className="text-xs text-text-secondary">
                    {group.transactions.length} line{group.transactions.length === 1 ? '' : 's'}
                  </span>
                  <span className="ml-auto text-sm tabular-nums">
                    {money(group.total)}
                    <span className="text-text-secondary"> · {money(group.monthly)}/mo</span>
                  </span>
                  <span className="text-xs text-text-secondary w-4 text-right">
                    {expanded ? '−' : '+'}
                  </span>
                </button>

                {expanded && (
                  <div className="border-t border-border">
                    {/* The sentence a witness reads out instead of guessing. */}
                    <p className="px-3 py-2 text-xs text-text-secondary bg-off-white/60">
                      {group.derivation}
                    </p>

                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="text-left text-xs uppercase tracking-wide
                                         text-text-secondary border-b border-border">
                            <th className="py-1.5 px-3 w-8">
                              <input type="checkbox" checked={allPicked}
                                aria-label={`Select all in ${group.label}`}
                                onChange={e => setPicked(prev => {
                                  const next = new Set(prev)
                                  ids.forEach(i => e.target.checked ? next.add(i) : next.delete(i))
                                  return next
                                })} />
                            </th>
                            <th className="py-1.5 pr-3 font-medium">Date</th>
                            <th className="py-1.5 pr-3 font-medium">Account</th>
                            <th className="py-1.5 pr-3 font-medium">Description</th>
                            <th className="py-1.5 pr-3 font-medium">Bates</th>
                            <th className="py-1.5 pr-3 font-medium">Filed by</th>
                            <th className="py-1.5 pr-3 font-medium text-right">Amount</th>
                          </tr>
                        </thead>
                        <tbody>
                          {group.transactions.map(line => (
                            <tr key={line.id}
                              className={`border-b border-border last:border-0 ${
                                isPending(line) ? 'bg-amber-50/50' : ''}`}>
                              <td className="py-1.5 px-3">
                                <input type="checkbox" checked={picked.has(line.id)}
                                  aria-label={`Select transaction ${line.id}`}
                                  onChange={e => setPicked(prev => {
                                    const next = new Set(prev)
                                    if (e.target.checked) next.add(line.id)
                                    else next.delete(line.id)
                                    return next
                                  })} />
                              </td>
                              <td className="py-1.5 pr-3 whitespace-nowrap text-text-secondary">
                                {formatDate(line.date)}
                              </td>
                              <td className="py-1.5 pr-3 whitespace-nowrap text-text-secondary">
                                {line.account}
                              </td>
                              <td className="py-1.5 pr-3">
                                {line.description}
                                {line.check_number && (
                                  <span className="ml-1.5 text-xs text-text-secondary font-mono">
                                    ck {line.check_number}
                                  </span>
                                )}
                              </td>
                              <td className="py-1.5 pr-3 whitespace-nowrap font-mono text-xs
                                             text-text-secondary"
                                title={line.document ?? undefined}>
                                {line.bates_number ?? '—'}
                                {line.page ? ` p.${line.page}` : ''}
                              </td>
                              <td className="py-1.5 pr-3 text-xs whitespace-nowrap">
                                <FiledBy line={line} />
                              </td>
                              <td className="py-1.5 pr-3 text-right tabular-nums whitespace-nowrap">
                                {money(line.amount)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {schedule && schedule.groups.length > 0 && (
        <div className="card p-4">
          <ExportButtons
            name={name}
            onNameChange={setName}
            count={schedule.groups.reduce((n, g) => n + g.transactions.length, 0)}
            hint="Every figure ties to the statement for the same period and accounts"
            onExport={format => exportFisSchedule(
              matterId, request, format,
              name.trim() || 'Schedule of Transactions by Category',
            )}
          />
        </div>
      )}
    </div>
  )
}

/**
 * Who filed this line.
 *
 * Not a quality judgment — provenance. A rule assignment is not worse than a
 * paralegal's, it is answerable differently, and a reviewer needs to know which
 * answer they are relying on before they sign the statement it feeds.
 */
function FiledBy({ line }: { line: FisScheduleTransaction }) {
  if (line.category_source === 'human') {
    return <span className="text-text-secondary">a person</span>
  }
  if (line.category_source === 'rule' || line.category_source === 'similarity') {
    return (
      <span className={line.reviewed ? 'text-success' : 'text-amber-800'}>
        {line.category_source === 'rule' ? 'rule' : 'similarity'}
        {line.category_rule_id ? ` #${line.category_rule_id}` : ''}
        {line.reviewed ? ' · checked' : ''}
      </span>
    )
  }
  return <span className="text-text-secondary italic">not filed</span>
}
