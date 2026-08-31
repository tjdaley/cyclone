import { useState } from 'react'
import { correctTransaction, deleteTransaction, restoreTransaction } from '../../lib/api'
import { money, formatDate } from '../../lib/money'
import type { AccountTransaction, AccountStatement, ExtractionFlag } from '../../types'

/**
 * Correct a value on an ingested line.
 *
 * Extraction misreads things — a smudged digit, a description running off the
 * page — so a line has to be correctable. But the corrected figure ends up in
 * an exhibit, and the first question on cross is where it came from, so nothing
 * is quietly overwritten: every change appends a MANUAL_CORRECTION flag naming
 * the field, both values, and the person who made it.
 */
export default function TransactionEditDialog({ transaction, onSaved, onClose }: {
  transaction: AccountTransaction
  onSaved: (updated: AccountTransaction, statement: AccountStatement | null) => void
  onClose: () => void
}) {
  const [draft, setDraft] = useState({
    description: transaction.description,
    transaction_date: transaction.transaction_date ?? '',
    posted_date: transaction.posted_date ?? '',
    counterparty: transaction.counterparty ?? '',
    location: transaction.location ?? '',
    amount: transaction.amount,
    running_balance: transaction.running_balance ?? '',
    bates_number: transaction.bates_number ?? '',
    check_number: transaction.check_number ?? '',
    physical_page_number: transaction.physical_page_number?.toString() ?? '',
  })
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmingDrop, setConfirmingDrop] = useState(false)

  function set<K extends keyof typeof draft>(key: K, value: (typeof draft)[K]) {
    setDraft(prev => ({ ...prev, [key]: value }))
  }

  const amountChanged = draft.amount !== transaction.amount
  const corrections = transaction.flags.filter(f => f.code === 'MANUAL_CORRECTION')

  async function save() {
    setBusy(true); setError(null)
    try {
      // Send only what actually differs. The server records one audit entry per
      // changed field, so posting the whole form would invent a history of
      // edits that never happened.
      const patch: Record<string, unknown> = {}
      if (draft.description !== transaction.description) patch.description = draft.description
      if (draft.transaction_date !== (transaction.transaction_date ?? '')) {
        patch.transaction_date = draft.transaction_date || null
      }
      if (draft.posted_date !== (transaction.posted_date ?? '')) {
        patch.posted_date = draft.posted_date || null
      }
      if (draft.counterparty !== (transaction.counterparty ?? '')) {
        patch.counterparty = draft.counterparty || null
      }
      if (draft.location !== (transaction.location ?? '')) patch.location = draft.location || null
      if (draft.amount !== transaction.amount) patch.amount = draft.amount
      if (draft.running_balance !== (transaction.running_balance ?? '')) {
        patch.running_balance = draft.running_balance || null
      }
      if (draft.bates_number !== (transaction.bates_number ?? '')) {
        patch.bates_number = draft.bates_number || null
      }
      if (draft.check_number !== (transaction.check_number ?? '')) {
        patch.check_number = draft.check_number || null
      }
      if (draft.physical_page_number !== (transaction.physical_page_number?.toString() ?? '')) {
        patch.physical_page_number = draft.physical_page_number ? Number(draft.physical_page_number) : null
      }
      if (Object.keys(patch).length === 0) { onClose(); return }
      if (reason.trim()) patch.reason = reason.trim()

      const result = await correctTransaction(transaction.id, patch)
      onSaved(result.transaction, result.statement)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the correction')
      setBusy(false)
    }
  }

  async function drop() {
    setBusy(true); setError(null)
    try {
      const result = await deleteTransaction(transaction.id, reason)
      onSaved(result.transaction, result.statement)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not remove the line')
      setBusy(false)
    }
  }

  async function restore() {
    setBusy(true); setError(null)
    try {
      const result = await restoreTransaction(transaction.id)
      onSaved(result.transaction, result.statement)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not restore the line')
      setBusy(false)
    }
  }

  const isDropped = transaction.deleted_at !== null

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-start justify-center overflow-y-auto p-4"
      role="dialog" aria-modal="true" aria-label="Correct transaction"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="card p-5 w-full max-w-2xl my-8 bg-white">
        <div className="flex items-baseline justify-between mb-1">
          <h2 className="font-semibold text-navy">Correct this line</h2>
          <button type="button" className="text-sm text-text-secondary hover:text-navy"
            onClick={onClose}>Close</button>
        </div>
        <p className="text-xs text-text-secondary mb-4">
          Every change is recorded on the line with your name and the old value, so the original
          stays readable from the record itself.
        </p>

        <div className="grid gap-3 md:grid-cols-2">
          <div className="md:col-span-2">
            <label className="label" htmlFor="tx-desc">Description</label>
            <input id="tx-desc" className="input mt-1" value={draft.description}
              onChange={e => set('description', e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="tx-date">Transaction date</label>
            <input id="tx-date" type="date" className="input mt-1" value={draft.transaction_date}
              onChange={e => set('transaction_date', e.target.value)} />
            {transaction.date_provenance !== 'printed' && (
              <p className="text-xs text-amber-700 mt-1">
                The year was inferred from the statement period, not printed on the line.
              </p>
            )}
          </div>
          <div>
            <label className="label" htmlFor="tx-posted">Posted date</label>
            <input id="tx-posted" type="date" className="input mt-1" value={draft.posted_date}
              onChange={e => set('posted_date', e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="tx-amount">Amount</label>
            <input id="tx-amount" className="input mt-1 font-mono tabular-nums" value={draft.amount}
              inputMode="decimal" onChange={e => set('amount', e.target.value)} />
            <p className="text-xs text-text-secondary mt-1">
              Signed by its effect on the printed balance: a deposit and a card purchase are both
              positive, a withdrawal and a card payment both negative.
            </p>
            {amountChanged && (
              <p className="text-xs text-navy mt-1">
                Saving re-checks the statement against its printed balances.
              </p>
            )}
          </div>
          <div>
            <label className="label" htmlFor="tx-running">Running balance</label>
            <input id="tx-running" className="input mt-1 font-mono tabular-nums"
              value={draft.running_balance} inputMode="decimal"
              onChange={e => set('running_balance', e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="tx-counterparty">Counterparty</label>
            <input id="tx-counterparty" className="input mt-1" value={draft.counterparty}
              onChange={e => set('counterparty', e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="tx-location">Location</label>
            <input id="tx-location" className="input mt-1" value={draft.location}
              onChange={e => set('location', e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="tx-bates">Bates number</label>
            <input id="tx-bates" className="input mt-1 font-mono" value={draft.bates_number}
              onChange={e => set('bates_number', e.target.value)} />
            <p className="text-xs text-text-secondary mt-1">
              Only what is actually stamped on the page. Leave blank if it is not.
            </p>
          </div>
          <div>
            <label className="label" htmlFor="tx-check-no">Check number</label>
            <input id="tx-check-no" className="input mt-1 font-mono" value={draft.check_number}
              onChange={e => set('check_number', e.target.value)} />
            <p className="text-xs text-text-secondary mt-1">
              As printed, without the asterisk — that marks a gap in the serial run, not the number.
            </p>
          </div>
          <div>
            <label className="label" htmlFor="tx-page">Page</label>
            <input id="tx-page" className="input mt-1 tabular-nums" value={draft.physical_page_number}
              inputMode="numeric" onChange={e => set('physical_page_number', e.target.value)} />
          </div>
          <div className="md:col-span-2">
            <label className="label" htmlFor="tx-reason">Why (optional)</label>
            <input id="tx-reason" className="input mt-1" value={reason}
              placeholder="Corrected against the source page"
              onChange={e => setReason(e.target.value)} />
          </div>
        </div>

        {corrections.length > 0 && (
          <div className="mt-4 border-t border-border pt-3">
            <h3 className="text-sm font-medium text-navy mb-1">Earlier corrections</h3>
            <ul className="space-y-1">
              {corrections.map((f, i) => <CorrectionLine key={i} flag={f} />)}
            </ul>
          </div>
        )}

        {error && <p className="text-sm text-red-700 mt-3">{error}</p>}

        <div className="flex flex-wrap items-center gap-3 mt-4">
          {isDropped ? (
            <button type="button" className="text-sm text-navy underline" disabled={busy}
              onClick={restore}>Put this line back</button>
          ) : confirmingDrop ? (
            <span className="flex items-center gap-2 text-sm">
              <span className="text-red-700">Remove this line from the statement?</span>
              <button type="button" className="text-red-700 underline font-medium" disabled={busy}
                onClick={drop}>Remove</button>
              <button type="button" className="text-text-secondary underline"
                onClick={() => setConfirmingDrop(false)}>Keep it</button>
            </span>
          ) : (
            <button type="button" className="text-sm text-red-700 underline" disabled={busy}
              onClick={() => setConfirmingDrop(true)}>Remove this line</button>
          )}

          <span className="ml-auto flex items-center gap-3">
            <button type="button" className="text-sm text-text-secondary hover:text-navy"
              onClick={onClose}>Cancel</button>
            <button type="button" className="btn-primary" disabled={busy || isDropped} onClick={save}>
              {busy ? 'Saving…' : 'Save correction'}
            </button>
          </span>
        </div>

        {!isDropped && confirmingDrop && (
          <p className="text-xs text-text-secondary mt-2">
            Removing hides the line and takes it out of every total, but keeps it — with your name
            and the reason above. If extraction invented this line, the statement will reconcile
            better without it; if it was real, the balance will stop tying and say so.
          </p>
        )}
        {isDropped && (
          <p className="text-xs text-amber-700 mt-2">
            This line was removed from the statement{transaction.deletion_reason
              ? ` — ${transaction.deletion_reason}` : ''}. It is excluded from every total until
            it is put back.
          </p>
        )}
      </div>
    </div>
  )
}

/** One entry from the line's correction history. */
function CorrectionLine({ flag }: { flag: ExtractionFlag }) {
  return (
    <li className="text-xs text-text-secondary">
      {flag.note}
      {flag.reason && <span className="italic"> {flag.reason}</span>}
      {flag.at && <span className="text-text-secondary/70"> · {formatDate(flag.at.slice(0, 10))}</span>}
    </li>
  )
}

/** Small marker for a row that has been corrected by hand. */
export function CorrectedMark({ flags }: { flags: ExtractionFlag[] }) {
  const corrections = flags.filter(f => f.code === 'MANUAL_CORRECTION')
  if (corrections.length === 0) return null
  return (
    <span className="ml-1 text-xs text-navy" title={corrections.map(f => f.note).join('\n')}>
      ✎
    </span>
  )
}

/** Formats a money string for a correction summary. Re-exported for convenience. */
export { money }
