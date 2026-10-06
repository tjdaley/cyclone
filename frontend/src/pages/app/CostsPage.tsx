import { FormEvent, useCallback, useEffect, useState } from 'react'
import {
  correctServiceRate, getCostReport, getServiceRates, getUnpricedMeters, setServiceRate,
} from '../../lib/api'
import { cost, formatDate, unitPrice } from '../../lib/money'
import type {
  CostGroup, CostReport, RateMeter, ServiceRate, SetServiceRatePayload, UnpricedMeter,
} from '../../types'

/**
 * Admin > Costs: what running Cyclone spends on models, and the prices behind it.
 *
 * Three rules shape the page, and all three are about not letting a figure look
 * more complete than it is:
 *
 * - **An unpriced call is not free.** A total with unpriced calls in it is a
 *   floor, and the page says so above the number rather than beneath the table.
 * - **A fraction of a cent is not $0.00.** `cost()` shows "<$0.01".
 * - **Dates are UTC calendar days.** A price starting at UTC midnight on
 *   1 October displayed in Texas local time would read 30 September.
 */

const GROUPS: { value: CostGroup; label: string; column: string }[] = [
  // One firm today, so this reads as a single row -- and it is the grouping
  // that says what every other one means once there is a second. See 043.
  { value: 'firm',         label: 'Firm',             column: 'Firm' },
  { value: 'vendor_model', label: 'Vendor and model', column: 'Vendor / model' },
  { value: 'profile',      label: 'Task',             column: 'Task profile' },
  { value: 'matter',       label: 'Matter',           column: 'Matter' },
  { value: 'staff',        label: 'Staff member',     column: 'Staff member' },
  { value: 'source',       label: 'Where it started', column: 'Source' },
]

const METERS: { value: RateMeter; label: string }[] = [
  { value: 'input_uncached',    label: 'Input' },
  { value: 'input_cache_read',  label: 'Cached input' },
  { value: 'input_cache_write', label: 'Cache write' },
  { value: 'output',            label: 'Output' },
]

const METER_LABEL: Record<RateMeter, string> = {
  input_uncached: 'Input', input_cache_read: 'Cached input',
  input_cache_write: 'Cache write', output: 'Output',
}

// Same shape as MatterFinancialsPage: a disabled text link must LOOK disabled.
const linkBusy = 'disabled:opacity-40 disabled:no-underline disabled:cursor-not-allowed'
const TH = 'px-4 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide'

/** "2026-09" for a Date, in UTC. */
function monthOf(date: Date): string {
  return date.toISOString().slice(0, 7)
}

/** The UTC calendar day of an ISO timestamp, formatted. See the note above. */
function utcDay(iso: string | null): string {
  return iso ? formatDate(iso.slice(0, 10)) : '—'
}

function monthLabel(isoDate: string): string {
  const [year, month] = isoDate.split('-').map(Number)
  return new Date(Date.UTC(year, month - 1, 1)).toLocaleDateString(undefined, {
    month: 'short', year: 'numeric', timeZone: 'UTC',
  })
}

function tokens(n: number | null): string {
  return n === null ? '—' : n.toLocaleString()
}

const EMPTY_FORM: SetServiceRatePayload = {
  provider: '', model: '', meter: 'input_uncached', price: '', effective_from: '',
  source_url: '', note: '',
}

