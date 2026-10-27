import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import {
  MAX_DIRECT_POST_MB, belongsInReceivedDocuments, isTooLargeError,
  tooLargeToUpload, uploadLimitMB, uploadRejectedMessage,
} from '../../lib/uploadLimit'
import {
  getMatter, getOpposingParties,
  queueStatement, awaitStatementJob, retryStatement, setStatementBoundary,
  correctStatementBalances,
  setStatementBates,
  getFinancialAccounts, updateFinancialAccount,
  getAccountStatements, getStatementExceptions, reviewStatement,
  getStatementTransactions, getAccountTransactions,
  previewAccountMerge, mergeAccounts,
  deleteStatement, previewAccountDelete, deleteAccount,
} from '../../lib/api'
import { money, isNegative, formatDate } from '../../lib/money'
import TransactionSearchPanel from './TransactionSearchPanel'
import UndisclosedAccountsPanel from './UndisclosedAccountsPanel'
import FisPanel from './FisPanel'
import CompliancePanel from './CompliancePanel'
import InventoryPanel from './InventoryPanel'
import NormalizedStatementButton from '../../components/NormalizedStatementButton'
import StatementPdfButton from '../../components/StatementPdfButton'
import TransactionEditDialog, { CorrectedMark } from './TransactionEditDialog'
import TransactionAddDialog from './TransactionAddDialog'
import AccountAddDialog from './AccountAddDialog'
import ClientTagReviewPanel from './ClientTagReviewPanel'
import type {
  Matter, OpposingParty,
  FinancialAccount, AccountType, PropertyCharacter, AccountOwnership,
  AccountMergePreview, AccountDeletePreview,
  AccountStatement, StatementReviewStatus, AccountTransaction,
  StatementIngestSummary, StatementJobStatus, ExtractionFlag, StatementBoundary,
  ProductionResponsibility,
} from '../../types'

// Dropdown order: what the money sits in, then what is owed, then what is
// locked away -- the reading order of a property inventory, matching
// compliance_service._TYPE_ORDER rather than alphabetical.
const ACCOUNT_TYPES: AccountType[] = [
  'checking', 'savings', 'brokerage', 'crypto', 'credit_card', 'loan',
  'retirement', 'life_insurance', 'annuity', 'hsa', 'other',
]

/**
 * Disabled has to LOOK disabled.
 *
 * These are text links (`text-xs text-navy underline`), and the browser renders
 * a disabled one identically to a live one — same colour, same underline, same
 * cursor. So a page-wide `busy` silently made every Accept/Retry/Reject inert
 * with no signal whatsoever, and the natural response was to click Retry again,
 * and again, on row after row. That is the same failure the payee queue had:
 * **a disabled button looks exactly like one nobody pressed.**
 *
 * `no-underline` is what carries it. Opacity alone still reads as a link.
 */
const linkBusy = 'disabled:opacity-40 disabled:no-underline disabled:cursor-not-allowed'

/** Why a row went inert. Shown on hover of anything `busy` disabled. */
const RETRY_WAIT =
  'Waiting — a re-read is running. One upload can hold several statements, so ' +
  'the rows here are being discarded and recreated; acting on one now would act ' +
  'on a row that is about to be replaced.'

const CHARACTERS: PropertyCharacter[] = [
  'community', 'separate_petitioner', 'separate_respondent', 'mixed', 'disputed',
]

// Typed `Record<AccountType, string>` deliberately: adding a member to the
// union breaks this until it has a label, which is what stops another type
// shipping unlabelled the way `crypto` did between 036 and 038.
const ACCOUNT_TYPE_LABEL: Record<AccountType, string> = {
  checking: 'Checking', savings: 'Savings', brokerage: 'Brokerage',
  crypto: 'Crypto', credit_card: 'Credit card', loan: 'Loan',
  retirement: 'Retirement', life_insurance: 'Life insurance',
  annuity: 'Annuity', hsa: 'HSA', other: 'Other',
}

const CHARACTER_LABEL: Record<PropertyCharacter, string> = {
  community: 'Community',
  separate_petitioner: 'Separate — Petitioner',
  separate_respondent: 'Separate — Respondent',
  mixed: 'Mixed',
  disputed: 'Disputed',
  // Not a kind of separate property: separate property is a party's own and
  // still gets inventoried. This is a third party's account the production
  // named in passing, and it leaves the compliance grid and the inventory.
  no_marital_interest: 'No marital property interest',
}

/**
 * The three jobs this page does.
 *
 * They were stacked on one page, which meant every one of them paid the scroll
 * cost of the other two — after an import you passed two hundred transaction
 * rows to reach the account list. They share the matter and the account list
 * and nothing else: importing is an event, curating accounts is bookkeeping,
 * and searching is analysis.
 */
type Tab = 'import' | 'accounts' | 'inventory' | 'compliance' | 'transactions' | 'fis'

const TABS: { id: Tab; label: string }[] = [
  { id: 'import', label: 'Import' },
  { id: 'accounts', label: 'Accounts' },
  { id: 'inventory', label: 'Inventory' },
  { id: 'compliance', label: 'Compliance' },
  { id: 'transactions', label: 'Transactions' },
  { id: 'fis', label: 'FIS' },
]

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

const RESPONSIBILITIES: ProductionResponsibility[] = ['unknown', 'client', 'opposing', 'both']

/**
 * Who must produce statements for an account.
 *
 * Deliberately not derived from "Held by" above. They usually agree and
 * sometimes do not — a joint account both sides were ordered to produce, or an
 * account the other party holds whose statements we have and they do not.
 * Ownership decides how an asset divides; this decides whose motion to compel
 * it is, and which compliance report the account appears on.
 */
