import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  getGuestAccounts, getGuestStatements, getGuestStatementTransactions, openGuestStatementPdf,
} from '../../lib/api'
import { formatDate, isNegative, money } from '../../lib/money'
import StatementPdfButton from '../../components/StatementPdfButton'
import type {
  AccountOwnership, AccountType, GuestAccount, GuestStatement, GuestTransaction, PropertyCharacter,
} from '../../types'

const TYPE_LABEL: Record<AccountType, string> = {
  checking: 'Checking', savings: 'Savings', brokerage: 'Brokerage', crypto: 'Crypto',
  credit_card: 'Credit card', loan: 'Loan', retirement: 'Retirement',
  life_insurance: 'Life insurance', annuity: 'Annuity', hsa: 'HSA', other: 'Other',
}
const OWNERSHIP_LABEL: Record<AccountOwnership, string> = {
  client_sole: 'The client, solely', opposing_sole: 'The other party, solely',
  joint: 'Jointly held', third_party: 'A third party', unknown: 'Not yet determined',
}
const CHARACTER_LABEL: Record<PropertyCharacter, string> = {
  community: 'Community', separate_petitioner: 'Separate — Petitioner',
  separate_respondent: 'Separate — Respondent', mixed: 'Mixed', disputed: 'Disputed',
  no_marital_interest: 'No marital property interest',
}

/**
 * Accounts, their statements, and a statement's lines. Read-only.
 *
 * The same drill-down the firm uses, with the same highlighting so a long list
 * of lines can be scrolled back from: the open account in a stronger yellow,
 * the open statement in a paler one.
 */
