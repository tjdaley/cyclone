import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  getMatter, getOpposingParties,
  ingestStatement, getFinancialAccounts, updateFinancialAccount,
  getAccountStatements, getStatementExceptions, reviewStatement,
  getStatementTransactions, getAccountTransactions,
  previewAccountMerge, mergeAccounts,
  deleteStatement, previewAccountDelete, deleteAccount,
} from '../../lib/api'
import { money, isNegative, formatDate } from '../../lib/money'
import TransactionSearchPanel from './TransactionSearchPanel'
import TransactionEditDialog, { CorrectedMark } from './TransactionEditDialog'
import type {
  Matter, OpposingParty,
  FinancialAccount, AccountType, PropertyCharacter, AccountOwnership,
  AccountMergePreview, AccountDeletePreview,
  AccountStatement, StatementReviewStatus, AccountTransaction,
  StatementIngestSummary, StatementJobStatus, ExtractionFlag,
} from '../../types'

const ACCOUNT_TYPES: AccountType[] = [
  'checking', 'savings', 'brokerage', 'credit_card', 'retirement', 'hsa', 'loan', 'other',
]

const CHARACTERS: PropertyCharacter[] = [
  'community', 'separate_petitioner', 'separate_respondent', 'mixed', 'disputed',
]

const ACCOUNT_TYPE_LABEL: Record<AccountType, string> = {
  checking: 'Checking', savings: 'Savings', brokerage: 'Brokerage',
  credit_card: 'Credit card', retirement: 'Retirement', hsa: 'HSA',
  loan: 'Loan', other: 'Other',
}

const CHARACTER_LABEL: Record<PropertyCharacter, string> = {
  community: 'Community',
  separate_petitioner: 'Separate — Petitioner',
  separate_respondent: 'Separate — Respondent',
  mixed: 'Mixed',
  disputed: 'Disputed',
}

const OWNERSHIPS: AccountOwnership[] = [
  'client_sole', 'opposing_sole', 'joint', 'third_party', 'unknown',
]

const OWNERSHIP_LABEL: Record<AccountOwnership, string> = {
  client_sole:   'Our client, solely',
  opposing_sole: 'Other party, solely',
  joint:         'Jointly held',
  third_party:   'A third party',
  unknown:       'Not yet determined',
}

const OWNERSHIP_COLOR: Record<AccountOwnership, string> = {
  client_sole:   'bg-navy/10 text-navy',
  opposing_sole: 'bg-purple-100 text-purple-800',
  // Joint decides how the asset divides, so it gets the loudest chip.
  joint:         'bg-teal-100 text-teal-800',
  third_party:   'bg-gray-100 text-gray-600',
  unknown:       'bg-amber-100 text-amber-800',
}

const REVIEW_COLOR: Record<StatementReviewStatus, string> = {
  auto_accepted: 'bg-green-100 text-green-800',
  accepted:      'bg-green-100 text-green-800',
  needs_review:  'bg-amber-100 text-amber-800',
  rejected:      'bg-gray-100 text-gray-500',
}

const REVIEW_LABEL: Record<StatementReviewStatus, string> = {
  auto_accepted: 'cleared',
  accepted:      'accepted',
  needs_review:  'needs review',
  rejected:      'rejected',
}

function period(s: AccountStatement): string {
  return `${formatDate(s.period_start)} – ${formatDate(s.period_end)}`
}

function accountLabel(a: FinancialAccount): string {
  const tail = a.account_number_last4 ? ` ····${a.account_number_last4}` : ''
  return `${a.institution}${tail}`
}

function Flags({ flags }: { flags: ExtractionFlag[] }) {
  if (flags.length === 0) return null
  return (
    <ul className="mt-2 space-y-1">
      {flags.map((f, i) => (
        <li key={i} className={`text-xs ${f.severity === 'warn' ? 'text-amber-700' : 'text-text-secondary'}`}>
          <span className="font-mono">{f.code}</span>
          {f.field_path && <span className="text-text-secondary"> · {f.field_path}</span>}
          {' — '}{f.note}
        </li>
      ))}
    </ul>
  )
}

/** Reconciliation state as a chip: printed close vs. the sum of the lines. */
function ReconcileBadge({ statement }: { statement: AccountStatement }) {
  if (statement.reconciled) {
    return <span className="text-xs rounded-full px-2 py-0.5 bg-green-100 text-green-800">reconciled</span>
  }
  if (statement.reconciliation_delta === null) {
    return <span className="text-xs rounded-full px-2 py-0.5 bg-gray-100 text-gray-600">not checked</span>
  }
  return (
    <span className="text-xs rounded-full px-2 py-0.5 bg-red-100 text-red-800 tabular-nums">
      off by {money(statement.reconciliation_delta)}
    </span>
  )
}

