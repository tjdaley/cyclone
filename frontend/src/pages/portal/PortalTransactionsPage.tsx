import { useCallback, useEffect, useMemo, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import {
  applyPortalTag, getPortalAccounts, getPortalTags, getPortalTransactions, removePortalTag,
} from '../../lib/api'
import { money, isNegative, formatDate } from '../../lib/money'
import type {
  PortalAccount, PortalMatter, PortalTag, PortalTransaction,
} from '../../types'

/**
 * The screen the portal exists for: the client going through their statements
 * and marking what they think matters.
 *
 * Three decisions shape it:
 *
 * - **A tag is a suggestion, and the page says so.** Nothing here claims the
 *   client has found waste; it says their attorney will review each one. The
 *   firm's own tags are not offered, only the ones published to clients.
 * - **A tag the firm removed is shown greyed and cannot be re-applied.** Hiding
 *   it would mean the client re-tags, is refused, and reports a bug.
 * - **Progress is visible.** Somebody working through two years of statements
 *   needs to keep their place more than they need anything else.
 */
export default function PortalTransactionsPage() {
  const { matter } = useOutletContext<{ matter: PortalMatter }>()

  const [accounts, setAccounts] = useState<PortalAccount[]>([])
  const [tags, setTags] = useState<PortalTag[]>([])
  const [rows, setRows] = useState<PortalTransaction[]>([])
  const [total, setTotal] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const [accountId, setAccountId] = useState<number | null>(null)
  const [tagId, setTagId] = useState<number | null>(null)
  const [untagged, setUntagged] = useState(false)
  const [textDraft, setTextDraft] = useState('')
  const [text, setText] = useState('')
  const [minDraft, setMinDraft] = useState('')
  const [maxDraft, setMaxDraft] = useState('')
  const [offset, setOffset] = useState(0)
  const PAGE = 100

  const tagById = useMemo(() => new Map(tags.map(t => [t.id, t])), [tags])

  useEffect(() => {
    getPortalAccounts(matter.id).then(setAccounts).catch(() => setAccounts([]))
    getPortalTags(matter.id).then(setTags).catch(() => setTags([]))
  }, [matter.id])

  // Amounts and text are debounced together, and an amount is only sent once it
  // is a whole figure — typing "-" on the way to "-500" must not flash an error.
  const amountReady = (draft: string) =>
    draft.trim() === '' || /^[+-]?\s*\$?\s*\d[\d,]*(\.\d{0,2})?$/.test(draft.trim())
  const amountsReady = amountReady(minDraft) && amountReady(maxDraft)

  useEffect(() => {
    const handle = setTimeout(() => setText(textDraft), 300)
    return () => clearTimeout(handle)
  }, [textDraft])

  const load = useCallback(async () => {
    if (!amountsReady) return
    setBusy(true); setError(null)
    try {
      const page = await getPortalTransactions(matter.id, {
        account_id: accountId, tag_id: tagId, untagged,
        text: text || null,
        amount_min: minDraft.trim() || null,
        amount_max: maxDraft.trim() || null,
        limit: PAGE, offset,
      })
      setRows(page.items)
      setTotal(page.total)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load your transactions')
    } finally { setBusy(false) }
  }, [matter.id, accountId, tagId, untagged, text, minDraft, maxDraft, offset, amountsReady])

  useEffect(() => { void load() }, [load])

  // Any change of criteria invalidates the page you were on.
  useEffect(() => { setOffset(0) }, [accountId, tagId, untagged, text, minDraft, maxDraft])

  const tagged = rows.filter(r => r.tag_ids.length > 0).length

  async function toggle(row: PortalTransaction, tag: PortalTag) {
    if (!matter.tagging_enabled) return
    if (row.cleared_tag_ids.includes(tag.id)) return
    setError(null); setNotice(null)
    const applying = !row.tag_ids.includes(tag.id)
    try {
      const result = applying
        ? await applyPortalTag(row.id, tag.id)
        : await removePortalTag(row.id, tag.id)
      // The server returns the line's state afterwards, so the row never has to
      // be guessed at from what we thought we sent.
      setRows(prev => prev.map(r => r.id === row.id
        ? { ...r, tag_ids: result.tag_ids, cleared_tag_ids: result.cleared_tag_ids }
        : r))
    } catch (e) {
      // A 409 is the two refusals that matter — paused, or already reviewed and
      // removed — and the server's sentence is written to be read by the client.
      setError(e instanceof Error ? e.message : 'That could not be saved')
    }
  }

  return (
    <div className="space-y-4">
      <div className="card p-5">
        <h2 className="font-display text-xl text-navy">Your transactions</h2>
        <p className="text-text-secondary mt-1">
          Go through these and mark anything you think your attorney should look at. Nothing
          you mark changes the record — it is a note for your attorney's office, and they
          review each one.
        </p>

        {!matter.tagging_enabled && (
          <p className="mt-3 rounded-md bg-amber-50 border border-amber-200 px-3 py-2 text-sm text-amber-900">
            Tagging is paused while your attorney's office prepares for a hearing. Everything
            you marked before is safe, and you can still read through your transactions.
          </p>
        )}

        <div className="grid gap-3 md:grid-cols-4 mt-4">
          <div className="md:col-span-2">
            <label className="label" htmlFor="p-text">Search the description</label>
            <input id="p-text" className="input mt-1" value={textDraft}
              placeholder="target, transfer, cash…"
              onChange={e => setTextDraft(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="p-min">Amount at least</label>
            <input id="p-min" className="input mt-1 font-mono" placeholder="5000"
              value={minDraft} onChange={e => setMinDraft(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="p-max">Amount at most</label>
            <input id="p-max" className="input mt-1 font-mono" placeholder="-25000"
              value={maxDraft} onChange={e => setMaxDraft(e.target.value)} />
          </div>
          <div className="md:col-span-2">
            <p className={`text-xs ${amountsReady ? 'text-text-secondary' : 'text-amber-700'}`}>
              {amountsReady
                ? 'No sign finds both directions — 5000 finds money in and money out. Use -5000 for money out only.'
                : 'Type a figure, such as 5000 or -5000.'}
            </p>
          </div>
          <div>
            <label className="label" htmlFor="p-account">Account</label>
            <select id="p-account" className="input mt-1" value={accountId ?? ''}
              onChange={e => setAccountId(e.target.value ? Number(e.target.value) : null)}>
              <option value="">All accounts</option>
              {accounts.map(a => (
                <option key={a.id} value={a.id}>
                  {a.institution}{a.account_number_last4 ? ` ····${a.account_number_last4}` : ''}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label" htmlFor="p-tag">Show</label>
            <select id="p-tag" className="input mt-1"
              value={untagged ? 'untagged' : (tagId ?? '')}
              onChange={e => {
                const value = e.target.value
                setUntagged(value === 'untagged')
                setTagId(value && value !== 'untagged' ? Number(value) : null)
              }}>
              <option value="">Everything</option>
              <option value="untagged">Not yet marked</option>
              {tags.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
            </select>
          </div>
        </div>
      </div>

      {error && (
        <p className="rounded-md bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-800">
          {error}
        </p>
      )}
      {notice && <p className="text-sm text-text-secondary">{notice}</p>}

      <div className="card">
        <div className="px-5 py-3 border-b border-border flex flex-wrap items-baseline gap-3">
          <span className="text-sm text-text-secondary tabular-nums">
            {total.toLocaleString()} transaction{total === 1 ? '' : 's'}
          </span>
          {rows.length > 0 && (
            <span className="text-sm text-text-secondary">
              {tagged} of {rows.length} on this page marked
            </span>
          )}
          {busy && <span className="text-sm text-text-secondary">Loading…</span>}
        </div>

        <ul className="divide-y divide-border">
          {rows.map(row => (
            <li key={row.id} className="px-5 py-4">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className="text-sm text-text-secondary tabular-nums w-24">
                  {formatDate(row.transaction_date)}
                </span>
                <span className="font-medium text-text-primary">{row.description}</span>
                <span className={`ml-auto tabular-nums font-medium ${
                  isNegative(row.amount) ? 'text-text-primary' : 'text-green-700'}`}>
                  {money(row.amount, row.currency)}
                </span>
              </div>
              <div className="text-xs text-text-secondary mt-0.5">
                {row.institution}{row.account_last4 ? ` ····${row.account_last4}` : ''}
                {row.check_number ? ` · check ${row.check_number}` : ''}
              </div>

              <div className="flex flex-wrap gap-1.5 mt-2">
                {tags.map(tag => {
                  const on = row.tag_ids.includes(tag.id)
                  const cleared = row.cleared_tag_ids.includes(tag.id)
                  return (
                    <button key={tag.id} type="button"
                      disabled={cleared || !matter.tagging_enabled}
                      title={cleared
                        ? "Your attorney's office reviewed this one and removed this tag."
                        : tag.description ?? undefined}
                      onClick={() => toggle(row, tag)}
                      className={`text-xs rounded-full px-2.5 py-1 border transition-colors ${
                        cleared
                          ? 'border-border bg-gray-100 text-gray-400 line-through cursor-not-allowed'
                          : on
                            ? 'border-navy bg-navy text-white'
                            : 'border-border text-text-secondary hover:border-navy hover:text-navy'
                      } ${!matter.tagging_enabled && !cleared ? 'opacity-50 cursor-not-allowed' : ''}`}>
                      {tag.label}
                    </button>
                  )
                })}
                {/* Tags the firm applied that are not in the client list still
                    show, greyed and unlabelled as theirs — the client should see
                    that a line has been looked at, without being handed the
                    firm's vocabulary. */}
                {row.tag_ids.filter(id => !tagById.has(id)).length > 0 && (
                  <span className="text-xs rounded-full px-2.5 py-1 border border-border text-text-secondary">
                    marked by your attorney's office
                  </span>
                )}
              </div>
            </li>
          ))}
          {rows.length === 0 && !busy && (
            <li className="px-5 py-8 text-center text-text-secondary">
              Nothing matches what you are looking for.
            </li>
          )}
        </ul>

        {total > PAGE && (
          <div className="px-5 py-3 border-t border-border flex items-center justify-between">
            <button type="button" className="btn-secondary" disabled={offset === 0 || busy}
              onClick={() => setOffset(Math.max(0, offset - PAGE))}>Back</button>
            <span className="text-sm text-text-secondary tabular-nums">
              {offset + 1}–{Math.min(offset + PAGE, total)} of {total.toLocaleString()}
            </span>
            <button type="button" className="btn-secondary"
              disabled={offset + PAGE >= total || busy}
              onClick={() => setOffset(offset + PAGE)}>Next</button>
          </div>
        )}
      </div>
    </div>
  )
}