export default function CostsPage() {
  const now = new Date()
  const sixMonthsAgo = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 5, 1))
  const [fromMonth, setFromMonth] = useState(monthOf(sixMonthsAgo))
  const [toMonth, setToMonth] = useState(monthOf(now))
  const [group, setGroup] = useState<CostGroup>('vendor_model')

  const [report, setReport] = useState<CostReport | null>(null)
  const [rates, setRates] = useState<ServiceRate[]>([])
  const [unpriced, setUnpriced] = useState<UnpricedMeter[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [form, setForm] = useState<SetServiceRatePayload | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const [editing, setEditing] = useState<{ id: number; price: string; source_url: string; note: string } | null>(null)
  const [editError, setEditError] = useState<string | null>(null)

  const loadReport = useCallback(async () => {
    try {
      setReport(await getCostReport(`${fromMonth}-01`, `${toMonth}-01`, group))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the cost report')
    }
  }, [fromMonth, toMonth, group])

  const loadPrices = useCallback(async () => {
    const [r, u] = await Promise.all([getServiceRates(), getUnpricedMeters()])
    setRates(r)
    setUnpriced(u)
  }, [])

  useEffect(() => {
    setLoading(true)
    Promise.all([loadReport(), loadPrices().catch(e => setError(e.message))])
      .finally(() => setLoading(false))
  }, [loadReport, loadPrices])

  const groupColumn = GROUPS.find(g => g.value === group)?.column ?? ''

  function openForm(prefill?: Partial<SetServiceRatePayload>) {
    setForm({ ...EMPTY_FORM, ...prefill })
    setFormError(null)
    window.scrollTo({ top: document.getElementById('prices')?.offsetTop ?? 0, behavior: 'smooth' })
  }

  async function submitPrice(e: FormEvent) {
    e.preventDefault()
    if (!form) return
    setSaving(true)
    setFormError(null)
    try {
      await setServiceRate({
        ...form,
        // Explicitly UTC midnight: the ledger, the report and every vendor
        // invoice are UTC, and a bare date would be read in the server's zone.
        effective_from: `${form.effective_from}T00:00:00Z`,
        source_url: form.source_url || null,
        note: form.note || null,
      })
      setForm(null)
      // A new price changes the report and the to-do list, not only the table.
      await Promise.all([loadPrices(), loadReport()])
    } catch (err) {
      // A 409 arrives as the database's own sentence: which price already
      // starts later, and what to do instead.
      setFormError(err instanceof Error ? err.message : 'Could not set the price')
    } finally {
      setSaving(false)
    }
  }

  async function saveCorrection(rate: ServiceRate) {
    if (!editing) return
    // PATCH semantics: only what actually changed.
    const payload: { price?: string; source_url?: string | null; note?: string | null } = {}
    if (editing.price !== unitPriceRaw(rate.price)) payload.price = editing.price
    if (editing.source_url !== (rate.source_url ?? '')) payload.source_url = editing.source_url || null
    if (editing.note !== (rate.note ?? '')) payload.note = editing.note || null
    if (Object.keys(payload).length === 0) { setEditing(null); return }
    setSaving(true)
    setEditError(null)
    try {
      await correctServiceRate(rate.id, payload)
      setEditing(null)
      await Promise.all([loadPrices(), loadReport()])
    } catch (err) {
      setEditError(err instanceof Error ? err.message : 'Could not save the correction')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="px-6 py-8 max-w-6xl mx-auto">
      <div className="mb-8">
        <h1 className="font-display text-3xl text-navy">Costs</h1>
        <p className="text-text-secondary mt-1">
          What the models cost, priced from the rates in force on the day each call was made.
          Months are UTC, to match vendor invoices.
        </p>
      </div>

      {error && (
        <div className="mb-6 rounded border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">{error}</div>
      )}

      {/* ── Spend ─────────────────────────────────────────────────────────── */}
      <section className="card mb-8">
        <div className="px-5 py-4 border-b border-border flex flex-wrap items-end gap-4">
          <h2 className="font-semibold text-navy mr-auto">Spend</h2>
          <div>
            <label className="label" htmlFor="cost-from">From</label>
            <input id="cost-from" type="month" className="input" value={fromMonth}
              onChange={e => setFromMonth(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="cost-to">Through</label>
            <input id="cost-to" type="month" className="input" value={toMonth}
              onChange={e => setToMonth(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="cost-group">Broken down by</label>
            <select id="cost-group" className="input" value={group}
              onChange={e => setGroup(e.target.value as CostGroup)}>
              {GROUPS.map(g => <option key={g.value} value={g.value}>{g.label}</option>)}
            </select>
          </div>
        </div>

        {report && (
          <div className="px-5 py-4 border-b border-border">
            <p className="text-xs text-text-secondary uppercase tracking-widest font-semibold mb-1">
              {report.complete ? 'Total' : 'Priced so far'}
            </p>
            <p className="font-display text-3xl text-navy tabular-nums">
              {cost(report.total_priced_cost, report.total_priced_cost_precise)}
              <span className="ml-3 text-base font-sans text-text-secondary">
                {report.total_calls.toLocaleString()} call{report.total_calls === 1 ? '' : 's'}
              </span>
            </p>
            {!report.complete && (
              // ABOVE the table, beside the number it qualifies. A caveat under
              // a long table is a caveat nobody reads.
              <p className="mt-3 rounded border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-text-primary">
                <strong>{report.total_unpriced_calls.toLocaleString()} call
                {report.total_unpriced_calls === 1 ? ' has' : 's have'} no price</strong>, so this is a
                floor, not a total. {unpriced.length > 0 && 'The missing prices are listed below.'}
              </p>
            )}
            {report.total_no_usage_calls > 0 && (
              <p className="mt-2 text-sm text-text-secondary">
                {report.total_no_usage_calls.toLocaleString()} call
                {report.total_no_usage_calls === 1 ? '' : 's'} reported no usage — almost always a
                failure before the vendor answered — and {report.total_no_usage_calls === 1 ? 'is' : 'are'} not
                counted in the total.
              </p>
            )}
          </div>
        )}

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                <th className={`${TH} text-left`}>Month</th>
                <th className={`${TH} text-left`}>{groupColumn}</th>
                <th className={`${TH} text-right`}>Calls</th>
                <th className={`${TH} text-right`}>Input</th>
                <th className={`${TH} text-right hidden md:table-cell`}>Cached</th>
                <th className={`${TH} text-right`}>Output</th>
                <th className={`${TH} text-right`}>Cost</th>
              </tr>
            </thead>
            <tbody>
              {loading && !report && (
                <tr><td colSpan={7} className="px-4 py-6 text-center text-text-secondary">Loading…</td></tr>
              )}
              {report && report.rows.length === 0 && (
                <tr><td colSpan={7} className="px-4 py-6 text-center text-text-secondary">
                  No model calls recorded in this range.
                </td></tr>
              )}
              {report?.rows.map(row => (
                <tr key={`${row.month}|${row.group_key}|${row.group_label}`} className="border-b border-border last:border-0">
                  <td className="px-4 py-3 whitespace-nowrap">{monthLabel(row.month)}</td>
                  <td className="px-4 py-3">{row.group_label}</td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {row.calls.toLocaleString()}
                    {row.failed_calls > 0 && (
                      <span className="ml-2 text-xs text-text-secondary" title="Attempts that failed over or errored — still billed when the vendor responded">
                        {row.failed_calls} failed
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">{tokens(row.input_uncached_tokens)}</td>
                  <td className="px-4 py-3 text-right tabular-nums hidden md:table-cell">{tokens(row.input_cache_read_tokens)}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{tokens(row.output_tokens)}</td>
                  <td className="px-4 py-3 text-right tabular-nums whitespace-nowrap">
                    {row.unpriced_calls === row.calls && row.calls > 0
                      ? <span className="text-warning font-medium">Unpriced</span>
                      : cost(row.priced_cost, row.priced_cost_precise)}
                    {row.unpriced_calls > 0 && row.unpriced_calls < row.calls && (
                      <span className="block text-xs text-warning">{row.unpriced_calls} unpriced</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* ── Missing prices ────────────────────────────────────────────────── */}
      {unpriced.length > 0 && (
        <section className="card mb-8 border-warning/40">
          <div className="px-5 py-4 border-b border-border">
            <h2 className="font-semibold text-navy">Missing prices</h2>
            <p className="text-sm text-text-secondary mt-1">
              These were used with no price in force. Enter each from the vendor's pricing page.
              Setting a price starts it on the first day it was used, so every affected call is covered.
              The model shown is the one the vendor says answered, which is what is billed.
            </p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border">
                  <th className={`${TH} text-left`}>Vendor / model</th>
                  <th className={`${TH} text-left`}>Meter</th>
                  <th className={`${TH} text-right`}>Calls</th>
                  <th className={`${TH} text-right`}>Tokens</th>
                  <th className={`${TH} text-left`}>First used</th>
                  <th className={TH} />
                </tr>
              </thead>
              <tbody>
                {unpriced.map(u => (
                  <tr key={`${u.vendor}|${u.model}|${u.meter}`} className="border-b border-border last:border-0">
                    <td className="px-4 py-3">
                      <span className="font-mono text-xs">{u.vendor} / {u.model}</span>
                      {u.requested_models.some(m => m !== u.model) && (
                        <span className="block text-xs text-text-secondary">
                          requested as <span className="font-mono">
                            {u.requested_models.filter(m => m !== u.model).join(', ')}
                          </span>
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3">{METER_LABEL[u.meter]}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{u.calls.toLocaleString()}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{u.tokens.toLocaleString()}</td>
                    <td className="px-4 py-3">{utcDay(u.first_seen)}</td>
                    <td className="px-4 py-3 text-right">
                      <button type="button" className="text-xs text-navy underline"
                        onClick={() => openForm({
                          provider: u.vendor, model: u.model, meter: u.meter,
                          effective_from: u.first_seen.slice(0, 10),
                        })}>
                        Set price
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* ── Prices ────────────────────────────────────────────────────────── */}
      <section id="prices" className="card">
        <div className="px-5 py-4 border-b border-border flex items-center justify-between gap-4">
          <div>
            <h2 className="font-semibold text-navy">Prices</h2>
            <p className="text-sm text-text-secondary mt-1">
              A new price closes the one before it. Prices are per million tokens, in USD.
            </p>
          </div>
          <button type="button" className="btn-primary" onClick={() => (form ? setForm(null) : openForm())}>
            {form ? 'Cancel' : 'New price'}
          </button>
        </div>

        {form && (
          <form onSubmit={submitPrice} className="px-5 py-4 border-b border-border bg-off-white/60">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div>
                <label className="label" htmlFor="rate-provider">Vendor</label>
                <input id="rate-provider" className="input font-mono" required value={form.provider}
                  placeholder="anthropic" onChange={e => setForm({ ...form, provider: e.target.value })} />
              </div>
              <div>
                <label className="label" htmlFor="rate-model">Model</label>
                <input id="rate-model" className="input font-mono" required value={form.model}
                  placeholder="exactly as the cost report shows it"
                  onChange={e => setForm({ ...form, model: e.target.value })} />
              </div>
              <div>
                <label className="label" htmlFor="rate-meter">Meter</label>
                <select id="rate-meter" className="input" value={form.meter}
                  onChange={e => setForm({ ...form, meter: e.target.value as RateMeter })}>
                  {METERS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
                </select>
              </div>
              <div>
                <label className="label" htmlFor="rate-price">Price per 1M tokens (USD)</label>
                <input id="rate-price" className="input tabular-nums" required inputMode="decimal"
                  pattern="\d+(\.\d{1,6})?" title="Up to six decimal places"
                  value={form.price} onChange={e => setForm({ ...form, price: e.target.value })} />
              </div>
              <div>
                <label className="label" htmlFor="rate-from">In force from (UTC)</label>
                <input id="rate-from" type="date" className="input" required value={form.effective_from}
                  onChange={e => setForm({ ...form, effective_from: e.target.value })} />
              </div>
              <div>
                <label className="label" htmlFor="rate-source">Source</label>
                <input id="rate-source" type="url" className="input" value={form.source_url ?? ''}
                  placeholder="the vendor's pricing page"
                  onChange={e => setForm({ ...form, source_url: e.target.value })} />
              </div>
              <div className="md:col-span-3">
                <label className="label" htmlFor="rate-note">Note</label>
                <input id="rate-note" className="input" value={form.note ?? ''}
                  onChange={e => setForm({ ...form, note: e.target.value })} />
              </div>
            </div>
            {formError && <p className="mt-3 text-sm text-danger">{formError}</p>}
            <div className="mt-4 flex gap-3">
              <button type="submit" className="btn-primary" disabled={saving}>
                {saving ? 'Saving…' : 'Set price'}
              </button>
              <button type="button" className="btn-secondary" onClick={() => setForm(null)}>Cancel</button>
            </div>
          </form>
        )}

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                <th className={`${TH} text-left`}>Vendor / model</th>
                <th className={`${TH} text-left`}>Meter</th>
                <th className={`${TH} text-right`}>Price / 1M</th>
                <th className={`${TH} text-left`}>From</th>
                <th className={`${TH} text-left`}>Until</th>
                <th className={`${TH} text-left hidden lg:table-cell`}>Source</th>
                <th className={TH} />
              </tr>
            </thead>
            <tbody>
              {rates.length === 0 && !loading && (
                <tr><td colSpan={7} className="px-4 py-6 text-center text-text-secondary">
                  No prices entered yet. Every call is unpriced until they are.
                </td></tr>
              )}
              {rates.map(rate => {
                const scheduled = !rate.in_force && rate.effective_to === null
                const ended = !rate.in_force && rate.effective_to !== null
                const isEditing = editing?.id === rate.id
                return (
                  <tr key={rate.id} className={`border-b border-border last:border-0 ${ended ? 'text-text-secondary' : ''}`}>
                    <td className="px-4 py-3">
                      <span className="font-mono text-xs">{rate.provider} / {rate.model}</span>
                      {rate.in_force && <span className="ml-2 rounded-full bg-success/10 px-2 py-0.5 text-xs text-success">In force</span>}
                      {scheduled && <span className="ml-2 rounded-full bg-navy/10 px-2 py-0.5 text-xs text-navy">Scheduled</span>}
                    </td>
                    <td className="px-4 py-3">{METER_LABEL[rate.meter]}</td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      {isEditing ? (
                        <input className="input w-28 text-right tabular-nums" inputMode="decimal"
                          aria-label="Corrected price" value={editing.price}
                          onChange={e => setEditing({ ...editing, price: e.target.value })} />
                      ) : unitPrice(rate.price)}
                      {rate.unit_count !== 1_000_000 && (
                        <span className="block text-xs text-text-secondary">per {rate.unit_count.toLocaleString()}</span>
                      )}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap">{utcDay(rate.effective_from)}</td>
                    <td className="px-4 py-3 whitespace-nowrap">{rate.effective_to ? utcDay(rate.effective_to) : '—'}</td>
                    <td className="px-4 py-3 hidden lg:table-cell max-w-xs">
                      {isEditing ? (
                        <div className="space-y-2">
                          <input className="input" aria-label="Corrected source" value={editing.source_url}
                            onChange={e => setEditing({ ...editing, source_url: e.target.value })} />
                          <input className="input" aria-label="Corrected note" placeholder="note"
                            value={editing.note} onChange={e => setEditing({ ...editing, note: e.target.value })} />
                        </div>
                      ) : (
                        <>
                          {rate.source_url
                            ? <a href={rate.source_url} target="_blank" rel="noreferrer" className="text-navy underline break-all">source</a>
                            : <span className="text-text-secondary">—</span>}
                          {rate.note && <span className="block text-xs text-text-secondary">{rate.note}</span>}
                        </>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right whitespace-nowrap">
                      {isEditing ? (
                        <>
                          <button type="button" className={`text-xs text-navy underline mr-3 ${linkBusy}`}
                            disabled={saving} onClick={() => saveCorrection(rate)}>
                            {saving ? 'Saving…' : 'Save'}
                          </button>
                          <button type="button" className="text-xs text-text-secondary underline"
                            onClick={() => { setEditing(null); setEditError(null) }}>Cancel</button>
                          {editError && <span className="block text-xs text-danger mt-1">{editError}</span>}
                        </>
                      ) : (
                        <button type="button" className={`text-xs text-navy underline ${linkBusy}`}
                          disabled={saving}
                          title="Fix a typo in the price, source or note. To change the price from a date, use New price."
                          onClick={() => {
                            setEditError(null)
                            setEditing({
                              id: rate.id, price: unitPriceRaw(rate.price),
                              source_url: rate.source_url ?? '', note: rate.note ?? '',
                            })
                          }}>
                          Correct
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}

/** A stored price as it should appear in an input: trailing zeros trimmed. */
function unitPriceRaw(price: string): string {
  if (!price.includes('.')) return price
  return price.replace(/0+$/, '').replace(/\.$/, '')
}