export default function MatterFinancialsPage() {
  const { matterId: matterIdParam } = useParams<{ matterId: string }>()
  const matterId = Number(matterIdParam)

  const [matter, setMatter] = useState<Matter | null>(null)
  const [parties, setParties] = useState<OpposingParty[]>([])
  const [accounts, setAccounts] = useState<FinancialAccount[]>([])
  const [exceptions, setExceptions] = useState<AccountStatement[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  // Upload
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [dragOver, setDragOver] = useState(false)
  const [job, setJob] = useState<{ status: StatementJobStatus['status']; seconds: number } | null>(null)
  // One entry per dropped file. Statements arrive as a stack of PDFs far more
  // often than one at a time, and taking only files[0] silently discarded the
  // rest — the worst kind of failure, since nothing looked wrong.
  const [queue, setQueue] = useState<{ done: number; total: number; name: string } | null>(null)
  const [summary, setSummary] = useState<StatementIngestSummary | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [batesPrefix, setBatesPrefix] = useState('')

  // Drill-down: one account expanded at a time, one statement inside it.
  const [openAccountId, setOpenAccountId] = useState<number | null>(null)
  const [statements, setStatements] = useState<AccountStatement[]>([])
  const [openStatementId, setOpenStatementId] = useState<number | null>(null)
  const [transactions, setTransactions] = useState<AccountTransaction[]>([])
  const [txLabel, setTxLabel] = useState<string>('')
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState<number | null>(null)
  const [correcting, setCorrecting] = useState<AccountTransaction | null>(null)

  async function refresh() {
    const [accountRows, exceptionRows] = await Promise.all([
      getFinancialAccounts(matterId),
      getStatementExceptions(matterId),
    ])
    setAccounts(accountRows)
    setExceptions(exceptionRows)
  }

  useEffect(() => {
    if (!Number.isFinite(matterId)) { setError('Bad matter id'); setLoading(false); return }
    let cancelled = false
    ;(async () => {
      try {
        const [m, p] = await Promise.all([getMatter(matterId), getOpposingParties(matterId)])
        if (cancelled) return
        setMatter(m)
        setParties(p)
        await refresh()
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load financials')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [matterId])

  const partyName = useMemo(() => {
    const byId = new Map(parties.map(p => [p.id, p.full_name]))
    return (id: number | null) => (id === null ? null : byId.get(id) ?? `party #${id}`)
  }, [parties])

  // ── Upload ─────────────────────────────────────────────────────────────

  /**
   * Ingest a stack of PDFs, one after another.
   *
   * Sequential rather than parallel on purpose: each upload is a queued job
   * doing several LLM calls, and firing ten at once buys nothing while making
   * the progress meaningless and the failures hard to attribute.
   */
  async function handleFiles(files: File[]) {
    const pdfs = files.filter(f => f.type === 'application/pdf')
    const rejected = files.length - pdfs.length
    if (pdfs.length === 0) {
      setUploadError('Only PDF files are accepted')
      return
    }
    setSummary(null)

    // Results accumulate across the whole stack, so one summary covers the drop.
    const combined: StatementIngestSummary = {
      statements_found: 0, auto_accepted: 0, needs_review: 0, results: [], bates: null,
    }
    // Failures are reported the moment they happen, not collected for the end.
    // A dozen statements is the better part of an hour; holding the first
    // file's error until the twelfth finishes means watching a spinner over a
    // run that died at the start, with nothing on screen to say so.
    const failures: string[] = rejected > 0 ? [`Skipped ${rejected} file(s) that were not PDFs`] : []
    setUploadError(failures.length ? failures.join(' · ') : null)

    function fail(message: string) {
      failures.push(message)
      setUploadError(failures.join(' · '))
    }

    for (let i = 0; i < pdfs.length; i++) {
      const file = pdfs[i]
      setQueue({ done: i, total: pdfs.length, name: file.name })
      setJob({ status: 'queued', seconds: 0 })
      try {
        const result = await ingestStatement(
          matterId, file, (status, seconds) => setJob({ status, seconds }), batesPrefix,
        )
        combined.statements_found += result.statements_found
        combined.auto_accepted += result.auto_accepted
        combined.needs_review += result.needs_review
        combined.results.push(...result.results)
        // One Bates series is shown for the drop; with several files it is the
        // first one found, and each statement row carries its own range anyway.
        if (!combined.bates && result.bates) combined.bates = result.bates
        setSummary({ ...combined })
      } catch (err) {
        // One bad PDF must not abandon the rest of the stack.
        fail(`${file.name}: ${err instanceof Error ? err.message : 'failed'}`)
      }

      // Reloading the page's own data gets its own guard. This sat outside the
      // try, so a failing accounts or exceptions call rejected the whole
      // function: the loop stopped, the spinner was never cleared, and no error
      // ever reached the screen. The upload had succeeded — only the redraw
      // failed — which is the most misleading way for this to break.
      try {
        await refresh()
      } catch (err) {
        fail(`Could not refresh the page after ${file.name}: ` +
             `${err instanceof Error ? err.message : 'unknown error'}`)
      }
    }

    setJob(null); setQueue(null)
  }

  // ── Drill-down ─────────────────────────────────────────────────────────

  async function openAccount(account: FinancialAccount) {
    if (openAccountId === account.id) {
      setOpenAccountId(null); setStatements([]); setOpenStatementId(null); setTransactions([])
      return
    }
    setOpenAccountId(account.id); setOpenStatementId(null); setTransactions([]); setBusy(true)
    try {
      setStatements(await getAccountStatements(account.id))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load statements')
    } finally { setBusy(false) }
  }

  async function openStatement(statement: AccountStatement) {
    if (openStatementId === statement.id) { setOpenStatementId(null); setTransactions([]); return }
    setOpenStatementId(statement.id); setBusy(true)
    try {
      setTransactions(await getStatementTransactions(statement.id))
      setTxLabel(period(statement))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load transactions')
    } finally { setBusy(false) }
  }

  /** Every line the account ever printed — what the waste exhibit is built from. */
  async function openWholeHistory(account: FinancialAccount) {
    setOpenStatementId(null); setBusy(true)
    try {
      setTransactions(await getAccountTransactions(account.id))
      setTxLabel(`all periods · ${accountLabel(account)}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load transactions')
    } finally { setBusy(false) }
  }

  /**
   * Discard a statement from the account list.
   *
   * The same operation as rejecting from the exceptions queue. It is reachable
   * here because a statement can look fine on ingest and only later turn out to
   * be a mess — pages scanned out of order, pages missing, no Bates numbers to
   * have caught it by.
   */
  async function discardStatement(statement: AccountStatement) {
    if (!window.confirm(
      'Delete this statement?\n\nIts transactions go with it, and if that leaves the account ' +
      'with nothing, the account goes too. The source PDF stays in storage, so it can be ' +
      'uploaded again.',
    )) return

    setBusy(true)
    try {
      const result = await deleteStatement(statement.id)
      setStatements(prev => prev.filter(s => s.id !== statement.id))
      setExceptions(prev => prev.filter(s => s.id !== statement.id))
      setOpenStatementId(null); setTransactions([])
      if (result.account_deleted) { setOpenAccountId(null); setEditing(null) }
      setNotice(
        `Deleted the statement and ${result.transactions_deleted} transaction` +
        `${result.transactions_deleted === 1 ? '' : 's'}. ` +
        (result.account_deleted
          ? 'The account it created was empty, so that went too.'
          : result.account_kept_reason ? `The account was kept — ${result.account_kept_reason}.` : ''),
      )
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete the statement')
    } finally { setBusy(false) }
  }

  async function decide(statement: AccountStatement, decision: 'accepted' | 'rejected') {
    if (decision === 'rejected' && !window.confirm(
      'Discard this statement?\n\nIts transactions are deleted with it, and if that leaves the ' +
      'account with nothing, the account goes too. This cannot be undone — the source PDF stays ' +
      'in storage, so it can be uploaded again.',
    )) return

    setBusy(true)
    try {
      const result = await reviewStatement(statement.id, decision)
      setExceptions(prev => prev.filter(s => s.id !== statement.id))

      if (result.discarded) {
        const { transactions_deleted, account_deleted, account_kept_reason } = result.discarded
        // Say plainly what went. A silent delete leaves the user guessing
        // whether anything happened beyond the row vanishing.
        setNotice(
          `Discarded the statement and ${transactions_deleted} transaction` +
          `${transactions_deleted === 1 ? '' : 's'}. ` +
          (account_deleted
            ? 'The account it created was empty, so that went too.'
            : account_kept_reason
              ? `The account was kept — ${account_kept_reason}.`
              : ''),
        )
        // The statement and possibly the account are gone; close anything open
        // on them rather than re-rendering against rows that no longer exist.
        setStatements(prev => prev.filter(s => s.id !== statement.id))
        setOpenStatementId(null)
        setTransactions([])
        if (account_deleted) { setOpenAccountId(null); setEditing(null) }
        await refresh()
      } else if (result.statement) {
        setStatements(prev => prev.map(s => (s.id === result.statement!.id ? result.statement! : s)))
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not record the review')
    } finally { setBusy(false) }
  }

  async function saveAccount(accountId: number, patch: Record<string, unknown>) {
    setBusy(true)
    try {
      const updated = await updateFinancialAccount(accountId, patch)
      setAccounts(prev => prev.map(a => (a.id === updated.id ? updated : a)))
      setEditing(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the account')
    } finally { setBusy(false) }
  }

  // ── Render ─────────────────────────────────────────────────────────────

  if (loading) return <div className="px-6 py-8 text-text-secondary">Loading…</div>

  return (
    <div className="px-6 py-8 max-w-6xl mx-auto space-y-6">
      <div>
        <Link to={`/app/matters/${matterId}`} className="text-sm text-navy underline">
          ← {matter?.short_name ?? matter?.matter_name ?? 'Matter'}
        </Link>
        <h1 className="font-display text-3xl text-navy mt-1">Financials</h1>
        <p className="text-text-secondary mt-1">
          Account statements, parsed to the transaction. The source for inventories,
          settlement spreadsheets, and waste exhibits.
        </p>
      </div>

      {error && (
        <div className="card p-3 border border-red-300 bg-red-50 text-sm text-red-700 flex justify-between">
          <span>{error}</span>
          <button type="button" className="underline" onClick={() => setError(null)}>dismiss</button>
        </div>
      )}

      {notice && (
        <div className="card p-3 border border-navy/20 bg-navy/5 text-sm text-navy flex justify-between">
          <span>{notice}</span>
          <button type="button" className="underline" onClick={() => setNotice(null)}>dismiss</button>
        </div>
      )}

      {/* ── Upload ── */}
      <div
        className={`card p-8 text-center border-2 border-dashed transition-colors ${
          job ? 'border-navy bg-navy/5' : dragOver ? 'border-navy bg-navy/5 cursor-pointer' : 'border-border hover:border-navy/40 cursor-pointer'
        }`}
        onDrop={e => {
          e.preventDefault(); setDragOver(false)
          if (!job) handleFiles(Array.from(e.dataTransfer.files))
        }}
        onDragOver={e => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={e => { e.preventDefault(); setDragOver(false) }}
        onClick={() => { if (!job) fileInputRef.current?.click() }}>
        <input ref={fileInputRef} type="file" accept=".pdf" multiple className="hidden"
          onChange={e => {
            if (e.target.files?.length) handleFiles(Array.from(e.target.files))
            e.target.value = ''
          }} />
        {job ? (
          <div>
            <div className="mx-auto mb-3 animate-spin w-8 h-8 border-4 border-navy/20 border-t-navy rounded-full" />
            <p className="text-navy font-medium">
              {job.status === 'queued' ? 'Queued…' : 'Reading the statement…'}
            </p>
            {queue && queue.total > 1 && (
              <p className="text-navy text-sm mt-1 tabular-nums">
                File {queue.done + 1} of {queue.total} · {queue.name}
              </p>
            )}
            <p className="text-text-secondary text-sm mt-1 tabular-nums">
              {job.seconds}s · one pass per statement in the file — a full year takes a few minutes
            </p>
          </div>
        ) : (
          <div>
            <svg className="mx-auto w-10 h-10 text-text-secondary/50 mb-3" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
            </svg>
            <p className="text-navy font-medium">Drop statement PDFs here or click to browse</p>
            <p className="text-text-secondary text-sm mt-1">
              Bank, brokerage, or credit card. Drop as many as you like — they run one at a time.
              Several statements in one file is fine too; each is filed separately.
            </p>
          </div>
        )}
      </div>

      <details className="text-sm">
        <summary className="cursor-pointer text-text-secondary hover:text-navy">
          Bates stamp options
        </summary>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <label className="text-sm text-text-secondary" htmlFor="bates-prefix">Prefix</label>
          <input id="bates-prefix" className="input py-1 w-40 font-mono" placeholder="KF-"
            value={batesPrefix} onChange={e => setBatesPrefix(e.target.value)} />
          <span className="text-xs text-text-secondary max-w-xl">
            Usually leave this empty — the stamp is found by its pattern, because its number advances
            one per page and nothing else on a statement does. Set it only when a document carries two
            competing series, or the wrong one was picked.
          </span>
        </div>
      </details>

      {uploadError && <p className="text-sm text-red-600">{uploadError}</p>}

      {/* ── Ingest result ── */}
      {summary && (
        <div className="card p-5">
          <div className="flex items-baseline justify-between mb-3">
            <h2 className="font-semibold text-navy">
              {summary.statements_found} statement{summary.statements_found === 1 ? '' : 's'} found
            </h2>
            <button type="button" className="text-xs text-navy underline" onClick={() => setSummary(null)}>clear</button>
          </div>

          {summary.bates ? (
            <div className="mb-3 border border-border rounded p-3 text-sm">
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="font-medium text-navy">Bates</span>
                <span className="font-mono text-text-primary">
                  {summary.bates.first} → {summary.bates.last}
                </span>
                <span className="text-xs text-text-secondary tabular-nums">
                  {summary.bates.pages_stamped} page{summary.bates.pages_stamped === 1 ? '' : 's'} stamped
                </span>
                {summary.bates.confidence === 'low' && (
                  <span className="text-xs rounded-full px-2 py-0.5 bg-amber-100 text-amber-800">
                    uncertain — check the prefix
                  </span>
                )}
              </div>
              {summary.bates.unstamped_pages.length > 0 && (
                <p className="text-xs text-text-secondary mt-1.5">
                  No readable stamp on page {summary.bates.unstamped_pages.join(', ')}. Those lines carry
                  no citation — a number is never filled in from the pages around it.
                </p>
              )}
              {summary.bates.gaps.length > 0 && (
                <p className="text-xs text-red-700 mt-1.5">
                  Missing from the run: <span className="font-mono">{summary.bates.gaps.join(', ')}</span>.
                  Those pages are absent from the production.
                </p>
              )}
            </div>
          ) : (
            <p className="text-xs text-text-secondary mb-3">
              No Bates series found — this does not look like a stamped production copy.
            </p>
          )}

          <div className="space-y-1.5">
            {summary.results.map((r, i) => (
              <div key={i} className="flex flex-wrap items-baseline gap-2 text-sm">
                <span className={`text-xs rounded-full px-2 py-0.5 font-medium ${
                  r.status === 'error' ? 'bg-red-100 text-red-800'
                  : r.status === 'duplicate' ? 'bg-gray-100 text-gray-600'
                  : r.status === 'needs_review' ? 'bg-amber-100 text-amber-800'
                  : 'bg-green-100 text-green-800'}`}>
                  {r.status.replace('_', ' ')}
                </span>
                <span className="text-text-primary">{r.institution ?? 'Unidentified institution'}</span>
                {r.period && (
                  <span className="text-xs text-text-secondary">
                    {formatDate(r.period[0])} – {formatDate(r.period[1])}
                  </span>
                )}
                {r.transactions !== null && (
                  <span className="text-xs text-text-secondary tabular-nums">{r.transactions} lines</span>
                )}
                {r.reconciled === false && r.delta && (
                  <span className="text-xs text-red-700 tabular-nums">off by {money(r.delta)}</span>
                )}
                {r.bates_first && (
                  <span className="text-xs text-text-secondary font-mono">
                    {r.bates_first}{r.bates_last && r.bates_last !== r.bates_first ? `–${r.bates_last}` : ''}
                  </span>
                )}
                {r.bates_gaps.length > 0 && (
                  <span className="text-xs text-red-700">
                    missing {r.bates_gaps.join(', ')}
                  </span>
                )}
                {r.error && <span className="text-xs text-red-700">{r.error}</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Exceptions queue ── */}
      {exceptions.length > 0 && (
        <div className="card p-5 border-l-4 border-l-amber-400">
          <h2 className="font-semibold text-navy mb-1">
            Needs review <span className="ml-1 text-sm font-normal text-text-secondary">{exceptions.length}</span>
          </h2>
          <p className="text-sm text-text-secondary mb-3">
            These did not clear on their own — the balances did not tie out, none were printed,
            or the extractor flagged something. Accepting keeps the statement as-is. Rejecting
            deletes it and its transactions, and removes the account if that leaves it empty;
            the source PDF stays in storage, so it can be uploaded again.
          </p>
          <div className="space-y-3">
            {exceptions.map(s => {
              const account = accounts.find(a => a.id === s.financial_account_id)
              return (
                <div key={s.id} className="border border-border rounded p-3">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="text-sm font-medium text-text-primary">
                      {account ? accountLabel(account) : `account #${s.financial_account_id}`}
                    </span>
                    <span className="text-xs text-text-secondary">{period(s)}</span>
                    <ReconcileBadge statement={s} />
                    {/* Which document this came out of. Storage renames the upload
                        to a job id, so without these two the row cannot be tied
                        back to the file on disk or to the import log. */}
                    {s.source_filename && (
                      <span className="text-xs text-text-secondary font-mono" title={s.source_filename}>
                        {s.source_filename}
                      </span>
                    )}
                    {s.bates_first && (
                      <span className="text-xs text-text-secondary font-mono">
                        {s.bates_first}
                        {s.bates_last && s.bates_last !== s.bates_first ? `–${s.bates_last}` : ''}
                      </span>
                    )}
                    <span className="ml-auto flex gap-3">
                      <button type="button" className="text-xs text-navy underline" disabled={busy}
                        onClick={() => decide(s, 'accepted')}>Accept</button>
                      <button type="button" className="text-xs text-red-700 underline" disabled={busy}
                        onClick={() => decide(s, 'rejected')}>Reject and delete</button>
                    </span>
                  </div>
                  <div className="mt-2 grid grid-cols-3 gap-3 text-xs text-text-secondary tabular-nums">
                    <span>opened {money(s.beginning_balance)}</span>
                    <span>printed close {money(s.ending_balance)}</span>
                    <span>computed {money(s.computed_ending_balance)}</span>
                  </div>
                  <Flags flags={s.flags} />
                </div>
              )
            })}
          </div>
        </div>
      )}

      {correcting && (
        <TransactionEditDialog
          transaction={correcting}
          onClose={() => setCorrecting(null)}
          onSaved={async (updated, statement) => {
            setTransactions(prev => prev.map(t => (t.id === updated.id ? updated : t)))
            // An amount change re-reconciles the statement, so the badge and the
            // exceptions queue both have to be told.
            if (statement) {
              setStatements(prev => prev.map(s => (s.id === statement.id ? statement : s)))
              await refresh()
            }
            setCorrecting(null)
          }} />
      )}

      {/* ── Search, categorize, tag ── */}
      <TransactionSearchPanel matterId={matterId} accounts={accounts} />

      {/* ── Accounts ── */}
      <div className="card p-5">
        <h2 className="font-semibold text-navy mb-3">
          Accounts <span className="ml-1 text-sm font-normal text-text-secondary">{accounts.length}</span>
        </h2>
        {accounts.length === 0 ? (
          <p className="text-sm text-text-secondary">
            None yet. Drop a statement above and the account will be created from it.
          </p>
        ) : (
          <div className="space-y-2">
            {accounts.map(a => (
              <div key={a.id} className={`border border-border rounded ${a.is_closed ? 'opacity-60' : ''}`}>
                <div className="flex flex-wrap items-baseline gap-2 p-3">
                  <button type="button" className="text-sm font-medium text-navy underline"
                    onClick={() => openAccount(a)}>
                    {accountLabel(a)}
                  </button>
                  <span className="text-xs rounded-full px-2 py-0.5 bg-off-white text-text-secondary">
                    {ACCOUNT_TYPE_LABEL[a.account_type]}
                  </span>
                  <span className={`text-xs rounded-full px-2 py-0.5 ${OWNERSHIP_COLOR[a.ownership]}`}>
                    {a.ownership === 'joint' && partyName(a.opposing_party_id)
                      ? `Joint with ${partyName(a.opposing_party_id)}`
                      : OWNERSHIP_LABEL[a.ownership]}
                  </span>
                  {a.property_character ? (
                    <span className="text-xs rounded-full px-2 py-0.5 bg-navy/10 text-navy">
                      {CHARACTER_LABEL[a.property_character]}
                    </span>
                  ) : (
                    <span className="text-xs rounded-full px-2 py-0.5 bg-amber-100 text-amber-800">
                      uncharacterized
                    </span>
                  )}
                  {a.name_on_account && <span className="text-xs text-text-secondary">{a.name_on_account}</span>}
                  {partyName(a.opposing_party_id) && (
                    <span className="text-xs text-text-secondary">held by {partyName(a.opposing_party_id)}</span>
                  )}
                  {a.is_closed && <span className="text-xs text-text-secondary">closed</span>}
                  <span className="ml-auto flex gap-3">
                    <button type="button" className="text-xs text-navy underline"
                      onClick={() => openWholeHistory(a)}>All transactions</button>
                    <button type="button" className="text-xs text-navy underline"
                      onClick={() => setEditing(editing === a.id ? null : a.id)}>
                      {editing === a.id ? 'Cancel' : 'Edit'}
                    </button>
                  </span>
                </div>

                {editing === a.id && (
                  <AccountEditor account={a} parties={parties} accounts={accounts} busy={busy}
                    onSave={patch => saveAccount(a.id, patch)}
                    onMerged={async () => {
                      // The merged-away account is gone; close the editor before
                      // refreshing or it re-renders against a row that no longer exists.
                      setEditing(null); setOpenAccountId(null); setStatements([])
                      await refresh()
                    }} />
                )}

                {openAccountId === a.id && (
                  <div className="border-t border-border p-3 space-y-1.5">
                    {statements.length === 0 ? (
                      <p className="text-sm text-text-secondary">No statements filed against this account.</p>
                    ) : statements.map(s => (
                      <div key={s.id} className={`flex flex-wrap items-baseline gap-2 text-sm ${
                        s.review_status === 'rejected' ? 'opacity-50' : ''}`}>
                        <button type="button" className="text-navy underline" onClick={() => openStatement(s)}>
                          {period(s)}
                        </button>
                        <span className={`text-xs rounded-full px-2 py-0.5 font-medium ${REVIEW_COLOR[s.review_status]}`}>
                          {REVIEW_LABEL[s.review_status]}
                        </span>
                        <ReconcileBadge statement={s} />
                        {s.bates_first && (
                          <span className="text-xs text-text-secondary font-mono">
                            {s.bates_first}
                            {s.bates_last && s.bates_last !== s.bates_first ? `–${s.bates_last}` : ''}
                          </span>
                        )}
                        <span className="ml-auto flex items-baseline gap-3">
                          <span className="text-xs text-text-secondary tabular-nums">
                            {money(s.beginning_balance)} → {money(s.ending_balance)}
                          </span>
                          <button type="button" className="text-xs text-red-700 underline" disabled={busy}
                            onClick={() => discardStatement(s)}>Delete</button>
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

      {/* ── Transactions ── */}
      {transactions.length > 0 && (
        <div className="card p-5">
          <div className="flex items-baseline justify-between mb-3">
            <h2 className="font-semibold text-navy">
              Transactions
              <span className="ml-2 text-sm font-normal text-text-secondary">{txLabel}</span>
            </h2>
            <span className="text-sm text-text-secondary tabular-nums">{transactions.length} lines</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-text-secondary border-b border-border">
                  <th className="py-2 pr-3 font-medium">Date</th>
                  <th className="py-2 pr-3 font-medium">Description</th>
                  <th className="py-2 pr-3 font-medium">Counterparty</th>
                  <th className="py-2 pr-3 font-medium text-right">Amount</th>
                  <th className="py-2 pr-3 font-medium text-right">Balance</th>
                  <th className="py-2 font-medium w-12"><span className="sr-only">Correct</span></th>
                </tr>
              </thead>
              <tbody>
                {transactions.map(t => (
                  <tr key={t.id} className="border-b border-border/60 align-top">
                    <td className="py-2 pr-3 whitespace-nowrap tabular-nums">
                      {formatDate(t.transaction_date)}
                      {t.date_provenance !== 'printed' && (
                        <span className="ml-1 text-xs text-amber-700" title="The year was inferred from the statement period, not printed on the line">
                          †
                        </span>
                      )}
                    </td>
                    <td className="py-2 pr-3">
                      {t.description}
                      <CorrectedMark flags={t.flags} />
                      {t.location && <span className="text-xs text-text-secondary"> · {t.location}</span>}
                      {t.flags.length > 0 && <Flags flags={t.flags} />}
                    </td>
                    <td className="py-2 pr-3 text-text-secondary">{t.counterparty ?? '—'}</td>
                    <td className={`py-2 pr-3 text-right whitespace-nowrap tabular-nums ${
                      isNegative(t.amount) ? 'text-text-primary' : 'text-green-700'}`}>
                      {money(t.amount)}
                    </td>
                    <td className="py-2 pr-3 text-right whitespace-nowrap tabular-nums text-text-secondary">
                      {money(t.running_balance)}
                    </td>
                    <td className="py-2 text-right">
                      <button type="button" className="text-xs text-navy underline"
                        onClick={() => setCorrecting(t)}>Edit</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-text-secondary mt-3">
            † date derived from the statement period rather than printed on the line.
            Amounts are signed by their effect on the printed balance.
          </p>
        </div>
      )}
    </div>
  )
}

/**
 * The attorney's half of an account: characterization, ownership, and purpose.
 *
 * Extraction never sets these — they are argued, not read off the page.
 */
function AccountEditor({ account, parties, accounts, busy, onSave, onMerged }: {
  account: FinancialAccount
  parties: OpposingParty[]
  accounts: FinancialAccount[]
  busy: boolean
  onSave: (patch: Record<string, unknown>) => void
  onMerged: () => Promise<void>
}) {
  const [draft, setDraft] = useState({
    institution: account.institution,
    account_type: account.account_type,
    account_number_last4: account.account_number_last4 ?? '',
    name_on_account: account.name_on_account ?? '',
    opposing_party_id: account.opposing_party_id,
    ownership: account.ownership,
    property_character: account.property_character,
    purpose: account.purpose ?? '',
    notes: account.notes ?? '',
    antecedent_account_id: account.antecedent_account_id,
    is_closed: account.is_closed,
  })

  function set<K extends keyof typeof draft>(key: K, value: (typeof draft)[K]) {
    setDraft(prev => ({ ...prev, [key]: value }))
  }

  return (
    <div className="border-t border-border p-3 grid gap-3 md:grid-cols-2">
      <div>
        <label className="label" htmlFor={`inst-${account.id}`}>Institution</label>
        <input id={`inst-${account.id}`} className="input mt-1" value={draft.institution}
          onChange={e => set('institution', e.target.value)} />
      </div>
      <div>
        <label className="label" htmlFor={`type-${account.id}`}>Type</label>
        <select id={`type-${account.id}`} className="input mt-1" value={draft.account_type}
          onChange={e => set('account_type', e.target.value as AccountType)}>
          {ACCOUNT_TYPES.map(t => <option key={t} value={t}>{ACCOUNT_TYPE_LABEL[t]}</option>)}
        </select>
      </div>
      <div>
        <label className="label" htmlFor={`last4-${account.id}`}>Last four</label>
        <input id={`last4-${account.id}`} className="input mt-1" maxLength={4} value={draft.account_number_last4}
          onChange={e => set('account_number_last4', e.target.value)} />
      </div>
      <div>
        <label className="label" htmlFor={`name-${account.id}`}>Name on account</label>
        <input id={`name-${account.id}`} className="input mt-1" value={draft.name_on_account}
          onChange={e => set('name_on_account', e.target.value)} />
      </div>
      <div>
        <label className="label" htmlFor={`own-${account.id}`}>Held by</label>
        <select id={`own-${account.id}`} className="input mt-1" value={draft.ownership}
          onChange={e => set('ownership', e.target.value as AccountOwnership)}>
          {OWNERSHIPS.map(o => <option key={o} value={o}>{OWNERSHIP_LABEL[o]}</option>)}
        </select>
      </div>
      <div>
        <label className="label" htmlFor={`char-${account.id}`}>Characterization</label>
        <select id={`char-${account.id}`} className="input mt-1" value={draft.property_character ?? ''}
          onChange={e => set('property_character', (e.target.value || null) as PropertyCharacter | null)}>
          <option value="">— not yet characterized —</option>
          {CHARACTERS.map(c => <option key={c} value={c}>{CHARACTER_LABEL[c]}</option>)}
        </select>
      </div>
      <div>
        <label className="label" htmlFor={`party-${account.id}`}>
          {draft.ownership === 'joint' ? 'Joint with' : 'Other party'}
        </label>
        <select id={`party-${account.id}`} className="input mt-1" value={draft.opposing_party_id ?? ''}
          onChange={e => set('opposing_party_id', e.target.value ? Number(e.target.value) : null)}
          disabled={draft.ownership === 'client_sole'}>
          <option value="">— none named —</option>
          {parties.map(p => <option key={p.id} value={p.id}>{p.full_name}</option>)}
        </select>
        {(draft.ownership === 'joint' || draft.ownership === 'opposing_sole') && !draft.opposing_party_id && (
          <p className="text-xs text-amber-700 mt-1">Name the other party — division turns on who holds this.</p>
        )}
      </div>
      <div className="md:col-span-2">
        <label className="label" htmlFor={`purpose-${account.id}`}>Purpose</label>
        <input id={`purpose-${account.id}`} className="input mt-1" value={draft.purpose}
          placeholder="Household operating account, business payroll, …"
          onChange={e => set('purpose', e.target.value)} />
      </div>
      <div className="md:col-span-2">
        <label className="label" htmlFor={`ante-${account.id}`}>Succeeds account</label>
        <select id={`ante-${account.id}`} className="input mt-1" value={draft.antecedent_account_id ?? ''}
          onChange={e => set('antecedent_account_id', e.target.value ? Number(e.target.value) : null)}>
          <option value="">— none, this account stands alone —</option>
          {accounts.filter(other => other.id !== account.id).map(other => (
            <option key={other.id} value={other.id}>
              {other.institution}{other.account_number_last4 ? ` ····${other.account_number_last4}` : ''}
            </option>
          ))}
        </select>
        <p className="text-xs text-text-secondary mt-1">
          For a reissued card, a bank migration, or a rollover. Three accounts where one succeeds
          the next look like three half-produced accounts; linked, they read as one history and the
          apparent gaps close.
        </p>
      </div>
      <div className="md:col-span-2">
        <label className="label" htmlFor={`notes-${account.id}`}>Notes</label>
        <textarea id={`notes-${account.id}`} className="input mt-1" rows={2} value={draft.notes}
          onChange={e => set('notes', e.target.value)} />
      </div>
      <div className="md:col-span-2 flex items-center justify-between">
        <label className="flex items-center gap-2 text-sm text-text-secondary">
          <input type="checkbox" checked={draft.is_closed}
            onChange={e => set('is_closed', e.target.checked)} />
          Account is closed
        </label>
        <button type="button" className="btn-primary" disabled={busy}
          onClick={() => onSave({
            ...draft,
            account_number_last4: draft.account_number_last4 || null,
            name_on_account: draft.name_on_account || null,
            purpose: draft.purpose || null,
            notes: draft.notes || null,
          })}>
          Save
        </button>
      </div>

      <div className="md:col-span-2 space-y-2">
        <MergePanel account={account} accounts={accounts} onMerged={onMerged} />
        <DeletePanel account={account} onDeleted={onMerged} />
      </div>
    </div>
  )
}

/**
 * Remove an account and everything filed under it.
 *
 * For a statement imported into the wrong matter, or an account that only
 * exists because an early extraction misread the institution and a clean copy
 * was ingested afterwards.
 *
 * Hard, not soft: the PDFs are still in storage, so a mistake costs a re-import,
 * while a half-deleted account sitting in an inventory is worse than one that is
 * gone. It previews first, because the cascade is the whole point.
 */
function DeletePanel({ account, onDeleted }: {
  account: FinancialAccount
  onDeleted: () => Promise<void>
}) {
  const [preview, setPreview] = useState<AccountDeletePreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function look() {
    setBusy(true); setError(null)
    try {
      setPreview(await previewAccountDelete(account.id))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not check the account')
    } finally { setBusy(false) }
  }

  async function commit() {
    if (!preview) return
    if (!window.confirm(
      `Delete ${preview.account_label}?\n\n${preview.statements} statement(s) and ` +
      `${preview.transactions} transaction(s) go with it. This cannot be undone — the source ` +
      'PDFs stay in storage, so the statements can be uploaded again.',
    )) return
    setBusy(true); setError(null)
    try {
      await deleteAccount(account.id)
      await onDeleted()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete the account')
      setBusy(false)
    }
  }

  return (
    <details className="border border-red-200 rounded p-3" onToggle={e => {
      if ((e.target as HTMLDetailsElement).open && !preview && !busy) look()
    }}>
      <summary className="cursor-pointer text-sm text-red-700 hover:text-red-800">
        Delete this account
      </summary>
      <p className="text-xs text-text-secondary mt-2">
        For a statement imported into the wrong matter, or an account that exists only because an
        extraction misread the institution. Everything filed under it goes.
      </p>

      {busy && !preview && <p className="text-xs text-text-secondary mt-2">checking…</p>}
      {error && <p className="text-xs text-red-700 mt-2">{error}</p>}

      {preview && (
        <div className="mt-3 text-sm">
          <p className="text-text-primary">
            Deletes <span className="tabular-nums font-medium">{preview.statements}</span> statement
            {preview.statements === 1 ? '' : 's'} and{' '}
            <span className="tabular-nums font-medium">{preview.transactions}</span> transaction
            {preview.transactions === 1 ? '' : 's'} from{' '}
            <span className="font-medium">{preview.account_label}</span>.
          </p>
          {preview.periods.length > 0 && (
            <p className="text-xs text-text-secondary mt-1">{preview.periods.join(' · ')}</p>
          )}
          {preview.warnings.length > 0 && (
            <ul className="mt-2 space-y-1">
              {preview.warnings.map((w, i) => (
                <li key={i} className="text-xs text-amber-700">{w}</li>
              ))}
            </ul>
          )}
          <button type="button" className="btn-primary bg-red-700 hover:bg-red-800 mt-3"
            disabled={busy} onClick={commit}>
            Delete account and everything under it
          </button>
        </div>
      )}
    </details>
  )
}

/**
 * Fold this account into another one that is really the same account.
 *
 * This exists because of a specific, common failure: many statements print the
 * institution only in the letterhead graphic, so the first upload files an
 * "Unknown institution" account. Correcting that name afterwards does not
 * retroactively match the next upload — institution plus last four is the dedup
 * key — so the second statement opens a second row for the same real account.
 *
 * Merging moves evidence and deletes a row, so it always previews first.
 */
function MergePanel({ account, accounts, onMerged }: {
  account: FinancialAccount
  accounts: FinancialAccount[]
  onMerged: () => Promise<void>
}) {
  const [targetId, setTargetId] = useState<number | ''>('')
  const [preview, setPreview] = useState<AccountMergePreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const candidates = accounts.filter(a => a.id !== account.id)

  async function look(id: number) {
    setBusy(true); setError(null); setPreview(null)
    try {
      setPreview(await previewAccountMerge(account.id, id))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not check the merge')
    } finally { setBusy(false) }
  }

  async function commit(force: boolean) {
    if (typeof targetId !== 'number') return
    setBusy(true); setError(null)
    try {
      await mergeAccounts(account.id, targetId, force)
      await onMerged()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Merge failed')
      setBusy(false)
    }
  }

  return (
    <details className="border border-border rounded p-3">
      <summary className="cursor-pointer text-sm text-text-secondary hover:text-navy">
        Merge into another account
      </summary>
      <p className="text-xs text-text-secondary mt-2">
        Use this when the same real account was filed twice — usually because the institution could
        not be read the first time. Everything here moves to the account you pick, and this one is
        deleted.
      </p>

      <div className="flex flex-wrap items-center gap-2 mt-2">
        <select className="input py-1 text-sm max-w-sm" value={targetId}
          aria-label="Account to merge into"
          onChange={e => {
            const v = e.target.value ? Number(e.target.value) : ''
            setTargetId(v); setPreview(null)
            if (typeof v === 'number') look(v)
          }}>
          <option value="">— keep which account? —</option>
          {candidates.map(a => (
            <option key={a.id} value={a.id}>
              {a.institution}{a.account_number_last4 ? ` ····${a.account_number_last4}` : ''}
            </option>
          ))}
        </select>
        {busy && <span className="text-xs text-text-secondary">checking…</span>}
      </div>

      {error && <p className="text-xs text-red-700 mt-2">{error}</p>}

      {preview && (
        <div className="mt-3 text-sm">
          <p className="text-text-primary">
            Moves <span className="tabular-nums font-medium">{preview.statements_to_move}</span> statement
            {preview.statements_to_move === 1 ? '' : 's'} and{' '}
            <span className="tabular-nums font-medium">{preview.transactions_to_move}</span> transaction
            {preview.transactions_to_move === 1 ? '' : 's'} onto{' '}
            <span className="font-medium">{preview.target_label}</span>, then deletes{' '}
            <span className="font-medium">{preview.source_label}</span>.
          </p>

          {preview.conflicts.length > 0 && (
            <ul className="mt-2 space-y-1">
              {preview.conflicts.map((c, i) => (
                <li key={i} className={`text-xs ${c.blocking ? 'text-red-700' : 'text-amber-700'}`}>
                  <span className="font-mono">{c.code}</span>
                  {c.blocking && <span className="font-medium"> · blocking</span>}
                  {' — '}{c.detail}
                </li>
              ))}
            </ul>
          )}

          <div className="mt-3">
            {!preview.can_merge ? (
              <p className="text-xs text-red-700">
                This merge cannot proceed until the blocking problem above is resolved.
              </p>
            ) : (
              <button type="button" className="btn-primary" disabled={busy}
                onClick={() => commit(preview.needs_force)}>
                {preview.needs_force ? 'Merge anyway' : 'Merge'}
              </button>
            )}
          </div>
        </div>
      )}
    </details>
  )
}