export default function GuestAccountsPage() {
  const matterId = Number(useParams().matterId)
  const [accounts, setAccounts] = useState<GuestAccount[]>([])
  const [openAccount, setOpenAccount] = useState<GuestAccount | null>(null)
  const [statements, setStatements] = useState<GuestStatement[]>([])
  const [openStatement, setOpenStatement] = useState<GuestStatement | null>(null)
  const [lines, setLines] = useState<GuestTransaction[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    getGuestAccounts(matterId).then(setAccounts)
      .catch(e => setError(e instanceof Error ? e.message : 'Could not load the accounts'))
  }, [matterId])

  async function toggleAccount(account: GuestAccount) {
    setOpenStatement(null); setLines([])
    if (openAccount?.id === account.id) { setOpenAccount(null); setStatements([]); return }
    setOpenAccount(account); setBusy(true)
    try { setStatements(await getGuestStatements(account.id)) }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not load the statements') }
    finally { setBusy(false) }
  }

  async function toggleStatement(statement: GuestStatement) {
    if (openStatement?.id === statement.id) { setOpenStatement(null); setLines([]); return }
    setOpenStatement(statement); setBusy(true)
    try { setLines(await getGuestStatementTransactions(statement.id)) }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not load the transactions') }
    finally { setBusy(false) }
  }

  const currency = openAccount?.currency

  return (
    <div className="space-y-6">
      {error && <p className="card p-4 text-sm text-red-700">{error}</p>}

      <div className="card p-5">
        <h2 className="font-semibold text-navy mb-3">
          Accounts <span className="ml-1 text-sm font-normal text-text-secondary">{accounts.length}</span>
        </h2>
        {accounts.length === 0 ? (
          <p className="text-sm text-text-secondary">No accounts on this matter yet.</p>
        ) : (
          <div className="space-y-2">
            {accounts.map(a => (
              <div key={a.id} className={`border rounded ${
                openAccount?.id === a.id ? 'border-yellow-400' : 'border-border'} ${
                a.is_closed ? 'opacity-60' : ''}`}>
                <div className={`flex flex-wrap items-baseline gap-2 p-3 rounded-t ${
                  openAccount?.id === a.id ? 'bg-yellow-200' : ''}`}>
                  <button type="button" className="text-sm font-medium text-navy underline"
                    onClick={() => toggleAccount(a)}>
                    {a.institution}{a.account_number_last4 ? ` ····${a.account_number_last4}` : ''}
                  </button>
                  <span className="text-xs rounded-full px-2 py-0.5 bg-off-white text-text-secondary">
                    {TYPE_LABEL[a.account_type]}
                  </span>
                  <span className="text-xs rounded-full px-2 py-0.5 bg-off-white text-text-secondary">
                    {OWNERSHIP_LABEL[a.ownership]}
                  </span>
                  {a.property_character && (
                    <span className="text-xs rounded-full px-2 py-0.5 bg-navy/10 text-navy">
                      {CHARACTER_LABEL[a.property_character]}
                    </span>
                  )}
                  {a.currency !== 'USD' && (
                    <span className="text-xs rounded-full px-2 py-0.5 bg-sky-100 text-sky-800 font-mono">
                      {a.currency}
                    </span>
                  )}
                  {a.name_on_account && <span className="text-xs text-text-secondary">{a.name_on_account}</span>}
                  {a.is_closed && <span className="text-xs text-text-secondary">closed</span>}
                </div>

                {openAccount?.id === a.id && (
                  <div className="border-t border-border p-3 space-y-1.5">
                    {busy && statements.length === 0 && <p className="text-sm text-text-secondary">Loading…</p>}
                    {!busy && statements.length === 0 && (
                      <p className="text-sm text-text-secondary">No statements are held for this account.</p>
                    )}
                    {statements.map(s => (
                      <div key={s.id} className={`flex flex-wrap items-baseline gap-2 text-sm -mx-1.5 px-1.5 py-0.5 rounded ${
                        openStatement?.id === s.id ? 'bg-yellow-100' : ''}`}>
                        <button type="button" className="text-navy underline" onClick={() => toggleStatement(s)}>
                          {formatDate(s.period_start)} – {formatDate(s.period_end)}
                        </button>
                        {s.has_pdf && (
                          <StatementPdfButton statementId={s.id} opener={openGuestStatementPdf} />
                        )}
                        <span className={`text-xs rounded-full px-2 py-0.5 ${s.reconciled
                          ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'}`}>
                          {s.reconciled ? 'ties to its balances' : 'does not tie'}
                        </span>
                        {s.bates_first && (
                          <span className="text-xs text-text-secondary font-mono">
                            {s.bates_first}{s.bates_last && s.bates_last !== s.bates_first ? `–${s.bates_last}` : ''}
                          </span>
                        )}
                        <span className="ml-auto text-xs text-text-secondary tabular-nums">
                          {money(s.beginning_balance, a.currency)} → {money(s.ending_balance, a.currency)}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {openStatement && (
        <div className="card p-5">
          <h2 className="font-semibold text-navy mb-3">
            Transactions
            <span className="ml-2 text-sm font-normal text-text-secondary">
              {formatDate(openStatement.period_start)} – {formatDate(openStatement.period_end)}
              {' · '}{lines.length} lines
            </span>
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-text-secondary border-b border-border">
                  <th className="py-2 pr-3 font-medium">Date</th>
                  <th className="py-2 pr-3 font-medium">Description</th>
                  <th className="py-2 pr-3 font-medium">Counterparty</th>
                  <th className="py-2 pr-3 font-medium">Bates</th>
                  <th className="py-2 pr-3 font-medium text-right">Amount</th>
                  <th className="py-2 font-medium text-right">Balance</th>
                </tr>
              </thead>
              <tbody>
                {lines.map(t => (
                  <tr key={t.id} className="border-b border-border/60 align-top">
                    <td className="py-2 pr-3 whitespace-nowrap tabular-nums">{formatDate(t.transaction_date)}</td>
                    <td className="py-2 pr-3">
                      {t.description}
                      {t.check_number && <span className="text-xs text-text-secondary"> · check {t.check_number}</span>}
                      {t.location && <span className="text-xs text-text-secondary"> · {t.location}</span>}
                    </td>
                    <td className="py-2 pr-3 text-text-secondary">{t.counterparty ?? '—'}</td>
                    <td className="py-2 pr-3 text-xs font-mono text-text-secondary">{t.bates_number ?? '—'}</td>
                    <td className={`py-2 pr-3 text-right whitespace-nowrap tabular-nums ${
                      isNegative(t.amount) ? 'text-text-primary' : 'text-green-700'}`}>
                      {money(t.amount, currency)}
                    </td>
                    <td className="py-2 text-right whitespace-nowrap tabular-nums text-text-secondary">
                      {money(t.running_balance, currency)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-text-secondary mt-3">
            Amounts are signed by their effect on the balance the institution printed.
          </p>
        </div>
      )}
    </div>
  )
}
