import { useState } from 'react'
import { createFinancialAccount } from '../../lib/api'
import type {
  AccountOwnership, AccountType, FinancialAccount, ProductionResponsibility, PropertyCharacter,
} from '../../types'

/**
 * Add an account by hand.
 *
 * Two jobs, and the second is the one that changes a workflow:
 *
 * - **A scrap of paper.** A receipt from a car floorboard naming an account
 *   nothing can be ingested for. It still belongs on the matter.
 * - **Resolving a row on the undisclosed list.** That report is rebuilt from
 *   the transactions on every run — which is right, and is why nothing can be
 *   ticked off it. Adding the account is what takes it off, and the character
 *   recorded says why: *No marital property interest* for a sibling repaid $5
 *   for parking, anything else for an account the estate has an interest in,
 *   which then joins the compliance grid with no statements against it. That
 *   second case is the point: an account on the matter with nothing produced
 *   is what a motion to compel is made of.
 */
export default function AccountAddDialog({ matterId, prefill, onAdded, onClose }: {
  matterId: number
  prefill?: {
    institution?: string | null
    account_number_last4?: string | null
    account_number_masked?: string | null
  }
  onAdded: (account: FinancialAccount) => void
  onClose: () => void
}) {
  const [draft, setDraft] = useState({
    institution: prefill?.institution ?? '',
    account_type: 'checking' as AccountType,
    account_number_last4: prefill?.account_number_last4 ?? '',
    account_number_masked: prefill?.account_number_masked ?? '',
    name_on_account: '',
    ownership: 'unknown' as AccountOwnership,
    property_character: '' as PropertyCharacter | '',
    production_responsibility: 'unknown' as ProductionResponsibility,
    purpose: '',
    notes: '',
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function set<K extends keyof typeof draft>(key: K, value: (typeof draft)[K]) {
    setDraft(prev => ({ ...prev, [key]: value }))
  }

  const outside = draft.property_character === 'no_marital_interest'

  async function save() {
    setBusy(true); setError(null)
    try {
      onAdded(await createFinancialAccount(matterId, {
        institution: draft.institution.trim(),
        account_type: draft.account_type,
        account_number_last4: draft.account_number_last4.trim() || null,
        account_number_masked: draft.account_number_masked.trim() || null,
        name_on_account: draft.name_on_account.trim() || null,
        ownership: draft.ownership,
        property_character: draft.property_character || null,
        production_responsibility: draft.production_responsibility,
        purpose: draft.purpose.trim() || null,
        notes: draft.notes.trim() || null,
      }))
    } catch (e) {
      // A 409 means the matter already has this account — the server says which.
      setError(e instanceof Error ? e.message : 'Could not add the account')
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-start justify-center overflow-y-auto p-4"
      role="dialog" aria-modal="true" aria-label="Add an account"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="card p-5 w-full max-w-2xl my-8 bg-white">
        <div className="flex items-baseline justify-between mb-1">
          <h2 className="font-semibold text-navy">Add an account</h2>
          <button type="button" className="text-sm text-text-secondary hover:text-navy"
            onClick={onClose}>Close</button>
        </div>
        <p className="text-xs text-text-secondary mb-4">
          For an account no statement has been imported for. It appears on the compliance grid
          with nothing produced against it — unless you mark it as outside the marital estate.
        </p>

        <div className="grid gap-3 md:grid-cols-2">
          <div className="md:col-span-2">
            <label className="label" htmlFor="acc-institution">Institution</label>
            <input id="acc-institution" className="input mt-1" value={draft.institution}
              placeholder="Bank of America" onChange={e => set('institution', e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="acc-type">Type</label>
            <select id="acc-type" className="input mt-1" value={draft.account_type}
              onChange={e => set('account_type', e.target.value as AccountType)}>
              {(['checking', 'savings', 'brokerage', 'crypto', 'credit_card', 'loan',
                 'retirement', 'life_insurance', 'annuity', 'hsa', 'other'] as AccountType[])
                .map(t => <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>)}
            </select>
          </div>
          <div>
            <label className="label" htmlFor="acc-last4">Last four</label>
            <input id="acc-last4" className="input mt-1 font-mono" maxLength={4}
              value={draft.account_number_last4}
              onChange={e => set('account_number_last4', e.target.value.replace(/\D/g, ''))} />
            <p className="text-xs text-text-secondary mt-1">
              How a reference is matched to this account. Without it, mentions of it keep
              appearing as undisclosed.
            </p>
          </div>
          <div>
            <label className="label" htmlFor="acc-owner">Held by</label>
            <select id="acc-owner" className="input mt-1" value={draft.ownership}
              onChange={e => set('ownership', e.target.value as AccountOwnership)}>
              <option value="unknown">Not known</option>
              <option value="client_sole">Our client</option>
              <option value="opposing_sole">The other party</option>
              <option value="joint">Joint</option>
              <option value="third_party">A third party</option>
            </select>
          </div>
          <div>
            <label className="label" htmlFor="acc-character">Character</label>
            <select id="acc-character" className="input mt-1" value={draft.property_character}
              onChange={e => set('property_character', e.target.value as PropertyCharacter | '')}>
              <option value="">Not decided</option>
              <option value="community">Community</option>
              <option value="separate_petitioner">Separate — Petitioner</option>
              <option value="separate_respondent">Separate — Respondent</option>
              <option value="mixed">Mixed</option>
              <option value="disputed">Disputed</option>
              <option value="no_marital_interest">No marital property interest</option>
            </select>
          </div>
          <div>
            <label className="label" htmlFor="acc-produces">Who produces it</label>
            <select id="acc-produces" className="input mt-1" value={draft.production_responsibility}
              onChange={e => set('production_responsibility',
                                 e.target.value as ProductionResponsibility)}>
              <option value="unknown">Not assigned</option>
              <option value="client">Our client</option>
              <option value="opposing">The other side</option>
              <option value="both">Both</option>
            </select>
            <p className="text-xs text-text-secondary mt-1">
              Unassigned accounts appear on neither compliance report.
            </p>
          </div>
          <div className="md:col-span-2">
            <label className="label" htmlFor="acc-notes">Notes</label>
            <input id="acc-notes" className="input mt-1" value={draft.notes}
              placeholder="Receipt found in the vehicle; account referenced by a transfer"
              onChange={e => set('notes', e.target.value)} />
          </div>
        </div>

        {outside && (
          <p className="mt-3 rounded-md bg-navy/5 px-3 py-2 text-xs text-text-secondary">
            Marked as outside the marital estate, this account is kept so references to it stop
            appearing as undisclosed — and it is left out of the compliance grid and the
            inventory, because there is nothing to compel and nothing to divide.
          </p>
        )}

        {error && <p className="text-sm text-red-700 mt-3">{error}</p>}

        <div className="flex items-center justify-end gap-3 mt-5">
          <button type="button" className="btn-secondary" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn-primary"
            disabled={busy || !draft.institution.trim()} onClick={() => void save()}>
            {busy ? 'Adding…' : 'Add account'}
          </button>
        </div>
      </div>
    </div>
  )
}
