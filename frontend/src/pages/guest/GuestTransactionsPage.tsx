import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  createGuestTag, deleteGuestTag, exportGuestTransactions, getGuestAccounts, getGuestCategories,
  getGuestTags, openGuestStatementPdf, searchGuestTransactions, tagGuestTransactions,
  updateGuestTag,
} from '../../lib/api'
import { formatDate, isNegative, money } from '../../lib/money'
import ExportButtons from '../../components/ExportButtons'
import StatementPdfButton from '../../components/StatementPdfButton'
import type {
  GuestAccount, GuestCategory, GuestTag, GuestTransaction, TransactionSearchFilter,
} from '../../types'

const PAGE_SIZE = 200

function emptyFilter(): TransactionSearchFilter {
  return {
    account_ids: null, date_from: null, date_to: null,
    category_ids: null, include_subcategories: true, uncategorized: false,
    tag_ids: null, tag_match_all: false, untagged: false,
    text: null, check_number: null, checks_only: false,
    amount_min: null, amount_max: null,
    include_deleted: false, limit: PAGE_SIZE, offset: 0,
  }
}

/** A whole figure, optionally signed — anything else stays out of the request. */
const FIGURE = /^[+-]?\d+(\.\d{1,2})?$/

/**
 * Search the matter's transactions, tag them, export them.
 *
 * Categories are shown and can be filtered on, never set. Tags are the guest's
 * own and nobody else's: the server narrows every filter and every chip to
 * them, and this screen offers no "untagged" filter because "no tag at all"
 * would reveal what the firm had tagged.
 */
