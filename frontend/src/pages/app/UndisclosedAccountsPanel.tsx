import { useCallback, useEffect, useState } from 'react'
import {
  getUndisclosedAccounts, exportUndisclosedAccounts, createPayeeClassification,
} from '../../lib/api'
import ExportButtons from '../../components/ExportButtons'
import { money, formatDate } from '../../lib/money'
import type { UndisclosedAccount, ReferencedInstitution, Creditor } from '../../types'

/**
 * True for a numeric string that is zero, without parsing it.
 *
 * One direction is usually empty — an account that only received money has no
 * "out" — and a column of $0.00 reads as a figure rather than as absence.
 */
function isZero(value: string): boolean {
  return /^-?0*\.?0*$/.test(value)
}

/**
 * How a creditor came to be on the list, in the words a reader needs.
 *
 * Every row says this, because the two sources carry different weight: a
 * category is a person's own filing on this matter's evidence, a ruling is a
 * standing firm-wide opinion about a name. A reader deciding what to put in a
 * request for production should be able to tell them apart at a glance.
 */
const REASON: Record<string, { label: string; tone: string }> = {
  liability_category: { label: 'filed as a debt', tone: 'bg-navy/10 text-navy' },
  classified: { label: 'known creditor', tone: 'bg-gold/20 text-yellow-900' },
}

/**
 * Accounts the production talks about but does not produce.
 *
 * Three shapes of evidence, three tables, because collapsing them would
 * misrepresent two. A transfer prints the other ACCOUNT's number. A wire prints
 * the other BANK and its routing number. A payment prints neither — only who
 * was paid — so creditors are keyed on the payee and depend on knowledge held
 * outside the statement.
 *
 * The panel is otherwise a reading surface rather than a workspace: every row
 * is a question to put to the other side, and the answer arrives as a document,
 * not as a click here. The one exception is the triage queue at the foot, which
 * is not a finding at all — it is the list of payees nobody has classified, and
 * sorting it is what makes the creditor table above trustworthy.
 */