const RESPONSIBILITY_LABEL: Record<ProductionResponsibility, string> = {
  unknown:  'Not yet decided',
  client:   'We produce it',
  opposing: 'They produce it',
  both:     'Both sides ordered to',
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

  // Kept in the URL rather than in state alone: a refresh should land you back
  // where you were, and a link to a matter's transactions should be a link.
  const [searchParams, setSearchParams] = useSearchParams()
  const tab: Tab = (TABS.find(t => t.id === searchParams.get('tab'))?.id) ?? 'import'
  function goTo(next: Tab) {
    setSearchParams(prev => {
      const params = new URLSearchParams(prev)
      params.set('tab', next)
      return params
    }, { replace: true })
  }

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
  // Which half of the work is running. The upload phase is short but not
  // instant — a dozen files is ten to fifteen seconds — and it used to render
  // nothing at all, so the screen looked exactly as it had before the drop.
  const [phase, setPhase] = useState<'uploading' | 'reading' | null>(null)
  // A ref, not state: the guard has to hold on the same tick as the drop, and
  // state does not update until the next render.
  const uploadingRef = useRef(false)
  const [job, setJob] = useState<{ status: StatementJobStatus['status']; seconds: number } | null>(null)
  // One entry per dropped file. Statements arrive as a stack of PDFs far more
  // often than one at a time, and taking only files[0] silently discarded the
  // rest — the worst kind of failure, since nothing looked wrong.
  const [queue, setQueue] = useState<{ done: number; total: number; name: string } | null>(null)
  // Files past the automatic import's capacity. They are STORED and offered to
  // the splitting bench rather than refused: the refusal this replaces was
  // right about the import and had nothing to offer instead.
  const [toSplit, setToSplit] = useState<
    { name: string; sourceDocumentId: number; reason: string }[]>([])
  // Learned from the server so there is no second copy of the number here.
  // Null means it could not be learned, and then nothing is pre-checked:
  // nginx still enforces, and a 413 carries the same advice.
  const [maxUploadMB, setMaxUploadMB] = useState<number | null>(null)
  // Set when a dropped file was too large for this page's upload path, so the
  // other door can be offered rather than merely named in an error.
  const [useReceived, setUseReceived] = useState(false)
  const [summary, setSummary] = useState<StatementIngestSummary | null>(null)
  // A LIST, not one joined string. A refused upload now comes back with a
  // paragraph explaining what to do about it, and three of those run together
  // with separators is a wall nobody reads.
  const [uploadError, setUploadError] = useState<string[]>([])
  // Advice, not failure — kept apart from uploadError so a warning about a
  // long PDF never reads as "the upload broke". These accumulate across a
  // dropped stack and stay on screen after it finishes, because the whole
  // point is to be read once the result looks wrong.
  const [uploadAdvice, setUploadAdvice] = useState<string[]>([])
  const [batesPrefix, setBatesPrefix] = useState('')

  // Drill-down: one account expanded at a time, one statement inside it.
  const [openAccountId, setOpenAccountId] = useState<number | null>(null)
  const [statements, setStatements] = useState<AccountStatement[]>([])
  const [openStatementId, setOpenStatementId] = useState<number | null>(null)
  const [transactions, setTransactions] = useState<AccountTransaction[]>([])
  const [txLabel, setTxLabel] = useState<string>('')
  /**
   * The statement being viewed, when exactly one is. Undefined in the
   * whole-account history, where a line has no single document to belong to.
   */
  const viewedStatement = statements.find(s => s.id === openStatementId)
  const [busy, setBusy] = useState(false)
  // Which statement's Retry is running, so its own row can say so while the
  // others explain why they went quiet. `busy` alone cannot: it is page-wide,
  // so every row looks equally stopped and none of them says which one is the
  // reason. See the `linkBusy` class and RETRY_WAIT below.
  const [retrying, setRetrying] = useState<number | null>(null)
  const [editing, setEditing] = useState<number | null>(null)
  const [correcting, setCorrecting] = useState<AccountTransaction | null>(null)
  /** The statement a line is being added to. Null unless the dialog is open. */
  const [addingTo, setAddingTo] = useState<AccountStatement | null>(null)
  /** Open when an account is being added by hand; carries any prefill. */
  /** The statement whose Bates numbers are being typed in. */
  const [batesFor, setBatesFor] = useState<AccountStatement | null>(null)
  /** The statement whose opening or closing balance is being corrected. */
  const [balancesFor, setBalancesFor] = useState<AccountStatement | null>(null)
  const [addingAccount, setAddingAccount] = useState<
    null | { institution?: string | null; account_number_last4?: string | null;
             account_number_masked?: string | null }>(null)

  async function refresh() {
    const [accountRows, exceptionRows] = await Promise.all([
      getFinancialAccounts(matterId),
      getStatementExceptions(matterId),
    ])
    setAccounts(accountRows)
    setExceptions(exceptionRows)
  }

  // The limit comes from the server so there is no second copy of the
  // number in the client. Null means it could not be learned, and then
  // nothing is pre-checked -- a guessed fallback would start refusing
  // files the server would have taken.
  useEffect(() => { void uploadLimitMB().then(setMaxUploadMB) }, [])

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
   * Ingest a stack of PDFs: upload them all, then watch them all.
   *
   * **Re-entry is refused on a ref, not on state.** React state does not update
   * until the next render, so two drops in quick succession both read the old
   * value and both run — which is exactly what happened: a dozen files dropped
   * twice produced twenty-four jobs, twelve of them recognised as duplicates
   * after the fact. The ref is set on the same tick as the drop, so the second
   * one cannot get past it.
   */
  async function handleFiles(files: File[]) {
    if (uploadingRef.current) {
      // Say so rather than dropping it silently. Somebody who re-drags is
      // already unsure the first one landed; ignoring them without a word is
      // what made them do it in the first place. The two phases want different
      // words — the reading one runs for minutes, and "still uploading" would
      // read as stuck.
      setNotice(phase === 'reading'
        ? 'Those files were not uploaded — the previous batch is still being read. '
          + 'Wait for it to finish, then drop them again.'
        : 'Those files were ignored — the previous drop is still uploading. '
          + 'Every file from it is being sent.')
      return
    }
    const pdfs = files.filter(f => f.type === 'application/pdf')
    const rejected = files.length - pdfs.length
    if (pdfs.length === 0) {
      setUploadError(['Only PDF files are accepted'])
      return
    }

    // Before the first await, so the drop zone changes on the same tick as the
    // drop. It used to switch only once every upload had finished — ten to
    // fifteen seconds of a screen that looked like nothing had happened.
    uploadingRef.current = true
    setPhase('uploading')
    setQueue({ done: 0, total: pdfs.length, name: '' })
    setNotice(null)
    setSummary(null)
    setUploadAdvice([])

    try {
      await ingestStack(pdfs, rejected)
    } finally {
      // Whatever happened, the drop zone comes back. A ref left true would
      // disable it for the life of the page, and the only cure would be a
      // reload nobody would know to try.
      setJob(null); setQueue(null); setPhase(null); setToSplit([]); setUseReceived(false)
      uploadingRef.current = false
    }
  }

  /**
   * Send every file, then watch every job.
   *
   * Split out so `handleFiles` can own the re-entry guard and release it in a
   * `finally` without this whole body sitting inside a try block.
   */
  async function ingestStack(pdfs: File[], rejected: number) {
    // Results accumulate across the whole stack, so one summary covers the drop.
    const combined: StatementIngestSummary = {
      statements_found: 0, auto_accepted: 0, needs_review: 0, results: [], bates: null,
    }
    // Failures are reported the moment they happen, not collected for the end.
    // A dozen statements is the better part of an hour; holding the first
    // file's error until the twelfth finishes means watching a spinner over a
    // run that died at the start, with nothing on screen to say so.
    const failures: string[] = rejected > 0 ? [`Skipped ${rejected} file(s) that were not PDFs`] : []
    setUploadError([...failures])

    function fail(message: string) {
      failures.push(message)
      setUploadError([...failures])
    }

    const advice: string[] = []
    function advise(notes: string[]) {
      advice.push(...notes)
      setUploadAdvice([...advice])
    }

    // HAND THE WHOLE STACK OVER BEFORE WAITING ON ANY OF IT.
    //
    // The upload is a second; the read is minutes, and the worker runs several
    // at once. Uploading one and waiting for it before sending the next left
    // that pool with exactly one job to run however many files were dropped —
    // so a year of statements took the sum of thirteen reads rather than the
    // longest few, and the only way to make it finish was to merge them into a
    // single enormous PDF, which is slower still.
    const queued: { file: File; jobId: string }[] = []
    const routed: { name: string; sourceDocumentId: number; reason: string }[] = []
    for (const file of pdfs) {
      // REFUSED BEFORE A BYTE IS SENT. nginx rejects an oversized body
      // mid-flight, so without this the user waits out the whole upload to be
      // shown a raw "413" they cannot act on.
      const tooBig = tooLargeToUpload(file, maxUploadMB)
      if (tooBig) {
        fail(tooBig)
        continue
      }
      // Not refused — routed. This page POSTs the file through the API, which
      // moves the bytes twice inside one request; past the size that path is
      // known to carry, Received documents uploads straight to storage.
      const elsewhere = belongsInReceivedDocuments(file)
      if (elsewhere) {
        setUseReceived(true)
        fail(elsewhere)
        continue
      }
      setQueue({ done: queued.length, total: pdfs.length, name: file.name })
      try {
        const job = await queueStatement(matterId, file, batesPrefix)

        // Too large for the automatic import, so a person splits it. Nothing
        // was queued, so there is no job to poll — and the file IS stored, so
        // this is an offer rather than a loss.
        if (job.status === 'routed_to_bench' || !job.id) {
          routed.push({
            name: file.name,
            sourceDocumentId: job.source_document_id ?? 0,
            reason: job.route_reason ?? 'This document needs splitting by hand.',
          })
          continue
        }
        queued.push({ file, jobId: job.id })
        // Shown as soon as the upload lands, which is seconds — long before the
        // read finishes. It does not stop the ingest: a document holding
        // several statements is legal input, and the reader is being told what
        // to do if this one comes out wrong, not being refused.
        if (job.warnings.length) {
          advise(pdfs.length > 1
            ? job.warnings.map(w => `${file.name}: ${w}`)
            : job.warnings)
        }
      } catch (err) {
        // One bad PDF must not abandon the rest of the stack.
        fail(isTooLargeError(err)
          ? uploadRejectedMessage(file.name, maxUploadMB)
          : `${file.name}: ${err instanceof Error ? err.message : 'failed'}`)
      }
    }

    setToSplit(routed)
    if (queued.length === 0) {
      // Nothing to read: every file went to the bench. Saying so beats leaving
      // a reading spinner on a queue of none.
      setQueue(null)
      setPhase(null)
      return
    }

    let finished = 0
    setPhase('reading')
    setQueue({ done: 0, total: queued.length, name: '' })
    setJob({ status: 'queued', seconds: 0 })

    // Every result is folded in as it lands. JavaScript is single-threaded, so
    // these callbacks cannot interleave mid-update — the counters are safe
    // without any coordination.
    await Promise.all(queued.map(async ({ file, jobId }) => {
      try {
        const result = await awaitStatementJob(
          jobId, (status, seconds) => setJob({ status, seconds }),
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
        fail(`${file.name}: ${err instanceof Error ? err.message : 'failed'}`)
      } finally {
        finished += 1
        setQueue({ done: finished, total: queued.length, name: '' })
      }
    }))

    // Refreshed once at the end rather than after each file. This gets its own
    // guard because it used to sit inside the loop's try: a failing accounts or
    // exceptions call rejected the whole function, the spinner was never
    // cleared, and no error reached the screen — the upload had succeeded and
    // only the redraw failed, which is the most misleading way for this to
    // break.
    try {
      await refresh()
    } catch (err) {
      fail(`Could not refresh the page: ${err instanceof Error ? err.message : 'unknown error'}`)
    }

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

  /**
   * Discard what an upload produced and read the PDF again.
   *
   * The confirmation names the whole document rather than the statement,
   * because that is what goes: one PDF can hold several accounts, and re-reading
   * it recreates every one of them, so the siblings have to be discarded too or
   * each account ends up filed twice. Somebody clicking Retry on one row needs
   * to know that before they click, not after.
   */
  async function retry(statement: AccountStatement) {
    const file = statement.source_filename ? `"${statement.source_filename}"` : 'this document'
    if (!window.confirm(
      `Read ${file} again from the start?\n\n` +
      'Everything that upload produced is discarded first — every statement from it, not ' +
      'just this one, along with their transactions and any account left empty. The PDF is ' +
      'then re-read, which recreates them.\n\n' +
      'If the PDF can no longer be retrieved, nothing is deleted.',
    )) return

    setBusy(true)
    setRetrying(statement.id)
    // On the SAME TICK as the click. Setting this later meant the upload panel
    // stayed idle through retryStatement() and refresh() -- two round trips of
    // a page that looks untouched, which is the window somebody fills by
    // clicking again.
    setPhase('reading')
    setError(null)
    try {
      const result = await retryStatement(statement.id)
      setNotice(
        `Discarded ${result.statements_discarded} statement` +
        `${result.statements_discarded === 1 ? '' : 's'} and ` +
        `${result.transactions_discarded} transaction` +
        `${result.transactions_discarded === 1 ? '' : 's'}` +
        (result.accounts_deleted
          ? `, and ${result.accounts_deleted} emptied account` +
            `${result.accounts_deleted === 1 ? '' : 's'}`
          : '') +
        '. The document is being read again — this page updates when it finishes.',
      )
      setOpenStatementId(null)
      setOpenAccountId(null)
      setEditing(null)
      setTransactions([])
      await refresh()
      // The re-read runs as a job and takes as long as the original did, so the
      // page shows the same progress it shows for an upload. Without this the
      // rows simply vanish and nothing says they are coming back.
      setJob({ status: 'queued', seconds: 0 })
      try {
        const summary = await awaitStatementJob(
          result.job_id, (status, seconds) => setJob({ status, seconds }),
        )
        setSummary(summary)
        setNotice(
          `Read ${file} again: ${summary.statements_found} statement` +
          `${summary.statements_found === 1 ? '' : 's'}, ` +
          `${summary.needs_review} still needing review.`,
        )
      } catch (err) {
        setError(
          'The document was discarded but the re-read did not finish: ' +
          `${err instanceof Error ? err.message : 'unknown error'}. ` +
          'The source PDF is still in storage and can be uploaded again.',
        )
      } finally {
        setJob(null)
        await refresh()
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not re-read the document')
    } finally {
      // Released in `finally`, and this is not optional: one unexpected throw
      // would otherwise leave the whole queue inert for the life of the page,
      // curable only by a reload nobody would think to try.
      // `phase` is cleared HERE and nowhere else. Set on the first tick, it
      // has to be released on every path out -- including a retryStatement()
      // that throws before the job exists, which the inner finally never sees.
      // A stranded phase leaves the panel spinning over nothing, and the upload
      // drop zone refuses input while it does.
      setPhase(null)
      setRetrying(null)
      setBusy(false)
    }
  }

  /**
   * Record whether a statement is the account's first, its last, or neither.
   *
   * Nothing on the page says which it is — an opening balance of zero is not
   * proof, since accounts get swept to zero routinely — so it is a judgment,
   * kept beside the other judgments on this data. It only ever suppresses a gap
   * the compliance report would otherwise raise, which is why the default is
   * the cautious one and marking is deliberate.
   */
  async function markBoundary(statement: AccountStatement, boundary: StatementBoundary) {
    setBusy(true)
    try {
      const updated = await setStatementBoundary(statement.id, boundary)
      setStatements(prev => prev.map(s => (s.id === updated.id ? updated : s)))
      setExceptions(prev => prev.map(s => (s.id === updated.id ? updated : s)))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not mark the statement')
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

      {/* Counts on the tabs, so what needs attention is legible without opening
          anything — it used to be visible only if you scrolled to it. */}
      <div className="flex gap-1 border-b border-border" role="tablist">
        {TABS.map(({ id, label }) => {
          const badge = id === 'import' ? exceptions.length
            : id === 'accounts' ? accounts.filter(a => a.ownership === 'unknown').length
            : 0
          const active = tab === id
          return (
            <button key={id} type="button" role="tab" aria-selected={active}
              className={`px-4 py-2 text-sm border-b-2 -mb-px transition-colors ${
                active
                  ? 'border-navy text-navy font-medium'
                  : 'border-transparent text-text-secondary hover:text-navy'}`}
              onClick={() => goTo(id)}>
              {label}
              {badge > 0 && (
                <span className="ml-2 text-xs rounded-full px-1.5 py-0.5 tabular-nums bg-amber-100 text-amber-800"
                  title={id === 'import'
                    ? `${badge} statement(s) need review`
                    : `${badge} account(s) with no owner recorded`}>
                  {badge}
                </span>
              )}
            </button>
          )
        })}
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

      {useReceived && (
        <div className="card p-4 border border-navy/30 bg-navy/5 text-sm
                        flex flex-wrap items-baseline gap-3">
          <span>Large productions are uploaded under Received documents.</span>
          <Link to={`/app/matters/${matterId}/received`}
                className="text-navy underline">Go to Received documents →</Link>
        </div>
      )}

      {toSplit.length > 0 && (
        <div className="card p-4 border border-navy/30 bg-navy/5 text-sm">
          <h3 className="font-display text-navy mb-1">
            {toSplit.length === 1 ? 'This file needs' : `${toSplit.length} files need`} splitting first
          </h3>
          <p className="text-text-secondary mb-3">
            {toSplit.length === 1 ? 'It is' : 'They are'} stored exactly as received — nothing
            was lost. Splitting by hand is quicker than correcting a bad automatic
            split, and you can leave {toSplit.length === 1 ? 'it' : 'them'} unsplit
            if you would rather not.
          </p>
          <ul className="space-y-2">
            {toSplit.map(r => (
              <li key={r.sourceDocumentId} className="flex flex-wrap items-baseline gap-2">
                <span className="font-mono">{r.name}</span>
                <span className="text-text-secondary text-xs">{r.reason}</span>
                <Link to={`/app/bench/${r.sourceDocumentId}`}
                      className="ml-auto text-navy underline">split it →</Link>
              </li>
            ))}
          </ul>
        </div>
      )}

      {tab === 'import' && (<>
      {/* ── Upload ── */}
      <div
        className={`card p-8 text-center border-2 border-dashed transition-colors ${
          phase ? 'border-navy bg-navy/5' : dragOver ? 'border-navy bg-navy/5 cursor-pointer' : 'border-border hover:border-navy/40 cursor-pointer'
        }`}
        onDrop={e => {
          e.preventDefault(); setDragOver(false)
          // A RE-READ IS THE ONE PHASE A DROP MUST NOT JOIN. handleFiles would
          // overwrite `job` and `phase`, so the retry's progress would vanish
          // mid-flight and the new upload would appear to be what Retry was
          // doing. Uploads may still stack on each other; that is deliberate
          // and unchanged.
          //
          // Read off `retrying`, which is set on the same tick as the click, so
          // this is not the stale-state read that let a second drop through
          // before -- handleFiles still refuses re-entry itself, on a ref.
          if (retrying !== null) return
          handleFiles(Array.from(e.dataTransfer.files))
        }}
        onDragOver={e => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={e => { e.preventDefault(); setDragOver(false) }}
        onClick={() => {
          if (uploadingRef.current || retrying !== null) return
          fileInputRef.current?.click()
        }}>
        <input ref={fileInputRef} type="file" accept=".pdf" multiple className="hidden"
          onChange={e => {
            if (e.target.files?.length) handleFiles(Array.from(e.target.files))
            e.target.value = ''
          }} />
        {phase ? (
          <div>
            <div className="mx-auto mb-3 animate-spin w-8 h-8 border-4 border-navy/20 border-t-navy rounded-full" />
            {phase === 'uploading' ? (
              <>
                {/* The reassurance, and the whole point of this branch: it
                    appears on the same tick as the drop. Sending a dozen files
                    takes ten to fifteen seconds, and this used to render
                    nothing — so the screen looked untouched and the natural
                    thing to do was drag them again, which is exactly what
                    produced twenty-four jobs for twelve files. */}
                <p className="text-navy font-medium">
                  {queue ? `Uploading ${queue.total} file${queue.total === 1 ? '' : 's'}…` : 'Uploading…'}
                </p>
                {queue && (
                  <p className="text-navy text-sm mt-1 tabular-nums">
                    {queue.done} of {queue.total} sent{queue.name ? ` · ${queue.name}` : ''}
                  </p>
                )}
                <p className="text-text-secondary text-sm mt-1">
                  Do not drop them again — every file is being sent.
                </p>
              </>
            ) : (
              <>
                {/* A re-read and a fresh upload reach this branch alike, and
                    they are not the same event to somebody watching: one is
                    "my files are being read", the other is "the document I
                    clicked Retry on is being discarded and read again". Saying
                    the wrong one is worse than saying nothing, because it
                    describes work the user did not ask for. */}
                <p className="text-navy font-medium">
                  {retrying !== null
                    ? (job?.status === 'queued'
                        ? 'Queued — re-reading the document…'
                        : 'Re-reading the document…')
                    : (job?.status === 'queued' ? 'Queued…' : 'Reading the statements…')}
                </p>
                {/* Counts finished, not "which one" — several are read at once,
                    so there is no current file to name. */}
                {queue && (
                  <p className="text-navy text-sm mt-1 tabular-nums">
                    {queue.done} of {queue.total} done
                  </p>
                )}
                <p className="text-text-secondary text-sm mt-1 tabular-nums">
                  {job?.seconds ?? 0}s · {retrying !== null
                    ? 'every statement from this upload is being recreated — you can leave this page'
                    : 'several are read at once — you can leave this page'}
                </p>
              </>
            )}
          </div>
        ) : (
          <div>
            <svg className="mx-auto w-10 h-10 text-text-secondary/50 mb-3" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
            </svg>
            <p className="text-navy font-medium">Drop statement PDFs here or click to browse</p>
            <p className="text-text-secondary text-sm mt-1">
              Bank, brokerage, or credit card. Drop as many as you like — several are read at
              once, so a stack of monthly PDFs finishes far sooner than the same months
              combined into one file. Several statements in one file still works; each is
              filed separately.
            </p>
            <p className="text-text-secondary text-xs mt-2">
              For a whole production — a scanned pile, or anything over
              {MAX_DIRECT_POST_MB} MB — use Received documents instead, which
              stores it exactly as served and splits it.
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

      {uploadAdvice.length > 0 && (
        <div className="p-3 border border-amber-300 bg-amber-50 rounded space-y-1">
          <p className="text-sm font-medium text-amber-900">Before you rely on this import</p>
          {uploadAdvice.map((note, i) => (
            <p key={i} className="text-sm text-amber-900">{note}</p>
          ))}
        </div>
      )}
      {uploadError.length > 0 && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 space-y-2">
          {uploadError.map((message, i) => (
            <p key={i} className="text-sm text-red-800">{message}</p>
          ))}
          <p className="text-xs text-red-700">
            {/* The refusal says this too, and it bears repeating where the
                person is looking: a rejected upload is not a half-import. */}
            Nothing from a rejected file was imported or stored.
          </p>
        </div>
      )}

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

          {/* An import creates accounts, and characterising them is the next
              thing to do — but the account list lives on another tab now, and
              a flag telling you to "merge them from the account editor" is no
              use if nothing points at where that is. */}
          <div className="mt-3 pt-3 border-t border-border flex flex-wrap items-baseline gap-3">
            <button type="button" className="text-sm text-navy underline"
              onClick={() => goTo('accounts')}>
              Review accounts →
            </button>
            <button type="button" className="text-sm text-navy underline"
              onClick={() => goTo('transactions')}>
              Search transactions →
            </button>
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
                    {/* Reviewing an exception nearly always means reading the
                        page. Deciding whether a balance really is what the
                        statement printed cannot be done from the numbers we
                        extracted from it. */}
                    <StatementPdfButton statementId={s.id} label="Show the source statement" />
                    {s.bates_first && (
                      <span className="text-xs text-text-secondary font-mono">
                        {s.bates_first}
                        {s.bates_last && s.bates_last !== s.bates_first ? `–${s.bates_last}` : ''}
                      </span>
                    )}
                    {/* THE ROW BEING RE-READ SAYS SO, and the others say why
                        they are waiting. Disabling all of them is correct — a
                        retry discards every statement its upload produced, so
                        the neighbouring rows are about to be destroyed and
                        recreated — but correct and silent is what had somebody
                        clicking Retry down the list wondering why nothing
                        happened. */}
                    {retrying === s.id ? (
                      <span className="ml-auto flex items-center gap-2 text-xs text-navy">
                        <span className="animate-spin w-3 h-3 border-2 border-navy/20 border-t-navy rounded-full" />
                        Re-reading this document{job?.seconds ? ` · ${job.seconds}s` : '\u2026'}
                      </span>
                    ) : (
                    <span className="ml-auto flex gap-3">
                      <button type="button" className={`text-xs text-navy underline ${linkBusy}`}
                        disabled={busy} title={busy ? RETRY_WAIT : undefined}
                        onClick={() => decide(s, 'accepted')}>Accept</button>
                      {/* Between accepting a bad read and throwing it away
                          outright: some statements come out wrong for a reason
                          a second reading fixes. It discards the whole upload
                          first — see retry() for why it cannot be just this
                          statement. */}
                      <button type="button" className={`text-xs text-navy underline ${linkBusy}`}
                        disabled={busy || !s.storage_path}
                        title={!s.storage_path
                          ? 'No source PDF was stored for this statement'
                          : busy
                            ? RETRY_WAIT
                            : 'Discard this upload and read the PDF again'}
                        onClick={() => retry(s)}>Retry</button>
                      <button type="button" className={`text-xs text-red-700 underline ${linkBusy}`}
                        disabled={busy} title={busy ? RETRY_WAIT : undefined}
                        onClick={() => decide(s, 'rejected')}>Reject and delete</button>
                    </span>
                    )}
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

      </>)}

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

      {balancesFor && (
        <StatementBalanceDialog
          statement={balancesFor}
          /* The neighbours come from the list already on screen — no fetch.
             Found by period rather than array position, so the hint stays right
             whatever order the list arrives in, and rejected reads are skipped
             because a discarded extraction is not evidence of a balance. */
          prior={statements
            .filter(x => x.review_status !== 'rejected'
                      && x.period_end <= balancesFor.period_start && x.id !== balancesFor.id)
            .sort((a, b) => b.period_end.localeCompare(a.period_end))[0]}
          next={statements
            .filter(x => x.review_status !== 'rejected'
                      && x.period_start >= balancesFor.period_end && x.id !== balancesFor.id)
            .sort((a, b) => a.period_start.localeCompare(b.period_start))[0]}
          onClose={() => setBalancesFor(null)}
          onSaved={async updated => {
            setStatements(prev => prev.map(x => (x.id === updated.id ? updated : x)))
            setBalancesFor(null)
            // The reconciliation moved, so the badge, the exceptions queue and
            // the account's totals are all stale.
            await refresh()
          }} />
      )}

      {batesFor && (
        <BatesDialog
          statement={batesFor}
          onClose={() => setBatesFor(null)}
          onSaved={async updated => {
            setStatements(prev => prev.map(x => (x.id === updated.id ? updated : x)))
            setBatesFor(null)
            // The lines now carry numbers, so anything open on screen is stale.
            if (openStatementId === updated.id) {
              setTransactions(await getStatementTransactions(updated.id))
            }
            await refresh()
          }} />
      )}

      {addingAccount && (
        <AccountAddDialog
          matterId={matterId}
          prefill={addingAccount}
          onClose={() => setAddingAccount(null)}
          onAdded={async () => { setAddingAccount(null); await refresh() }} />
      )}

      {addingTo && (
        <TransactionAddDialog
          statement={addingTo}
          onClose={() => setAddingTo(null)}
          onAdded={async (created, statement) => {
            setTransactions(prev => [...prev, created])
            // A new line always moves the sum, so the statement's badge and the
            // exceptions queue are both stale the moment it is saved.
            if (statement) {
              setStatements(prev => prev.map(s => (s.id === statement.id ? statement : s)))
              await refresh()
            }
            setAddingTo(null)
          }} />
      )}

      {tab === 'transactions' && (<>
        <TransactionSearchPanel matterId={matterId} accounts={accounts} />
        {/* Below the search, not beside it: reviewing what the client marked is
            its own pass through the data, done when somebody sits down to it. */}
        <div className="mt-6">
          <ClientTagReviewPanel matterId={matterId} />
        </div>
      </>)}

      {tab === 'inventory' && <InventoryPanel matterId={matterId} />}

      {tab === 'compliance' && <CompliancePanel matterId={matterId} />}

      {tab === 'fis' && <FisPanel matterId={matterId} accounts={accounts} />}

      {tab === 'accounts' && (<>
      {/* ── Accounts ── */}
      <div className="card p-5">
        <h2 className="font-semibold text-navy mb-3 flex items-baseline gap-3">
          <span>
            Accounts <span className="ml-1 text-sm font-normal text-text-secondary">{accounts.length}</span>
          </span>
          {/* An account nothing can be ingested for — a receipt naming one, or
              a reference somebody is resolving off the undisclosed list. */}
          <button type="button" className="ml-auto text-xs text-navy underline font-normal"
            disabled={busy} onClick={() => setAddingAccount({})}>Add an account</button>
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
                        <StatementPdfButton statementId={s.id} />
                        {/* Beside the link to the original, because the two are
                            used together: one is opened on screen and the other
                            printed to read against it. */}
                        <NormalizedStatementButton statementId={s.id} disabled={busy} />
                        <span className={`text-xs rounded-full px-2 py-0.5 font-medium ${REVIEW_COLOR[s.review_status]}`}>
                          {REVIEW_LABEL[s.review_status]}
                        </span>
                        <ReconcileBadge statement={s} />
                        {s.bates_first ? (
                          <span className="text-xs text-text-secondary font-mono">
                            {s.bates_first}
                            {s.bates_last && s.bates_last !== s.bates_first ? `–${s.bates_last}` : ''}
                          </span>
                        ) : (
                          /* Detection found no series. Rare, and retrying cannot
                             help: the document reads the same way every time. */
                          <button type="button" className="text-xs text-navy underline"
                            disabled={busy}
                            title="Type the stamps off the pages. They are recorded as entered by hand."
                            onClick={() => setBatesFor(s)}>
                            add Bates
                          </button>
                        )}
                        <span className="ml-auto flex items-baseline gap-3">
                          {/* Marks the edges of the account's life, which is
                              what lets the compliance matrix tell a hole in the
                              production from a month that never existed. */}
                          <select className="text-xs border border-border rounded px-1 py-0.5 bg-white"
                            value={s.boundary} disabled={busy}
                            title={busy ? RETRY_WAIT
                              : "Is this the account's first statement, its last, or neither?"}
                            onChange={e => markBoundary(s, e.target.value as StatementBoundary)}>
                            <option value="intermediate">neither end</option>
                            <option value="opening">first statement †</option>
                            <option value="closing">last statement ‡</option>
                          </select>
                          {/* The balances are the control, because this is where
                              somebody already looks when a statement is off by a
                              little — and a misread opening balance has no other
                              repair: a Retry re-reads the same PDF and returns the
                              same number. */}
                          <button type="button"
                            className="text-xs text-text-secondary tabular-nums underline
                                       decoration-dotted hover:text-navy"
                            disabled={busy}
                            title={busy ? RETRY_WAIT
                              : 'Correct a balance the import read wrong'}
                            onClick={() => setBalancesFor(s)}>
                            {money(s.beginning_balance)} → {money(s.ending_balance)}
                          </button>
                          <button type="button"
                            className={`text-xs text-red-700 underline ${linkBusy}`}
                            title={busy ? RETRY_WAIT : undefined} disabled={busy}
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

      {/* ── Transactions ──
          Shown whenever a statement is open, even with no lines on it. A
          statement that extracted NOTHING is exactly the one somebody needs to
          add a line to, and hiding the panel on an empty result made that the
          one case the screen could not reach. */}
      {(transactions.length > 0 || viewedStatement !== undefined) && (
        <div className="card p-5">
          <div className="flex items-baseline justify-between mb-3">
            <h2 className="font-semibold text-navy">
              Transactions
              <span className="ml-2 text-sm font-normal text-text-secondary">{txLabel}</span>
            </h2>
            <div className="flex items-baseline gap-3">
              {/* Only with one statement open: a line belongs to the document
                  it is printed on, and the whole-account view spans many. */}
              {viewedStatement !== undefined && (
                <button type="button" className="text-xs text-navy underline" disabled={busy}
                  title="Add a line that is printed on this statement but the import missed"
                  onClick={() => setAddingTo(viewedStatement)}>Add a line</button>
              )}
              <span className="text-sm text-text-secondary tabular-nums">
                {transactions.length} lines
              </span>
            </div>
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

      <UndisclosedAccountsPanel matterId={matterId} />
      </>)}
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
    production_responsibility: account.production_responsibility,
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
        <label className="label" htmlFor={`resp-${account.id}`}>Who produces</label>
        <select id={`resp-${account.id}`} className="input mt-1"
          value={draft.production_responsibility}
          onChange={e => set('production_responsibility',
                             e.target.value as ProductionResponsibility)}>
          {RESPONSIBILITIES.map(r => (
            <option key={r} value={r}>{RESPONSIBILITY_LABEL[r]}</option>
          ))}
        </select>
        <p className="text-xs text-text-secondary mt-1">
          Which compliance report this account appears on. Usually follows &ldquo;Held by&rdquo;
          and sometimes does not.
        </p>
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

/**
 * Type a statement's Bates numbers in, when detection found none.
 *
 * The numbers run consecutively across the pages in hand — a partial production
 * of physical pages 1, 2 and 4 carries three consecutive stamps, because the
 * missing sheet took its number with it. Both ends are asked for so the
 * arithmetic can check the span: being off by one mis-cites every line after
 * the first, silently, which is worse than no citation at all.
 */
/** A typed figure, forgiving about how it was pasted. "$1,300.03" -> "1300.03". */
function cleanMoney(text: string): string {
  return text.replace(/[$,\s]/g, '')
}

/** Whole dollars and cents, optionally negative. Never parsed through Number(). */
const MONEY_SHAPE = /^-?\d+(\.\d{1,2})?$/

function sameMoney(a: string | null, b: string | null): boolean {
  if (a === null || b === null) return false
  const [x, y] = [cleanMoney(a), cleanMoney(b)]
  // Compare as fixed-point text, so "1300.0" and "1300.00" agree without a
  // float ever existing. Pad the cents rather than parsing.
  const pad = (v: string) => (v.includes('.') ? v : `${v}.0`).replace(/(\.\d)$/, '$10')
  return pad(x) === pad(y)
}

/**
 * Correct a balance the extraction read wrong.
 *
 * **The neighbours are the check, and they are free.** A statement's opening
 * balance is the previous statement's close, and its close is the next one's
 * opening — so where the account has adjacent statements the right figure is
 * already on screen, and a mismatch says which end is suspect without anybody
 * fetching a PDF. Offered as a hint with one click to use it, never filled in
 * automatically: the authority is the figure printed on the page, and a chain
 * that disagrees does not say WHICH of the two statements is wrong.
 */
function StatementBalanceDialog({ statement, prior, next, onSaved, onClose }: {
  statement: AccountStatement
  prior?: AccountStatement
  next?: AccountStatement
  onSaved: (updated: AccountStatement) => void | Promise<void>
  onClose: () => void
}) {
  const [beginning, setBeginning] = useState(statement.beginning_balance ?? '')
  const [ending, setEnding] = useState(statement.ending_balance ?? '')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const beginningChanged = !sameMoney(beginning, statement.beginning_balance)
  const endingChanged = !sameMoney(ending, statement.ending_balance)
  const typed = [beginning, ending].filter(v => v.trim())
  const malformed = typed.some(v => !MONEY_SHAPE.test(cleanMoney(v)))
  const nothingToDo = !beginningChanged && !endingChanged

  async function save() {
    setBusy(true); setError(null)
    try {
      await onSaved(await correctStatementBalances(statement.id, {
        // Only the end that moved. Omitting one leaves it alone, which is what
        // keeps a flag off a figure nobody touched.
        ...(beginningChanged ? { beginning_balance: cleanMoney(beginning) } : {}),
        ...(endingChanged ? { ending_balance: cleanMoney(ending) } : {}),
        ...(reason.trim() ? { reason: reason.trim() } : {}),
      }))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the balances')
      setBusy(false)
    }
  }

  /** What the statement either side of this one says this figure should be. */
  function Chain({ neighbour, expected, current, onUse, label }: {
    neighbour?: AccountStatement
    expected: string | null
    current: string
    onUse: () => void
    label: string
  }) {
    if (!neighbour || expected === null) {
      return (
        <p className="text-xs text-text-secondary mt-1">
          No {label} statement is held, so there is nothing to check this against.
        </p>
      )
    }
    const agrees = sameMoney(current, expected)
    return (
      <p className={`text-xs mt-1 ${agrees ? 'text-text-secondary' : 'text-amber-700'}`}>
        {agrees ? '✓ ' : '≠ '}
        The {label} statement ({period(neighbour)}) {label === 'previous' ? 'closed' : 'opened'} at{' '}
        <span className="font-mono tabular-nums">{money(expected)}</span>
        {!agrees && (
          <>
            {' '}
            <button type="button" className="text-navy underline" onClick={onUse}>use it</button>
          </>
        )}
      </p>
    )
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-start justify-center overflow-y-auto p-4"
      role="dialog" aria-modal="true" aria-label="Correct the balances"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="card p-5 w-full max-w-lg my-8 bg-white">
        <div className="flex items-baseline justify-between mb-1">
          <h2 className="font-semibold text-navy">Balances for {period(statement)}</h2>
          <button type="button" className="text-sm text-text-secondary hover:text-navy"
            onClick={onClose}>Close</button>
        </div>
        <p className="text-xs text-text-secondary mb-4">
          Copy the figures off the statement. Changing one re-checks the arithmetic against the
          lines on it, so a statement corrected into balance stops reporting as unreconciled.
          Both the old and new figures are recorded against your name.
        </p>

        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <label className="label" htmlFor="bal-beginning">Opening balance</label>
            <input id="bal-beginning" className="input mt-1 font-mono tabular-nums"
              value={beginning} placeholder="0.00"
              onChange={e => setBeginning(e.target.value)} />
            <Chain neighbour={prior} expected={prior?.ending_balance ?? null}
              current={beginning} label="previous"
              onUse={() => setBeginning(prior?.ending_balance ?? '')} />
          </div>
          <div>
            <label className="label" htmlFor="bal-ending">Closing balance</label>
            <input id="bal-ending" className="input mt-1 font-mono tabular-nums"
              value={ending} placeholder="0.00"
              onChange={e => setEnding(e.target.value)} />
            <Chain neighbour={next} expected={next?.beginning_balance ?? null}
              current={ending} label="next"
              onUse={() => setEnding(next?.beginning_balance ?? '')} />
          </div>
        </div>

        <div className="mt-4">
          <label className="label" htmlFor="bal-reason">Reason</label>
          <input id="bal-reason" className="input mt-1" value={reason}
            placeholder="Read off page 1; the import picked up the previous period's close"
            onChange={e => setReason(e.target.value)} />
        </div>

        {malformed && (
          <p className="text-sm text-amber-700 mt-3">
            A balance must be a figure like 1300.03 or -245.00.
          </p>
        )}
        {error && <p className="text-sm text-red-700 mt-3">{error}</p>}

        <div className="flex items-center justify-end gap-3 mt-5">
          <button type="button" className="btn-secondary" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn-primary"
            disabled={busy || malformed || nothingToDo}
            title={nothingToDo ? 'Neither figure has changed' : undefined}
            onClick={() => void save()}>
            {busy ? 'Saving…' : 'Save balances'}
          </button>
        </div>
      </div>
    </div>
  )
}

function BatesDialog({ statement, onSaved, onClose }: {
  statement: AccountStatement
  onSaved: (updated: AccountStatement) => void | Promise<void>
  onClose: () => void
}) {
  const [first, setFirst] = useState('')
  const [last, setLast] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save() {
    setBusy(true); setError(null)
    try {
      const result = await setStatementBates(statement.id, first.trim(), last.trim() || undefined)
      await onSaved(result.statement)
    } catch (e) {
      // The server refuses a range that does not span the pages in hand, and
      // says both counts — that sentence is the whole value of the check.
      setError(e instanceof Error ? e.message : 'Could not save the Bates numbers')
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-start justify-center overflow-y-auto p-4"
      role="dialog" aria-modal="true" aria-label="Add Bates numbers"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="card p-5 w-full max-w-lg my-8 bg-white">
        <div className="flex items-baseline justify-between mb-1">
          <h2 className="font-semibold text-navy">Bates numbers for this statement</h2>
          <button type="button" className="text-sm text-text-secondary hover:text-navy"
            onClick={onClose}>Close</button>
        </div>
        <p className="text-xs text-text-secondary mb-4">
          Copy the stamps off the first and last pages, exactly as printed. The numbers in
          between are filled in across the pages held — a production missing a sheet still runs
          consecutively. Every line is recorded as numbered by hand.
        </p>

        <div className="grid gap-3 md:grid-cols-2">
          <div>
            <label className="label" htmlFor="bates-first">First page</label>
            <input id="bates-first" className="input mt-1 font-mono" value={first}
              placeholder="KF 000101" onChange={e => setFirst(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="bates-last">Last page</label>
            <input id="bates-last" className="input mt-1 font-mono" value={last}
              placeholder="KF 000103" onChange={e => setLast(e.target.value)} />
            <p className="text-xs text-text-secondary mt-1">
              Leave blank for a single page.
            </p>
          </div>
        </div>

        {error && <p className="text-sm text-red-700 mt-3">{error}</p>}

        <div className="flex items-center justify-end gap-3 mt-5">
          <button type="button" className="btn-secondary" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn-primary" disabled={busy || !first.trim()}
            onClick={() => void save()}>
            {busy ? 'Saving…' : 'Apply'}
          </button>
        </div>
      </div>
    </div>
  )
}
