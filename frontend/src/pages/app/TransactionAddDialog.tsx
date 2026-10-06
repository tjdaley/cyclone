import { useState } from 'react'
import { addTransaction } from '../../lib/api'
import type { AccountStatement, AccountTransaction } from '../../types'

/**
 * Add a line the import missed.
 *
 * **What this replaces is an INSERT typed into the database.** A line the
 * extraction never produced — a page a pass stopped reading, a figure printed
 * somewhere nothing looked — leaves the statement permanently out of balance,
 * and the fix was a row entered by hand at zero and then corrected through the
 * app until the arithmetic closed. That works and leaves no trace anybody
 * reading the exhibit can see.
 *
 * So the flag is the feature, not the insert: the line is recorded as
 * MANUAL_ENTRY with the person's name and their reason, which is the same
 * promise a correction makes. The dialog says so before anything is saved,
 * because somebody adding a figure to evidence should know it will carry their
 * name.
 *
 * It is for a line that IS on the document and was missed. Nothing can check
 * that, which is exactly why the record names a person.
 */
export default function TransactionAddDialog({ statement, onAdded, onClose }: {
  statement: AccountStatement
  onAdded: (created: AccountTransaction, updated: AccountStatement | null) => void
  onClose: () => void
}) {
  const [draft, setDraft] = useState({
    description: '',
    // Defaulted to the period's start so a date is at worst inside the right
    // statement. An empty date is accepted — a policy statement's figures are
    // period totals and never had one — but a guessed month is not.
    transaction_date: statement.period_start ?? '',
    amount: '',
    counterparty: '',
    check_number: '',
    physical_page_number: '',
    bates_number: '',
  })
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function set<K extends keyof typeof draft>(key: K, value: (typeof draft)[K]) {
    setDraft(prev => ({ ...prev, [key]: value }))
  }

  // Money stays a string end to end, so this asks only whether the characters
  // form a signed figure — never Number().
  const amountReady = /^-?\$?\s*\d[\d,]*(\.\d{1,2})?$/.test(draft.amount.trim())
  const ready = draft.description.trim().length > 0 && amountReady

  async function save() {
    setBusy(true); setError(null)
    try {
      const result = await addTransaction(statement.id, {
        description: draft.description.trim(),
        amount: draft.amount.replace(/[$,\s]/g, ''),
        transaction_date: draft.transaction_date || null,
        counterparty: draft.counterparty.trim() || null,
        check_number: draft.check_number.trim() || null,
        physical_page_number: draft.physical_page_number
          ? Number(draft.physical_page_number)
          : null,
        bates_number: draft.bates_number.trim() || null,
        ...(reason.trim() ? { reason: reason.trim() } : {}),
      })
      onAdded(result.transaction, result.statement)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not add the line')
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-start justify-center overflow-y-auto p-4"
      role="dialog" aria-modal="true" aria-label="Add a transaction"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="card p-5 w-full max-w-2xl my-8 bg-white">
        <div className="flex items-baseline justify-between mb-1">
          <h2 className="font-semibold text-navy">Add a line to this statement</h2>
          <button type="button" className="text-sm text-text-secondary hover:text-navy"
            onClick={onClose}>Close</button>
        </div>
        <p className="text-xs text-text-secondary mb-4">
          For a line that is printed on the statement and the import missed. It is recorded with
          your name as added by hand, and the statement is re-checked against its printed
          balances when you save.
        </p>

        <div className="grid gap-3 md:grid-cols-2">
          <div className="md:col-span-2">
            <label className="label" htmlFor="add-desc">Description</label>
            <input id="add-desc" className="input mt-1" value={draft.description}
              placeholder="As printed on the statement"
              onChange={e => set('description', e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="add-date">Transaction date</label>
            <input id="add-date" type="date" className="input mt-1" value={draft.transaction_date}
              min={statement.period_start ?? undefined} max={statement.period_end ?? undefined}
              onChange={e => set('transaction_date', e.target.value)} />
            <p className="text-xs text-text-secondary mt-1">
              Leave blank only if the statement prints none. An undated line is excluded from
              every date-filtered search and exhibit.
            </p>
          </div>
          <div>
            <label className="label" htmlFor="add-amount">Amount</label>
            <input id="add-amount" className="input mt-1 font-mono tabular-nums" value={draft.amount}
              inputMode="decimal" placeholder="-1250.00"
              onChange={e => set('amount', e.target.value)} />
            <p className="text-xs text-text-secondary mt-1">
              Signed by its effect on the printed balance: a deposit and a card purchase are both
              positive, a withdrawal and a card payment both negative.
            </p>
            {draft.amount.trim() && !amountReady && (
              <p className="text-xs text-warning mt-1">Type a figure, such as -1250.00</p>
            )}
          </div>
          <div>
            <label className="label" htmlFor="add-counterparty">Counterparty</label>
            <input id="add-counterparty" className="input mt-1" value={draft.counterparty}
              onChange={e => set('counterparty', e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="add-check">Check number</label>
            <input id="add-check" className="input mt-1 font-mono" value={draft.check_number}
              onChange={e => set('check_number', e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="add-page">Page</label>
            <input id="add-page" className="input mt-1 tabular-nums" value={draft.physical_page_number}
              inputMode="numeric" onChange={e => set('physical_page_number', e.target.value)} />
            <p className="text-xs text-text-secondary mt-1">
              The page of the source PDF it is printed on — what a reader checks it against.
            </p>
          </div>
          <div>
            <label className="label" htmlFor="add-bates">Bates number</label>
            <input id="add-bates" className="input mt-1 font-mono" value={draft.bates_number}
              onChange={e => set('bates_number', e.target.value)} />
            <p className="text-xs text-text-secondary mt-1">
              Only what is stamped on that page. Never a number worked out from its neighbours.
            </p>
          </div>
          <div className="md:col-span-2">
            <label className="label" htmlFor="add-reason">Why (recommended)</label>
            <input id="add-reason" className="input mt-1" value={reason}
              placeholder="Printed on page 7; the import stopped before it"
              onChange={e => setReason(e.target.value)} />
          </div>
        </div>

        {error && <p className="text-sm text-red-700 mt-3">{error}</p>}

        <div className="flex items-center justify-end gap-3 mt-5">
          <button type="button" className="btn-secondary" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn-primary" disabled={busy || !ready} onClick={save}>
            {busy ? 'Adding…' : 'Add line'}
          </button>
        </div>
      </div>
    </div>
  )
}