export default function UndisclosedAccountsPanel({ matterId }: { matterId: number }) {
  const [rows, setRows] = useState<UndisclosedAccount[]>([])
  const [institutions, setInstitutions] = useState<ReferencedInstitution[]>([])
  const [creditors, setCreditors] = useState<Creditor[]>([])
  const [candidates, setCandidates] = useState<Creditor[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [ruling, setRuling] = useState<string | null>(null)
  const [showQueue, setShowQueue] = useState(false)
  const [exhibitName, setExhibitName] = useState('Accounts Referenced But Not Produced')

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const report = await getUndisclosedAccounts(matterId)
      setRows(report.accounts)
      setInstitutions(report.institutions)
      setCreditors(report.creditors)
      setCandidates(report.candidates)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not scan the transactions')
    } finally {
      setLoading(false)
    }
  }, [matterId])

  useEffect(() => { void load() }, [load])

  /**
   * Rule on a payee and rescan.
   *
   * Scope is the decision that matters here, and it is not the same for the two
   * verdicts. A creditor is nearly always a national brand, so it goes firm-wide
   * and never has to be triaged again. `not_creditor` is scoped to this matter
   * unless the user says otherwise, because suppressing a payee across every
   * case on one paralegal's judgment is how a real account goes missing
   * silently — the failure this whole panel exists to prevent.
   */
  const rule = useCallback(async (
    payee: string,
    classification: 'creditor' | 'not_creditor',
    firmWide: boolean,
  ) => {
    setRuling(payee)
    setError(null)
    try {
      await createPayeeClassification({
        pattern: payee,
        classification,
        matter_id: firmWide ? null : matterId,
      })
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save that ruling')
    } finally {
      setRuling(null)
    }
  }, [matterId, load])

  // The dagger is only worth explaining if something on screen carries one.
  const anyInferred = rows.some(r => r.institution_inferred)
  const nothingFound = rows.length === 0 && institutions.length === 0 && creditors.length === 0

  return (
    <div className="card p-5 space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-display text-navy">Referenced but not produced</h2>
          <p className="text-sm text-text-secondary mt-1 max-w-2xl">
            Accounts named in transfer descriptions on the statements we have, with no
            statement of their own on this matter.
          </p>
        </div>
        <button type="button" className="btn-secondary text-sm shrink-0"
          onClick={() => void load()} disabled={loading}>
          {loading ? 'Scanning…' : 'Rescan'}
        </button>
      </div>

      {error && (
        <div className="p-3 border border-red-300 bg-red-50 text-sm text-red-700 rounded">
          {error}
        </div>
      )}

      {!loading && !error && nothingFound && candidates.length === 0 && (
        <p className="text-sm text-text-secondary py-4">
          Every account mentioned in a transfer, a wire, or a payment is already on this
          matter. That is the result you want — though it only covers the statements
          produced so far.
        </p>
      )}

      {(rows.length > 0 || institutions.length > 0 || creditors.length > 0
        || candidates.length > 0) && (
        <>
        {rows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-text-secondary border-b border-border">
                  <th className="py-2 pr-4 font-medium">Account</th>
                  <th className="py-2 pr-4 font-medium text-right">Lines</th>
                  <th className="py-2 pr-4 font-medium text-right">In</th>
                  <th className="py-2 pr-4 font-medium text-right">Out</th>
                  <th className="py-2 pr-4 font-medium">Activity</th>
                  <th className="py-2 font-medium">Seen on</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(row => {
                  const open = expanded === row.last4
                  return (
                    <tr key={row.last4}
                      className="border-b border-border last:border-0 align-top cursor-pointer hover:bg-off-white"
                      onClick={() => setExpanded(open ? null : row.last4)}>
                      <td className="py-2.5 pr-4">
                        <div className="font-medium text-navy">
                          {row.institution ?? 'Unknown institution'}
                          {' ····'}{row.last4}
                          {row.institution_inferred && (
                            <span className="text-gold ml-0.5" title="Institution inferred, not stated">†</span>
                          )}
                        </div>
                        <div className="text-xs text-text-secondary font-mono mt-0.5">
                          as printed: {row.reference}
                        </div>
                        {open && row.examples.length > 0 && (
                          <ul className="mt-2 space-y-1">
                            {row.examples.map((example, i) => (
                              <li key={i} className="text-xs font-mono text-text-secondary
                                bg-off-white border border-border rounded px-2 py-1">
                                {example}
                              </li>
                            ))}
                          </ul>
                        )}
                      </td>
                      <td className="py-2.5 pr-4 text-right tabular-nums">{row.mentions}</td>
                      <td className="py-2.5 pr-4 text-right tabular-nums text-success">
                        {isZero(row.money_in) ? '—' : money(row.money_in)}
                      </td>
                      <td className="py-2.5 pr-4 text-right tabular-nums text-danger">
                        {isZero(row.money_out) ? '—' : money(row.money_out)}
                      </td>
                      <td className="py-2.5 pr-4 whitespace-nowrap text-text-secondary">
                        {row.first_seen
                          ? `${formatDate(row.first_seen)} – ${formatDate(row.last_seen)}`
                          : '—'}
                      </td>
                      <td className="py-2.5 text-text-secondary">
                        {row.seen_on.join(', ')}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          )}

          {/* Wires name a bank, never an account, so they cannot join the table
              above — but they are the same finding reached another way, and
              usually the larger money. */}
          {institutions.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-sm font-medium text-navy">
                Institutions named by wires, with no account produced
              </h3>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase tracking-wide
                                   text-text-secondary border-b border-border">
                      <th className="py-2 pr-4 font-medium">Institution</th>
                      <th className="py-2 pr-4 font-medium text-right">Wires</th>
                      <th className="py-2 pr-4 font-medium text-right">In</th>
                      <th className="py-2 pr-4 font-medium text-right">Out</th>
                      <th className="py-2 pr-4 font-medium">Activity</th>
                      <th className="py-2 font-medium">Seen on</th>
                    </tr>
                  </thead>
                  <tbody>
                    {institutions.map(row => (
                      <tr key={row.aba ?? row.institution}
                        className="border-b border-border last:border-0 align-top">
                        <td className="py-2.5 pr-4">
                          <div className="font-medium text-navy">{row.institution}</div>
                          {row.aba && (
                            <div className="text-xs text-text-secondary font-mono mt-0.5">
                              ABA {row.aba}
                            </div>
                          )}
                          {/* The finding, not a detail: they sent it to themselves. */}
                          {row.same_party_wires > 0 && (
                            <div className="text-xs text-amber-800 mt-1">
                              {row.same_party_wires} of {row.wires} sent and received by the
                              same party
                            </div>
                          )}
                        </td>
                        <td className="py-2.5 pr-4 text-right tabular-nums">{row.wires}</td>
                        <td className="py-2.5 pr-4 text-right tabular-nums text-success">
                          {isZero(row.money_in) ? '—' : money(row.money_in)}
                        </td>
                        <td className="py-2.5 pr-4 text-right tabular-nums text-danger">
                          {isZero(row.money_out) ? '—' : money(row.money_out)}
                        </td>
                        <td className="py-2.5 pr-4 whitespace-nowrap text-text-secondary">
                          {row.first_seen
                            ? `${formatDate(row.first_seen)} – ${formatDate(row.last_seen)}`
                            : '—'}
                        </td>
                        <td className="py-2.5 text-text-secondary">{row.seen_on.join(', ')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-xs text-text-secondary">
                A wire names the bank that sent it and its routing number, never the account the
                money left — so these identify an institution rather than an account. The routing
                number is the identity, because it is checksummed where a bank's name is not.
              </p>
            </div>
          )}

          {/* A payment names a payee and, almost never, a number. So these are
              keyed on who was paid — a different question from the tables above,
              reaching the same finding. */}
          {creditors.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-sm font-medium text-navy">
                Creditors paid, with no account produced
              </h3>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase tracking-wide
                                   text-text-secondary border-b border-border">
                      <th className="py-2 pr-4 font-medium">Creditor</th>
                      <th className="py-2 pr-4 font-medium text-right">Payments</th>
                      <th className="py-2 pr-4 font-medium text-right">Paid</th>
                      <th className="py-2 pr-4 font-medium">Activity</th>
                      <th className="py-2 font-medium">Paid from</th>
                    </tr>
                  </thead>
                  <tbody>
                    {creditors.map(row => (
                      <tr key={row.payee}
                        className="border-b border-border last:border-0 align-top">
                        <td className="py-2.5 pr-4">
                          <div className="font-medium text-navy">
                            {row.creditor_name ?? row.payee}
                            {/* The number, when a payment happened to print one.
                                Rare, and the strongest form of this row: the
                                creditor and the account from a single line. */}
                            {row.last4.map(digits => (
                              <span key={digits} className="ml-1 font-mono text-xs">
                                ····{digits}
                              </span>
                            ))}
                          </div>
                          <div className="flex items-center gap-2 mt-0.5">
                            <span className={`text-[11px] px-1.5 py-0.5 rounded ${
                              REASON[row.reason]?.tone ?? 'bg-off-white text-text-secondary'}`}>
                              {REASON[row.reason]?.label ?? row.reason}
                            </span>
                            {row.creditor_type && (
                              <span className="text-xs text-text-secondary">
                                {row.creditor_type.replace(/_/g, ' ')}
                              </span>
                            )}
                            <span className="text-xs text-text-secondary font-mono">
                              {row.payee}
                            </span>
                          </div>
                        </td>
                        <td className="py-2.5 pr-4 text-right tabular-nums">{row.payments}</td>
                        <td className="py-2.5 pr-4 text-right tabular-nums text-danger">
                          {money(row.money_out)}
                        </td>
                        <td className="py-2.5 pr-4 whitespace-nowrap text-text-secondary">
                          {row.first_seen
                            ? `${formatDate(row.first_seen)} – ${formatDate(row.last_seen)}`
                            : '—'}
                        </td>
                        <td className="py-2.5 text-text-secondary">{row.seen_on.join(', ')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-xs text-text-secondary">
                A payment prints who was paid, not the account it paid. These identify a
                creditor — and the amount is what it costs to service the debt, which is
                often the number that matters before any statement arrives.
              </p>
            </div>
          )}

          {/* The exhibit behind a motion to compel: the other side's own
              statements naming accounts they did not produce. */}
          <ExportButtons
            name={exhibitName}
            onNameChange={setExhibitName}
            count={rows.length + institutions.length + creditors.length}
            hint="CSV is data only; MD, DOCX and PDF are exhibits with the case caption"
            onExport={format => exportUndisclosedAccounts(
              matterId, format, exhibitName.trim() || 'Accounts Referenced But Not Produced')}
          />

          {/* Deliberately below the export, visually quieter, and collapsed. It
              is a work queue, not a finding: a utility and a card issuer look
              identical here, and presenting them as results would assert of the
              first exactly what it asserts of the second. */}
          {candidates.length > 0 && (
            <div className="border-t border-border pt-3">
              <button type="button"
                className="text-sm text-navy hover:underline"
                onClick={() => setShowQueue(!showQueue)}>
                {showQueue ? '▾' : '▸'} {candidates.length} payee
                {candidates.length === 1 ? '' : 's'} nobody has ruled on
              </button>
              <p className="text-xs text-text-secondary mt-1">
                Payments to counterparties this firm has not classified. Nothing here is a
                finding — a water bill and a credit card read the same on a statement.
                Sorting them once teaches the system for every matter afterwards.
              </p>

              {showQueue && (
                <div className="overflow-x-auto mt-3">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-xs uppercase tracking-wide
                                     text-text-secondary border-b border-border">
                        <th className="py-2 pr-4 font-medium">Payee</th>
                        <th className="py-2 pr-4 font-medium text-right">Payments</th>
                        <th className="py-2 pr-4 font-medium text-right">Paid</th>
                        <th className="py-2 pr-4 font-medium">Activity</th>
                        <th className="py-2 font-medium">This is a…</th>
                      </tr>
                    </thead>
                    <tbody>
                      {candidates.map(row => {
                        const open = expanded === row.payee
                        const busy = ruling === row.payee
                        return (
                          <tr key={row.payee}
                            className="border-b border-border last:border-0 align-top">
                            <td className="py-2.5 pr-4">
                              <button type="button" className="font-medium text-navy text-left"
                                onClick={() => setExpanded(open ? null : row.payee)}>
                                {row.payee}
                              </button>
                              {open && (
                                <ul className="mt-2 space-y-1">
                                  {row.examples.map((example, i) => (
                                    <li key={i} className="text-xs font-mono text-text-secondary
                                      bg-off-white border border-border rounded px-2 py-1">
                                      {example}
                                    </li>
                                  ))}
                                </ul>
                              )}
                            </td>
                            <td className="py-2.5 pr-4 text-right tabular-nums">{row.payments}</td>
                            <td className="py-2.5 pr-4 text-right tabular-nums text-danger">
                              {money(row.money_out)}
                            </td>
                            <td className="py-2.5 pr-4 whitespace-nowrap text-text-secondary">
                              {row.first_seen
                                ? `${formatDate(row.first_seen)} – ${formatDate(row.last_seen)}`
                                : '—'}
                            </td>
                            <td className="py-2.5 whitespace-nowrap">
                              <button type="button" disabled={busy}
                                className="btn-secondary text-xs mr-2"
                                title="A card issuer, lender, or servicer. Saved firm-wide —
                                       it is the same creditor in every case."
                                onClick={() => void rule(row.payee, 'creditor', true)}>
                                Creditor
                              </button>
                              <button type="button" disabled={busy}
                                className="btn-secondary text-xs"
                                title="A vendor. Hidden on this matter; nothing is hidden
                                       from other cases on one judgment."
                                onClick={() => void rule(row.payee, 'not_creditor', false)}>
                                Vendor
                              </button>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}


          {/* The reasoning behind the list, stated where the list is read. An
              inference presented without its basis is just an assertion. */}
          <div className="text-xs text-text-secondary space-y-1 pt-1 border-t border-border">
            {anyInferred && (
              <p>
                <span className="text-gold">†</span>{' '}
                Institution inferred: the description named an account number but no bank,
                so it is assumed to be held at the institution whose statement it was
                printed on. Verify before relying on it.
              </p>
            )}
            <p>
              Direction is taken from the sign of each amount, not from the words
              &ldquo;to&rdquo; and &ldquo;from&rdquo; — a description often carries both.
              Accounts are matched on their last four digits.
            </p>
            <p>
              Click a row for the descriptions these figures came from. This is a list of
              questions to ask, not findings: confirm each against the statements before
              it goes into a request or a pleading.
            </p>
          </div>
        </>
      )}
    </div>
  )
}