export default function GuestTransactionsPage() {
  const matterId = Number(useParams().matterId)
  const [filter, setFilter] = useState<TransactionSearchFilter>(emptyFilter)
  const [textDraft, setTextDraft] = useState('')
  const [minDraft, setMinDraft] = useState('')
  const [maxDraft, setMaxDraft] = useState('')
  const [rows, setRows] = useState<GuestTransaction[]>([])
  const [total, setTotal] = useState(0)
  const [accounts, setAccounts] = useState<GuestAccount[]>([])
  const [categories, setCategories] = useState<GuestCategory[]>([])
  const [tags, setTags] = useState<GuestTag[]>([])
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [newTag, setNewTag] = useState('')
  /** Said when "Add" is pressed with no name, rather than doing nothing visible. */
  const [tagHint, setTagHint] = useState<string | null>(null)
  const [exhibitName, setExhibitName] = useState('Financial Summary')

  const categoryById = useMemo(() => new Map(categories.map(c => [c.id, c])), [categories])
  const tagById = useMemo(() => new Map(tags.map(t => [t.id, t])), [tags])
  const activeTags = tags.filter(t => t.is_active)

  useEffect(() => {
    Promise.all([getGuestAccounts(matterId), getGuestCategories(), getGuestTags(matterId)])
      .then(([a, c, t]) => { setAccounts(a); setCategories(c); setTags(t) })
      .catch(e => setError(e instanceof Error ? e.message : 'Could not load the matter'))
  }, [matterId])

  const run = useCallback(async (next: TransactionSearchFilter) => {
    setBusy(true); setError(null)
    try {
      const result = await searchGuestTransactions(matterId, next)
      setRows(result.items); setTotal(result.total); setSelected(new Set())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Search failed')
    } finally { setBusy(false) }
  }, [matterId])

  useEffect(() => { run(filter) }, [filter, run])

  function patch(changes: Partial<TransactionSearchFilter>) {
    setFilter(prev => ({ ...prev, ...changes, offset: 0 }))
  }

  function applyDrafts() {
    patch({
      text: textDraft.trim() || null,
      amount_min: FIGURE.test(minDraft.trim()) ? minDraft.trim() : null,
      amount_max: FIGURE.test(maxDraft.trim()) ? maxDraft.trim() : null,
    })
  }

  async function refreshTags() { setTags(await getGuestTags(matterId)) }

  async function addTag() {
    if (!newTag.trim()) {
      setTagHint('Type a name for the new tag in the box first, then press Add.')
      return
    }
    setTagHint(null)
    try { await createGuestTag(matterId, { label: newTag.trim() }); setNewTag(''); await refreshTags() }
    catch (e) { setTagHint(e instanceof Error ? e.message : 'Could not create the tag') }
  }

  async function apply(tagId: number, remove: boolean) {
    if (selected.size === 0) return
    setBusy(true)
    try { await tagGuestTransactions(matterId, [...selected], tagId, remove); await run(filter) }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not tag the lines') }
    finally { setBusy(false) }
  }

  const allOnPage = rows.length > 0 && rows.every(r => selected.has(r.id))
  const mixed = new Set(rows.map(r => r.currency)).size > 1
  const from = total === 0 ? 0 : (filter.offset ?? 0) + 1
  const to = Math.min((filter.offset ?? 0) + PAGE_SIZE, total)

  return (
    <div className="space-y-4">
      {error && <p className="card p-4 text-sm text-red-700">{error}</p>}

      <div className="card p-4 space-y-3">
        <div className="flex flex-wrap gap-2 items-end">
          <input className="input py-1 text-sm flex-1 min-w-48" placeholder="Description contains…"
            value={textDraft} onChange={e => setTextDraft(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') applyDrafts() }} />
          <input className="input py-1 text-sm w-28" placeholder="Amount from" value={minDraft}
            onChange={e => setMinDraft(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') applyDrafts() }} />
          <input className="input py-1 text-sm w-28" placeholder="to" value={maxDraft}
            onChange={e => setMaxDraft(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') applyDrafts() }} />
          <button type="button" className="btn-secondary py-1 text-sm" onClick={applyDrafts}>Search</button>
        </div>
        <div className="flex flex-wrap gap-2 items-center text-sm">
          <select className="input py-1 text-sm max-w-xs" value={filter.account_ids?.[0] ?? ''}
            onChange={e => patch({ account_ids: e.target.value ? [Number(e.target.value)] : null })}>
            <option value="">All accounts</option>
            {accounts.map(a => (
              <option key={a.id} value={a.id}>
                {a.institution}{a.account_number_last4 ? ` ····${a.account_number_last4}` : ''}
              </option>
            ))}
          </select>
          <select className="input py-1 text-sm max-w-xs" value={filter.category_ids?.[0] ?? ''}
            onChange={e => patch({ category_ids: e.target.value ? [Number(e.target.value)] : null })}>
            <option value="">All categories</option>
            {categories.map(c => <option key={c.id} value={c.id}>{c.description}</option>)}
          </select>
          <input type="date" className="input py-1 text-sm" value={filter.date_from ?? ''}
            onChange={e => patch({ date_from: e.target.value || null })} />
          <span className="text-text-secondary">to</span>
          <input type="date" className="input py-1 text-sm" value={filter.date_to ?? ''}
            onChange={e => patch({ date_to: e.target.value || null })} />
          <button type="button" className="text-xs text-navy underline"
            onClick={() => { setTextDraft(''); setMinDraft(''); setMaxDraft(''); setFilter(emptyFilter()) }}>
            Clear
          </button>
        </div>

        <div className="flex flex-wrap gap-1.5 items-center">
          <span className="text-xs text-text-secondary mr-1">Your tags:</span>
          {tags.length === 0 && <span className="text-xs text-text-secondary">none yet</span>}
          {tags.map(t => {
            const on = filter.tag_ids?.includes(t.id) ?? false
            return (
              <span key={t.id} className={`inline-flex items-center gap-1 text-xs rounded-full px-2.5 py-1 border ${
                on ? 'bg-navy text-white border-navy' : 'bg-off-white border-border'} ${
                t.is_active ? '' : 'opacity-50'}`}>
                <button type="button" onClick={() => {
                  const next = on ? (filter.tag_ids ?? []).filter(i => i !== t.id)
                                  : [...(filter.tag_ids ?? []), t.id]
                  patch({ tag_ids: next.length ? next : null })
                }}>
                  {t.label}
                </button>
                <button type="button" className="opacity-60 hover:opacity-100"
                  title={t.is_active ? 'Retire this tag (it stays on the lines it is on)' : 'Bring it back'}
                  onClick={async () => { await updateGuestTag(t.id, { is_active: !t.is_active }); await refreshTags() }}>
                  {t.is_active ? '–' : '+'}
                </button>
                <button type="button" className="opacity-60 hover:opacity-100" title="Delete this tag everywhere"
                  onClick={async () => {
                    if (!window.confirm(`Delete "${t.label}"? It comes off every line it is on.`)) return
                    await deleteGuestTag(t.id); await refreshTags(); await run(filter)
                  }}>×</button>
              </span>
            )
          })}
          <input className="input py-0.5 text-xs w-48 ml-2" placeholder="Name a new tag…"
            aria-label="New tag name" value={newTag}
            onChange={e => { setNewTag(e.target.value); setTagHint(null) }}
            onKeyDown={e => { if (e.key === 'Enter') addTag() }} />
          <button type="button" className="btn-secondary py-0.5 px-2 text-xs" onClick={addTag}>
            Add tag
          </button>
          {tagHint && <span className="basis-full text-xs text-amber-700">{tagHint}</span>}
        </div>
      </div>

      <div className="card p-4">
        <div className="flex flex-wrap items-center gap-3 mb-3 text-sm">
          <span>
            {busy ? 'Searching…' : <><span className="font-medium tabular-nums">{total}</span> matching lines</>}
            {total > PAGE_SIZE && <span className="text-text-secondary"> · showing {from}–{to}</span>}
          </span>
          {mixed && <span className="text-xs text-text-secondary">mixed currencies</span>}
          {selected.size > 0 && (
            <>
              <span className="text-text-secondary">{selected.size} selected</span>
              <select className="input py-1 text-sm" value="" disabled={busy || activeTags.length === 0}
                onChange={e => { if (e.target.value) apply(Number(e.target.value), false) }}>
                <option value="">Add tag…</option>
                {activeTags.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
              </select>
              <select className="input py-1 text-sm" value="" disabled={busy || tags.length === 0}
                onChange={e => { if (e.target.value) apply(Number(e.target.value), true) }}>
                <option value="">Remove tag…</option>
                {tags.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
              </select>
            </>
          )}
          <span className="ml-auto flex gap-2">
            <button type="button" className="text-xs text-navy underline disabled:opacity-40"
              disabled={(filter.offset ?? 0) === 0}
              onClick={() => setFilter(f => ({ ...f, offset: Math.max(0, (f.offset ?? 0) - PAGE_SIZE) }))}>
              Previous
            </button>
            <button type="button" className="text-xs text-navy underline disabled:opacity-40"
              disabled={to >= total}
              onClick={() => setFilter(f => ({ ...f, offset: (f.offset ?? 0) + PAGE_SIZE }))}>
              Next
            </button>
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-text-secondary border-b border-border">
                <th className="py-2 pr-2 w-6">
                  <input type="checkbox" checked={allOnPage} aria-label="Select every line on this page"
                    onChange={() => setSelected(allOnPage ? new Set() : new Set(rows.map(r => r.id)))} />
                </th>
                <th className="py-2 pr-3 font-medium">Date</th>
                <th className="py-2 pr-3 font-medium">Bates</th>
                <th className="py-2 pr-3 font-medium">Description</th>
                <th className="py-2 pr-3 font-medium">Account</th>
                <th className="py-2 pr-3 font-medium">Category</th>
                <th className="py-2 font-medium text-right">Amount</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(row => (
                <tr key={row.id} className="border-b border-border/60 align-top">
                  <td className="py-2 pr-2">
                    <input type="checkbox" checked={selected.has(row.id)} aria-label="Select this line"
                      onChange={() => setSelected(prev => {
                        const next = new Set(prev)
                        if (next.has(row.id)) next.delete(row.id); else next.add(row.id)
                        return next
                      })} />
                  </td>
                  <td className="py-2 pr-3 whitespace-nowrap tabular-nums">{formatDate(row.transaction_date)}</td>
                  <td className="py-2 pr-3 text-xs font-mono text-text-secondary whitespace-nowrap">
                    <span className="inline-flex items-center gap-1">
                      {row.bates_number ?? '—'}
                      <StatementPdfButton statementId={row.statement_id} page={row.physical_page_number}
                        opener={openGuestStatementPdf} label="Show this line's statement" />
                    </span>
                  </td>
                  <td className="py-2 pr-3">
                    {row.description}
                    {row.tag_ids.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-1">
                        {row.tag_ids.map(id => tagById.get(id) && (
                          <span key={id} className="text-xs rounded-full px-2 py-0.5 border bg-off-white border-border">
                            {tagById.get(id)!.label}
                          </span>
                        ))}
                      </div>
                    )}
                  </td>
                  <td className="py-2 pr-3 text-xs text-text-secondary whitespace-nowrap">
                    {row.institution}
                    {row.account_last4 && <span className="block">····{row.account_last4}</span>}
                  </td>
                  <td className="py-2 pr-3 text-xs text-text-secondary">
                    {row.category_id !== null ? categoryById.get(row.category_id)?.description ?? '' : ''}
                  </td>
                  <td className={`py-2 text-right whitespace-nowrap tabular-nums ${
                    isNegative(row.amount) ? 'text-text-primary' : 'text-green-700'}`}>
                    {money(row.amount, row.currency)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <ExportButtons
        name={exhibitName}
        onNameChange={setExhibitName}
        count={total}
        hint="Every matching line, not just this page. CSV is the flat data; MD, DOCX and PDF are exhibits"
        onExport={format => exportGuestTransactions(
          matterId, { ...filter, offset: 0 }, format, exhibitName.trim() || 'Financial Summary')}
      />
    </div>
  )
}
