import { useCallback, useEffect, useState } from 'react'
import { clearClientTag, getClientTags, restoreClientTag } from '../../lib/api'
import { money, isNegative, formatDate } from '../../lib/money'
import type { ClientTagReview } from '../../types'

/**
 * What the client has marked, for staff to review.
 *
 * **A client tag is useful and it is a guess**, and the two must never be
 * indistinguishable: tags drive Rule 1006 exhibits, and a client's guess
 * arriving in one as though an attorney had made it is the confident-wrong
 * failure this codebase spends its effort avoiding. This is the screen where
 * they are told apart.
 *
 * Clearing is a suppression, not a delete. The client cannot put back a tag
 * staff cleared — staff can, and it then becomes a staff tag, because applying
 * it is their judgment at that point.
 */
export default function ClientTagReviewPanel({ matterId }: { matterId: number }) {
  const [rows, setRows] = useState<ClientTagReview[]>([])
  const [showCleared, setShowCleared] = useState(false)
  const [busy, setBusy] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      setRows(await getClientTags(matterId, showCleared))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the client tags')
    } finally { setLoading(false) }
  }, [matterId, showCleared])

  useEffect(() => { void load() }, [load])

  async function act(row: ClientTagReview, clear: boolean) {
    setBusy(row.link_id); setError(null)
    try {
      const updated = clear ? await clearClientTag(row.link_id) : await restoreClientTag(row.link_id)
      // Dropped from the list optimistically when it no longer belongs there —
      // a row that stays put after being acted on is what produces a second click.
      setRows(prev => showCleared
        ? prev.map(r => (r.link_id === updated.link_id ? updated : r))
        : prev.filter(r => r.link_id !== row.link_id))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That could not be saved')
    } finally { setBusy(null) }
  }

  const live = rows.filter(r => !r.cleared_at).length

  return (
    <div className="card p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3 mb-1">
        <h2 className="font-semibold text-navy">Marked by the client</h2>
        <label className="text-xs text-text-secondary flex items-center gap-1.5">
          <input type="checkbox" checked={showCleared}
            onChange={e => setShowCleared(e.target.checked)} />
          Include ones you removed
        </label>
      </div>
      <p className="text-xs text-text-secondary mb-3">
        Tags the client applied in their portal. Removing one takes it off the line and the
        client cannot put it back — you can. A tag you restore becomes yours, because applying
        it is your judgment at that point.
      </p>

      {error && <p className="text-sm text-red-700 mb-2">{error}</p>}
      {loading && <p className="text-sm text-text-secondary">Loading…</p>}

      {!loading && rows.length === 0 && (
        <p className="text-sm text-text-secondary">
          The client has not marked anything yet.
        </p>
      )}

      {rows.length > 0 && (
        <>
          <p className="text-xs text-text-secondary mb-2 tabular-nums">
            {live} to review{showCleared && rows.length > live
              ? ` · ${rows.length - live} already removed` : ''}
          </p>
          <ul className="divide-y divide-border">
            {rows.map(row => (
              <li key={row.link_id}
                className={`py-2 flex flex-wrap items-baseline gap-x-3 gap-y-1 ${
                  row.cleared_at ? 'opacity-50' : ''}`}>
                <span className="text-xs rounded-full px-2 py-0.5 bg-rose-50 text-rose-800">
                  {row.tag_label}
                </span>
                <span className="text-sm tabular-nums text-text-secondary w-24">
                  {formatDate(row.transaction_date)}
                </span>
                <span className="text-sm">{row.description}</span>
                <span className="text-xs text-text-secondary">
                  {row.institution}{row.account_last4 ? ` ····${row.account_last4}` : ''}
                </span>
                <span className={`text-sm tabular-nums ${
                  isNegative(row.amount) ? 'text-text-primary' : 'text-green-700'}`}>
                  {money(row.amount)}
                </span>
                <span className="ml-auto flex items-center gap-3">
                  {row.cleared_at ? (
                    <button type="button" className="text-xs text-navy underline"
                      disabled={busy === row.link_id}
                      onClick={() => void act(row, false)}>
                      {busy === row.link_id ? 'Saving…' : 'Put it back'}
                    </button>
                  ) : (
                    <button type="button" className="text-xs text-red-700 underline"
                      disabled={busy === row.link_id}
                      title="Takes it off the line. The client cannot re-apply it."
                      onClick={() => void act(row, true)}>
                      {busy === row.link_id ? 'Saving…' : 'Remove'}
                    </button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}
