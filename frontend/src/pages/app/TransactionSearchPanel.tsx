import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  searchTransactions, getTransactionCategories, getTransactionTags,
  categorizeTransactions, tagTransactions, createMatterTag, deleteTransactionTag,
} from '../../lib/api'
import { money, isNegative, formatDate } from '../../lib/money'
import TransactionEditDialog, { CorrectedMark } from './TransactionEditDialog'
import type {
  FinancialAccount, TransactionCategory, TransactionTag,
  TransactionSearchFilter, TransactionSearchRow,
} from '../../types'

const PAGE_SIZE = 200

/**
 * Tag colours. Presentation only — the token is stored on the tag so the same
 * claim reads the same colour everywhere it appears.
 */
const TAG_COLOR: Record<string, string> = {
  red:    'bg-red-100 text-red-800 border-red-200',
  amber:  'bg-amber-100 text-amber-800 border-amber-200',
  blue:   'bg-blue-100 text-blue-800 border-blue-200',
  purple: 'bg-purple-100 text-purple-800 border-purple-200',
  green:  'bg-green-100 text-green-800 border-green-200',
  gray:   'bg-gray-100 text-gray-700 border-gray-200',
}
const TAG_COLORS = Object.keys(TAG_COLOR)

function tagClass(tag: TransactionTag): string {
  return TAG_COLOR[tag.color ?? 'gray'] ?? TAG_COLOR.gray
}

/** An empty filter — also what "Clear" resets to. */
function emptyFilter(): TransactionSearchFilter {
  return {
    account_ids: null, date_from: null, date_to: null,
    category_ids: null, include_subcategories: true, uncategorized: false,
    tag_ids: null, tag_match_all: false, untagged: false,
    text: null, check_number: null, checks_only: false,
    include_deleted: false, limit: PAGE_SIZE, offset: 0,
  }
}

function isFiltered(f: TransactionSearchFilter): boolean {
  return Boolean(
    f.account_ids?.length || f.date_from || f.date_to || f.category_ids?.length ||
    f.uncategorized || f.tag_ids?.length || f.untagged || f.text ||
    f.check_number || f.checks_only,
  )
}

