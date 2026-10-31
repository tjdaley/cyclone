import { useEffect, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { getPortalAccounts, getPortalStatements, openPortalStatementPdf } from '../../lib/api'
import { money, formatDate } from '../../lib/money'
import type { PortalAccount, PortalMatter, PortalStatement } from '../../types'

const TYPE_LABEL: Record<string, string> = {
  checking: 'Checking', savings: 'Savings', brokerage: 'Brokerage', crypto: 'Crypto',
  credit_card: 'Credit card', loan: 'Loan', retirement: 'Retirement',
  life_insurance: 'Life insurance', annuity: 'Annuity', hsa: 'HSA', other: 'Other',
}

/**
 * The accounts on the case and the statements held for each.
 *
 * Read-only, and worded for a client: a statement that has not reconciled is
 * "being checked", not INCOMPLETE_EXTRACTION. The staff flags are accurate and
 * written for a paralegal; a client reading them about their own bank statement
 * concludes the system is broken.
 */
export default function PortalAccountsPage() {
  const { matter } = useOutletContext<{ matter: PortalMatter }>()
  const [accounts, setAccounts] = useState<PortalAccount[]>([])
  const [openId, setOpenId] = useState<number | null>(null)
  const [statements, setStatements] = useState<PortalStatement[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getPortalAccounts(matter.id)
      .then(setAccounts)
      .catch(e => setError(e instanceof Error ? e.message : 'Could not load your accounts'))
  }, [matter.id])

  async function open(account: PortalAccount) {
    if (openId === account.id) { setOpenId(null); setStatements([]); return }
    setOpenId(account.id); setBusy(true); setError(null)
    try {
      setStatements(await getPortalStatements(account.id))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the statements')
    } finally { setBusy(false) }
  }

  return (
    <div className="space-y-4">
      <div className="card p-5">
        <h2 className="font-display text-xl text-navy">Accounts and statements</h2>
        <p className="text-text-secondary mt-1">
          Every account on your case, and the statements your attorney's office holds for each
          one. Open a statement to see the original document.
        </p>
      </div>

      {error && (
        <p className="rounded-md bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-800">
          {error}
        </p>
      )}

      <div className="card divide-y divide-border">
        {accounts.map(account => (
          <div key={account.id}>
            <button type="button" className="w-full text-left px-5 py-4 hover:bg-navy/5"
              onClick={() => open(account)}>
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className="font-medium text-text-primary">{account.institution}</span>
                {account.account_number_last4 && (
                  <span className="font-mono text-sm text-text-secondary">
                    ····{account.account_number_last4}
                  </span>
                )}
                <span className="text-xs rounded-full bg-navy/5 text-navy px-2 py-0.5">
                  {TYPE_LABEL[account.account_type] ?? account.account_type}
                </span>
                {account.is_closed && (
                  <span className="text-xs text-text-secondary">closed</span>
                )}
                <span className="ml-auto text-sm text-text-secondary tabular-nums">
                  {account.statements} statement{account.statements === 1 ? '' : 's'}
                </span>
              </div>
              {account.name_on_account && (
                <div className="text-xs text-text-secondary mt-0.5">{account.name_on_account}</div>
              )}
            </button>

            {openId === account.id && (
              <div className="px-5 pb-4">
                {busy && <p className="text-sm text-text-secondary">Loading…</p>}
                {!busy && statements.length === 0 && (
                  <p className="text-sm text-text-secondary">
                    No statements are held for this account yet.
                  </p>
                )}
                <ul className="divide-y divide-border/60">
                  {statements.map(s => (
                    <li key={s.id} className="py-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
                      <span className="text-sm tabular-nums">
                        {formatDate(s.period_start)} – {formatDate(s.period_end)}
                      </span>
                      <span className="text-xs text-text-secondary tabular-nums">
                        {s.transactions} line{s.transactions === 1 ? '' : 's'}
                      </span>
                      {s.ending_balance !== null && (
                        <span className="text-sm tabular-nums text-text-secondary">
                          closing {money(s.ending_balance, account.currency)}
                        </span>
                      )}
                      {!s.complete && (
                        <span className="text-xs text-amber-700"
                          title="Your attorney's office is still checking the figures on this one.">
                          being checked
                        </span>
                      )}
                      <button type="button" className="ml-auto text-sm text-navy underline"
                        onClick={() => { void openPortalStatementPdf(s.id).catch(e =>
                          setError(e instanceof Error ? e.message : 'That document could not be opened')) }}>
                        Open the document
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        ))}
        {accounts.length === 0 && (
          <p className="px-5 py-8 text-center text-text-secondary">
            No accounts have been added to your case yet.
          </p>
        )}
      </div>
    </div>
  )
}