export default function TransactionSearchPanel({ matterId, accounts }: {
  matterId: number
  accounts: FinancialAccount[]
}) {
  const [filter, setFilter] = useState<TransactionSearchFilter>(emptyFilter)
  const [rows, setRows] = useState<TransactionSearchRow[]>([])
  const [total, setTotal] = useState(0)
  const [pageSum, setPageSum] = useState('0.00')
  const [categories, setCategories] = useState<TransactionCategory[]>([])
  const [tags, setTags] = useState<TransactionTag[]>([])
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showTagManager, setShowTagManager] = useState(false)
  const [correcting, setCorrecting] = useState<TransactionSearchRow | null>(null)

  // Draft text, debounced into the filter. Typing straight into the filter
  // fires a search per keystroke against a table of statement lines.
  const [textDraft, setTextDraft] = useState('')

  const categoryById = useMemo(
    () => new Map(categories.map(c => [c.id, c])), [categories],
  )
  const tagById = useMemo(() => new Map(tags.map(t => [t.id, t])), [tags])

  const run = useCallback(async (next: TransactionSearchFilter) => {
    setBusy(true); setError(null)
    try {
      const result = await searchTransactions(matterId, next)
      setRows(result.items)
      setTotal(result.total)
      setPageSum(result.sum_amount)
      setSelected(new Set())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Search failed')
    } finally { setBusy(false) }
  }, [matterId])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const [cats, tagRows] = await Promise.all([
          getTransactionCategories(), getTransactionTags(matterId),
        ])
        if (cancelled) return
        setCategories(cats)
        setTags(tagRows)
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load categories and tags')
      }
    })()
    return () => { cancelled = true }
  }, [matterId])

  useEffect(() => { run(filter) }, [filter, run])

  useEffect(() => {
    const handle = setTimeout(() => {
      setFilter(f => (f.text ?? '') === textDraft ? f : { ...f, text: textDraft || null, offset: 0 })
    }, 300)
    return () => clearTimeout(handle)
  }, [textDraft])

  function patch(changes: Partial<TransactionSearchFilter>) {
    // Any change to the criteria invalidates the page you were on.
    setFilter(f => ({ ...f, ...changes, offset: 'offset' in changes ? changes.offset! : 0 }))
  }

  function toggleIn(list: number[] | null | undefined, id: number): number[] | null {
    const set = new Set(list ?? [])
    if (set.has(id)) set.delete(id); else set.add(id)
    return set.size ? [...set] : null
  }

  function toggleRow(id: number) {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  const allOnPageSelected = rows.length > 0 && rows.every(r => selected.has(r.id))

  async function refreshTags() {
    setTags(await getTransactionTags(matterId))
  }

  async function applyCategory(categoryId: number | null) {
    if (selected.size === 0) return
    setBusy(true); setError(null)
    try {
      await categorizeTransactions(matterId, [...selected], categoryId)
      await run(filter)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not categorize')
      setBusy(false)
    }
  }

  async function applyTag(tagId: number, remove: boolean) {
    if (selected.size === 0) return
    setBusy(true); setError(null)
    try {
      await tagTransactions(matterId, [...selected], tagId, remove)
      await Promise.all([run(filter), refreshTags()])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not tag')
      setBusy(false)
    }
  }

  const showing = rows.length
  const from = total === 0 ? 0 : (filter.offset ?? 0) + 1
  const to = (filter.offset ?? 0) + showing

  return (
    <div className="card p-5 space-y-4">
      {correcting && (
        <TransactionEditDialog
          transaction={correcting}
          onClose={() => setCorrecting(null)}
          onSaved={updated => {
            // Keep the search context the row was rendered with; the correction
            // endpoint returns the bare transaction, not the joined row.
            setRows(prev => prev.map(r => (r.id === updated.id
              ? { ...r, ...updated, tag_ids: r.tag_ids, institution: r.institution,
                  account_last4: r.account_last4 }
              : r)))
            setCorrecting(null)
          }} />
      )}
      <div className="flex items-baseline justify-between">
        <h2 className="font-semibold text-navy">Find transactions</h2>
        <button type="button" className="text-xs text-navy underline"
          onClick={() => setShowTagManager(v => !v)}>
          {showTagManager ? 'Hide tags' : 'Manage tags'}
        </button>
      </div>

      {showTagManager && (
        <TagManager matterId={matterId} tags={tags} onChanged={refreshTags}
          onError={setError} />
      )}

      {/* ── Filters ── */}
      <div className="grid gap-3 md:grid-cols-4">
        <div className="md:col-span-2">
          <label className="label" htmlFor="tx-text">Description contains</label>
          <input id="tx-text" className="input mt-1" value={textDraft} placeholder="pilot point, wedding, transfer…"
            onChange={e => setTextDraft(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="tx-check">Check #</label>
          <input id="tx-check" className="input mt-1 font-mono" placeholder="2495"
            value={filter.check_number ?? ''}
            onChange={e => patch({ check_number: e.target.value.trim() || null })} />
        </div>
        <div>
          <label className="label" htmlFor="tx-from">From</label>
          <input id="tx-from" type="date" className="input mt-1" value={filter.date_from ?? ''}
            onChange={e => patch({ date_from: e.target.value || null })} />
        </div>
        <div>
          <label className="label" htmlFor="tx-to">To</label>
          <input id="tx-to" type="date" className="input mt-1" value={filter.date_to ?? ''}
            onChange={e => patch({ date_to: e.target.value || null })} />
        </div>

        <div className="md:col-span-2">
          <label className="label" htmlFor="tx-category">Category</label>
          <select id="tx-category" className="input mt-1"
            value={filter.uncategorized ? 'none' : (filter.category_ids?.[0] ?? '')}
            onChange={e => {
              const v = e.target.value
              if (v === 'none') patch({ uncategorized: true, category_ids: null })
              else patch({ uncategorized: false, category_ids: v ? [Number(v)] : null })
            }}>
            <option value="">— any category —</option>
            <option value="none">Uncategorized</option>
            {categories.map(c => (
              <option key={c.id} value={c.id}>
                {'  '.repeat(c.depth)}{c.description}{c.include_in_fis ? '' : ' (not on FIS)'}
              </option>
            ))}
          </select>
          {filter.category_ids?.length ? (
            <label className="flex items-center gap-2 text-xs text-text-secondary mt-1.5">
              <input type="checkbox" checked={filter.include_subcategories !== false}
                onChange={e => patch({ include_subcategories: e.target.checked })} />
              Include subcategories
            </label>
          ) : null}
        </div>

        <div className="md:col-span-2">
          <span className="label">Accounts</span>
          <div className="flex flex-wrap gap-1.5 mt-1">
            {accounts.length === 0 && <span className="text-sm text-text-secondary">No accounts yet</span>}
            {accounts.map(a => {
              const on = filter.account_ids?.includes(a.id) ?? false
              return (
                <button key={a.id} type="button"
                  className={`text-xs rounded-full px-2.5 py-1 border transition-colors ${
                    on ? 'bg-navy text-white border-navy' : 'bg-off-white text-text-secondary border-border hover:border-navy/40'}`}
                  onClick={() => patch({ account_ids: toggleIn(filter.account_ids, a.id) })}>
                  {a.institution}{a.account_number_last4 ? ` ····${a.account_number_last4}` : ''}
                </button>
              )
            })}
          </div>
        </div>
      </div>

      {/* ── Tag filter ── */}
      <div>
        <div className="flex flex-wrap items-baseline gap-3">
          <span className="label">Tags</span>
          {(filter.tag_ids?.length ?? 0) > 1 && (
            <label className="flex items-center gap-1.5 text-xs text-text-secondary">
              <input type="checkbox" checked={filter.tag_match_all ?? false}
                onChange={e => patch({ tag_match_all: e.target.checked })} />
              Must carry all of them
            </label>
          )}
        </div>
        <div className="flex flex-wrap gap-1.5 mt-1">
          <button type="button"
            className={`text-xs rounded-full px-2.5 py-1 border transition-colors ${
              filter.untagged ? 'bg-navy text-white border-navy' : 'bg-off-white text-text-secondary border-border hover:border-navy/40'}`}
            onClick={() => patch({ untagged: !filter.untagged, tag_ids: null })}>
            Untagged
          </button>
          {/* Removed lines are hidden from every total; this is how they are
              found again to put back. */}
          <button type="button"
            className={`text-xs rounded-full px-2.5 py-1 border transition-colors ${
              filter.checks_only ? 'bg-navy text-white border-navy' : 'bg-off-white text-text-secondary border-border hover:border-navy/40'}`}
            onClick={() => patch({ checks_only: !filter.checks_only })}>
            Checks only
          </button>
          <button type="button"
            className={`text-xs rounded-full px-2.5 py-1 border transition-colors ${
              filter.include_deleted ? 'bg-red-700 text-white border-red-700' : 'bg-off-white text-text-secondary border-border hover:border-navy/40'}`}
            onClick={() => patch({ include_deleted: !filter.include_deleted })}>
            Show removed
          </button>
          {tags.map(t => {
            const on = filter.tag_ids?.includes(t.id) ?? false
            return (
              <button key={t.id} type="button"
                className={`text-xs rounded-full px-2.5 py-1 border transition-colors ${
                  on ? 'bg-navy text-white border-navy' : `${tagClass(t)} hover:opacity-80`}`}
                title={t.description ?? undefined}
                onClick={() => patch({ tag_ids: toggleIn(filter.tag_ids, t.id), untagged: false })}>
                {t.matter_id === null ? t.label : `★ ${t.label}`}
                {t.usage_count ? <span className="ml-1 opacity-70 tabular-nums">{t.usage_count}</span> : null}
              </button>
            )
          })}
        </div>
        <p className="text-xs text-text-secondary mt-1.5">★ marks a tag that exists only on this matter.</p>
      </div>

      {error && (
        <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-2 flex justify-between">
          <span>{error}</span>
          <button type="button" className="underline" onClick={() => setError(null)}>dismiss</button>
        </div>
      )}

      {/* ── Result header ── */}
      <div className="flex flex-wrap items-baseline gap-3 border-t border-border pt-3">
        <span className="text-sm text-text-primary">
          {busy ? 'Searching…' : (
            <>
              <span className="font-medium tabular-nums">{total}</span> matching line{total === 1 ? '' : 's'}
              {total > showing && <span className="text-text-secondary"> · showing {from}–{to}</span>}
            </>
          )}
        </span>
        <span className="text-sm text-text-secondary tabular-nums">
          page total {money(pageSum)}
        </span>
        {isFiltered(filter) && (
          <button type="button" className="text-xs text-navy underline"
            onClick={() => { setTextDraft(''); setFilter(emptyFilter()) }}>Clear filters</button>
        )}
        <span className="ml-auto flex items-center gap-2">
          <button type="button" className="text-xs text-navy underline disabled:text-text-secondary disabled:no-underline"
            disabled={(filter.offset ?? 0) === 0 || busy}
            onClick={() => patch({ offset: Math.max(0, (filter.offset ?? 0) - PAGE_SIZE) })}>← Previous</button>
          <button type="button" className="text-xs text-navy underline disabled:text-text-secondary disabled:no-underline"
            disabled={to >= total || busy}
            onClick={() => patch({ offset: (filter.offset ?? 0) + PAGE_SIZE })}>Next →</button>
        </span>
      </div>

      {/* ── Bulk actions ── */}
      {selected.size > 0 && (
        <div className="flex flex-wrap items-center gap-3 bg-navy/5 border border-navy/20 rounded p-3">
          <span className="text-sm font-medium text-navy tabular-nums">{selected.size} selected</span>

          <select className="input py-1 text-sm max-w-xs" value="" disabled={busy}
            onChange={e => { const v = e.target.value; if (v) applyCategory(v === 'none' ? null : Number(v)) }}>
            <option value="">File under…</option>
            <option value="none">— clear category —</option>
            {categories.map(c => (
              <option key={c.id} value={c.id}>
                {'  '.repeat(c.depth)}{c.description}
              </option>
            ))}
          </select>

          <select className="input py-1 text-sm max-w-xs" value="" disabled={busy}
            onChange={e => { const v = e.target.value; if (v) applyTag(Number(v), false) }}>
            <option value="">Add tag…</option>
            {tags.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
          </select>

          <select className="input py-1 text-sm max-w-xs" value="" disabled={busy}
            onChange={e => { const v = e.target.value; if (v) applyTag(Number(v), true) }}>
            <option value="">Remove tag…</option>
            {tags.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
          </select>

          <button type="button" className="text-xs text-navy underline ml-auto"
            onClick={() => setSelected(new Set())}>Clear selection</button>
        </div>
      )}

      {/* ── Results ── */}
      {rows.length === 0 && !busy ? (
        <p className="text-sm text-text-secondary">
          {isFiltered(filter)
            ? 'No lines match this filter.'
            : 'No transactions yet — ingest a statement and they will appear here.'}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-text-secondary border-b border-border">
                <th className="py-2 pr-2 w-8">
                  <input type="checkbox" checked={allOnPageSelected} aria-label="Select every line on this page"
                    onChange={e => setSelected(e.target.checked ? new Set(rows.map(r => r.id)) : new Set())} />
                </th>
                <th className="py-2 pr-3 font-medium">Date</th>
                <th className="py-2 pr-3 font-medium">Description</th>
                <th className="py-2 pr-3 font-medium">Account</th>
                <th className="py-2 pr-3 font-medium">Category</th>
                <th className="py-2 pr-3 font-medium">Source</th>
                <th className="py-2 pr-3 font-medium text-right">Amount</th>
                <th className="py-2 font-medium w-12"><span className="sr-only">Correct</span></th>
              </tr>
            </thead>
            <tbody>
              {rows.map(row => {
                const category = row.category_id ? categoryById.get(row.category_id) : undefined
                return (
                  <tr key={row.id}
                    className={`border-b border-border/60 align-top ${
                      row.deleted_at ? 'opacity-50 line-through decoration-red-400' : ''} ${
                      selected.has(row.id) ? 'bg-navy/5' : ''}`}>
                    <td className="py-2 pr-2">
                      <input type="checkbox" checked={selected.has(row.id)}
                        aria-label={`Select ${row.description}`}
                        onChange={() => toggleRow(row.id)} />
                    </td>
                    <td className="py-2 pr-3 whitespace-nowrap tabular-nums">
                      {formatDate(row.transaction_date)}
                      {row.date_provenance !== 'printed' && (
                        <span className="ml-1 text-xs text-amber-700"
                          title="Year inferred from the statement period, not printed on the line">†</span>
                      )}
                    </td>
                    <td className="py-2 pr-3">
                      <div>
                        {row.check_number && (
                          <span className="mr-1.5 text-xs font-mono rounded px-1.5 py-0.5 bg-navy/10 text-navy">
                            #{row.check_number}
                          </span>
                        )}
                        {row.description}<CorrectedMark flags={row.flags} />
                        {row.deleted_at && (
                          <span className="ml-2 text-xs text-red-700 no-underline">
                            removed{row.deletion_reason ? ` — ${row.deletion_reason}` : ''}
                          </span>
                        )}
                      </div>
                      {row.location && <span className="text-xs text-text-secondary">{row.location}</span>}
                      {row.tag_ids.length > 0 && (
                        <div className="flex flex-wrap gap-1 mt-1">
                          {row.tag_ids.map(id => {
                            const tag = tagById.get(id)
                            if (!tag) return null
                            return (
                              <span key={id} className={`text-xs rounded-full px-2 py-0.5 border ${tagClass(tag)}`}>
                                {tag.label}
                              </span>
                            )
                          })}
                        </div>
                      )}
                    </td>
                    <td className="py-2 pr-3 text-xs text-text-secondary whitespace-nowrap">
                      {row.institution}
                      {row.account_last4 && <span className="block">····{row.account_last4}</span>}
                    </td>
                    <td className="py-2 pr-3 text-xs">
                      {category ? (
                        <span className="text-text-secondary" title={category.path}>{category.description}</span>
                      ) : row.category ? (
                        // The extractor's free-text guess. Shown in italics so it
                        // is never mistaken for a category someone actually chose.
                        <span className="text-text-secondary/70 italic" title="Suggested by extraction — not filed">
                          {row.category}
                        </span>
                      ) : (
                        <span className="text-amber-700">—</span>
                      )}
                    </td>
                    <td className="py-2 pr-3 text-xs text-text-secondary whitespace-nowrap tabular-nums">
                      {row.bates_number ?? (row.physical_page_number ? `p. ${row.physical_page_number}` : '—')}
                      {row.bates_number && row.physical_page_number && (
                        <span className="block text-text-secondary/70">p. {row.physical_page_number}</span>
                      )}
                    </td>
                    <td className={`py-2 pr-3 text-right whitespace-nowrap tabular-nums ${
                      isNegative(row.amount) ? 'text-text-primary' : 'text-green-700'}`}>
                      {money(row.amount)}
                    </td>
                    <td className="py-2 text-right">
                      <button type="button" className="text-xs text-navy underline"
                        onClick={() => setCorrecting(row)}>Edit</button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-xs text-text-secondary">
        † date derived from the statement period rather than printed on the line. Amounts are signed by
        their effect on the printed balance. Source shows the Bates number where the production carried
        one, and the page of the source PDF.
      </p>
    </div>
  )
}

/**
 * Create and retire the tags on a matter.
 *
 * Only matter-scoped tags can be made here. The firm-wide layer ("Waste Claim")
 * is administered centrally, because a label added to every case in the firm is
 * not a per-matter decision.
 */
function TagManager({ matterId, tags, onChanged, onError }: {
  matterId: number
  tags: TransactionTag[]
  onChanged: () => Promise<void>
  onError: (message: string) => void
}) {
  const [label, setLabel] = useState('')
  const [description, setDescription] = useState('')
  const [color, setColor] = useState('gray')
  const [busy, setBusy] = useState(false)

  async function create() {
    if (!label.trim()) return
    setBusy(true)
    try {
      await createMatterTag(matterId, { label: label.trim(), description: description.trim() || null, color })
      setLabel(''); setDescription('')
      await onChanged()
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Could not create the tag')
    } finally { setBusy(false) }
  }

  async function remove(tag: TransactionTag) {
    setBusy(true)
    try {
      await deleteTransactionTag(tag.id)
      await onChanged()
    } catch (err) {
      // The API refuses with a 409 while the tag is in use. That count is the
      // whole point of the guard, so surface it and make the user confirm.
      const message = err instanceof Error ? err.message : 'Could not delete the tag'
      if (message.includes('force=true')) {
        if (window.confirm(`${message}\n\nDelete it anyway? The lines stay, but this tag comes off all of them.`)) {
          try {
            await deleteTransactionTag(tag.id, true)
            await onChanged()
          } catch (forced) {
            onError(forced instanceof Error ? forced.message : 'Could not delete the tag')
          }
        }
      } else {
        onError(message)
      }
    } finally { setBusy(false) }
  }

  const matterTags = tags.filter(t => t.matter_id !== null)

  return (
    <div className="border border-border rounded p-3 space-y-3 bg-off-white/50">
      <div className="grid gap-2 md:grid-cols-[1fr_1.5fr_auto_auto]">
        <input className="input" placeholder="Waste: Sister's Wedding" value={label}
          aria-label="Tag label" onChange={e => setLabel(e.target.value)} />
        <input className="input" placeholder="What this tag means, for whoever tags next" value={description}
          aria-label="Tag description" onChange={e => setDescription(e.target.value)} />
        <select className="input" value={color} aria-label="Tag colour"
          onChange={e => setColor(e.target.value)}>
          {TAG_COLORS.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <button type="button" className="btn-primary" disabled={busy || !label.trim()} onClick={create}>
          Add tag
        </button>
      </div>

      {matterTags.length === 0 ? (
        <p className="text-xs text-text-secondary">
          No tags specific to this matter yet. The firm-wide tags above are available on every case.
        </p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {matterTags.map(t => (
            <span key={t.id} className={`text-xs rounded-full pl-2.5 pr-1 py-1 border flex items-center gap-1.5 ${tagClass(t)}`}>
              {t.label}
              {t.usage_count ? <span className="opacity-70 tabular-nums">{t.usage_count}</span> : null}
              <button type="button" className="px-1 opacity-60 hover:opacity-100" disabled={busy}
                aria-label={`Delete ${t.label}`} onClick={() => remove(t)}>×</button>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
