/**
 * The splitting bench — one received PDF becomes N documents.
 *
 * Why a person does this rather than a model, and every decision below, is in
 * docs/specs/document-labeling/SPEC_splitting_bench.md. The short version: on a
 * bad pile, reviewing a wrong automatic split costs more than making the right
 * one, so the operator groups pages and the machine names them afterward.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams, useNavigate } from 'react-router-dom'

import {
  benchPages, benchThumbnail, bundlePages, finishBench, ingestDocumentAsStatement,
  leaveBench, openBench, undoBundle,
} from '../../lib/api'
import type {
  BenchGrid, BenchPage, BenchSession, BundleSummary,
} from '../../types'

const PER_SCREEN = 20

export default function SplittingBenchPage() {
  const { sourceDocumentId } = useParams<{ sourceDocumentId: string }>()
  const sourceId = Number(sourceDocumentId)
  const navigate = useNavigate()

  const [session, setSession] = useState<BenchSession | null>(null)
  const [grid, setGrid] = useState<BenchGrid | null>(null)
  const [offset, setOffset] = useState(0)
  const [includeClaimed, setIncludeClaimed] = useState(false)
  const [selected, setSelected] = useState<number[]>([])
  // Bundles come from the server, never from this component's memory. Keeping
  // them here meant that closing the page or finishing lost the list, leaving
  // somebody told there were four documents with no way to see which pages were
  // in which and no way to undo any of them -- so they guessed, and guessing on
  // this screen makes duplicates.
  const [bundles, setBundles] = useState<BundleSummary[]>([])
  const [thumbs, setThumbs] = useState<Record<number, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [focus, setFocus] = useState(0)
  // Documents sent for statement extraction this session. The read takes
  // minutes in a worker, so this is only an acknowledgement that it started —
  // the exceptions queue is where the result is reviewed.
  const [sent, setSent] = useState<Record<number, string>>({})

  // A ref, not state: two quick actions both read the old state value and both
  // run. Set on the same tick, this cannot double-submit.
  const working = useRef(false)
  // Object URLs outlive the render that made them, and a 396-page pile scrolled
  // end to end is 396 of them. The browser holds every one until told
  // otherwise, so they are revoked on replacement and on unmount.
  const liveUrls = useRef<string[]>([])

  const loadSession = useCallback(async () => {
    const next = await openBench(sourceId)
    setSession(next)
    setBundles(next.bundles)
  }, [sourceId])

  const loadGrid = useCallback(async () => {
    const next = await benchPages(sourceId, offset, PER_SCREEN, includeClaimed)
    setGrid(next)
    setFocus(0)

    liveUrls.current.forEach((u) => URL.revokeObjectURL(u))
    liveUrls.current = []
    setThumbs({})

    // Sequential rather than Promise.all: twenty parallel renders on one node
    // is a thundering herd for no gain, and they appear top-first this way,
    // which is the order somebody reads them in.
    for (const page of next.pages) {
      try {
        const url = await benchThumbnail(sourceId, page.page)
        liveUrls.current.push(url)
        setThumbs((prev) => ({ ...prev, [page.page]: url }))
      } catch {
        // A page that will not render is still selectable from its badges.
      }
    }
  }, [sourceId, offset, includeClaimed])

  useEffect(() => {
    loadSession().catch((e: Error) => setError(e.message))
  }, [loadSession])

  useEffect(() => {
    loadGrid().catch((e: Error) => setError(e.message))
  }, [loadGrid])

  useEffect(() => () => {
    liveUrls.current.forEach((u) => URL.revokeObjectURL(u))
  }, [])

  /** Which bundle a page is in, by page number. */
  const holder = new Map<number, BundleSummary>()
  bundles.forEach((b) => b.pages.forEach((pg) => holder.set(pg, b)))

  /** Selected pages that are already in a bundle. */
  const overlapping = selected.filter((pg) => holder.has(pg))

  const toggle = (page: number) => {
    setSelected((prev) => (prev.includes(page)
      ? prev.filter((p) => p !== page)
      : [...prev, page]))
  }

  const done = async () => {
    if (working.current || selected.length === 0) return
    working.current = true
    setBusy(true)
    setError(null)
    try {
      await bundlePages(sourceId, selected)
      setSelected([])
      await Promise.all([loadSession(), loadGrid()])
    } catch (e) {
      setError((e as Error).message)
    } finally {
      working.current = false
      setBusy(false)
    }
  }

  const undo = async (documentId: number) => {
    if (working.current) return
    working.current = true
    setBusy(true)
    setError(null)
    try {
      await undoBundle(documentId)
      await Promise.all([loadSession(), loadGrid()])
    } catch (e) {
      setError((e as Error).message)
    } finally {
      working.current = false
      setBusy(false)
    }
  }

  /**
   * Read one split document as an account statement.
   *
   * The one route out of the bench into a processor, chosen by a person for
   * now: the classifier that would choose it needs a taxonomy nobody has
   * finalised. It does not block — the read is minutes of work in the worker,
   * and the exceptions queue is where its result belongs.
   */
  const ingest = async (documentId: number) => {
    setError(null)
    setSent((prev) => ({ ...prev, [documentId]: 'sending' }))
    try {
      await ingestDocumentAsStatement(documentId)
      setSent((prev) => ({ ...prev, [documentId]: 'reading' }))
    } catch (e) {
      setSent((prev) => {
        const next = { ...prev }
        delete next[documentId]
        return next
      })
      setError((e as Error).message)
    }
  }

  const finish = async () => {
    if (working.current) return

    // FINISH IS NOT DONE, and the two buttons sit side by side. Pressing Finish
    // with pages selected silently discarded the selection and swept the rest
    // of the pile into one document -- which is what happened the first time
    // somebody used this screen. Refuse and say so.
    if (selected.length > 0) {
      setError(`${selected.length} page${selected.length === 1 ? ' is' : 's are'} still `
        + 'selected. Press DONE to bundle them, or clear the selection — Finish '
        + 'would gather them into the leftovers instead.')
      return
    }

    const left = session?.pages_unclaimed ?? 0
    if (left > 0 && !window.confirm(
      `${left} page${left === 1 ? '' : 's'} are not in any document yet. `
      + 'Finishing gathers them all into ONE document so none is lost. '
      + 'Carry on?')) {
      return
    }

    working.current = true
    setBusy(true)
    setError(null)
    try {
      const result = await finishBench(sourceId)
      navigate(`/app/matters/${session?.matter_id ?? ''}`, {
        state: { benchResult: result },
      })
    } catch (e) {
      setError((e as Error).message)
    } finally {
      working.current = false
      setBusy(false)
    }
  }

  const leave = async () => {
    await leaveBench(sourceId).catch(() => undefined)
    navigate(session ? `/app/matters/${session.matter_id}` : '/app')
  }

  // A desk operator works faster on the keyboard than with a mouse, and this
  // screen is one repeated action. Space toggles, Enter is DONE, Ctrl+Z undoes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!grid || grid.pages.length === 0) return
      if (e.key === 'ArrowRight') { setFocus((f) => Math.min(f + 1, grid.pages.length - 1)); e.preventDefault() }
      else if (e.key === 'ArrowLeft') { setFocus((f) => Math.max(f - 1, 0)); e.preventDefault() }
      else if (e.key === 'ArrowDown') { setFocus((f) => Math.min(f + 4, grid.pages.length - 1)); e.preventDefault() }
      else if (e.key === 'ArrowUp') { setFocus((f) => Math.max(f - 4, 0)); e.preventDefault() }
      else if (e.key === ' ') { toggle(grid.pages[focus].page); e.preventDefault() }
      else if (e.key === 'Enter') { void done(); e.preventDefault() }
      else if (e.key === 'z' && (e.ctrlKey || e.metaKey)) {
        const last = [...bundles].reverse().find((b) => b.can_undo)
        if (last) void undo(last.document_id)
        e.preventDefault()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  if (error && !session) {
    return <div className="card m-6"><p className="text-danger">{error}</p></div>
  }
  if (!session) {
    return <div className="p-6 text-text-secondary">Opening…</div>
  }

  // STILL BEING READ. Without this the grid renders with no pages and announces
  // "Every page is in a document" — the opposite of the truth, to somebody
  // whose file has not been read yet. An empty grid has two possible meanings
  // and only one of them is "you are finished".
  if (!session.surveyed) {
    return (
      <div className="p-6">
        <div className="card max-w-xl">
          <h1 className="font-display text-xl text-navy">Still being read</h1>
          <p className="mt-2 text-sm text-text-secondary">
            {session.original_filename} is stored safely. Cyclone is reading it
            to find out what is on each page — on a large production that takes
            a minute or two, and it happens in the background so nothing is
            waiting on your browser.
          </p>
          <div className="mt-4 flex items-center gap-3">
            <button type="button" className="btn-primary"
                    onClick={() => void loadSession().catch(
                      (e: Error) => setError(e.message))}>
              Check again
            </button>
            <button type="button" className="btn-secondary"
                    onClick={() => navigate(`/app/matters/${session.matter_id}/received`)}>
              Back to received documents
            </button>
          </div>
          {error && <p className="mt-3 text-sm text-danger">{error}</p>}
        </div>
      </div>
    )
  }

  const remaining = grid?.pages_shown ?? 0
  const screens = Math.max(1, Math.ceil(remaining / PER_SCREEN))
  const screen = Math.floor(offset / PER_SCREEN) + 1

  return (
    <div className="p-4">
      {/* The size of the job, stated. An oversized pile routes here instead of
          being refused, so cancelling has to be an informed choice. */}
      <div className="card mb-4">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <div>
            <h1 className="font-display text-xl text-navy">Split this document</h1>
            <p className="text-sm text-text-secondary">
              {session.original_filename} — {session.page_count} pages,
              {' '}{session.documents_so_far} document{session.documents_so_far === 1 ? '' : 's'} so far,
              {' '}{session.pages_unclaimed} page{session.pages_unclaimed === 1 ? '' : 's'} left
              {session.readable_pages < session.page_count && (
                <> · {session.page_count - session.readable_pages} page
                  {session.page_count - session.readable_pages === 1 ? '' : 's'} with no readable text</>
              )}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button type="button" className="btn-secondary" onClick={leave}>
              Leave for now
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                const last = [...bundles].reverse().find((b) => b.can_undo)
                if (last) void undo(last.document_id)
              }}
              disabled={busy || !bundles.some((b) => b.can_undo)}
            >
              Unbundle last
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={finish}
              disabled={busy || session.documents_so_far === 0}
            >
              Finish
            </button>
          </div>
        </div>

        {session.held_by_other && (
          <p className="mt-3 rounded border border-warning bg-warning/10 p-2 text-sm">
            Somebody else has this document open. Two people splitting one pile
            claim the same pages, and the second person&apos;s work becomes
            duplicate documents. Check before you carry on.
          </p>
        )}
        {error && <p className="mt-3 text-sm text-danger">{error}</p>}
      </div>

      {/* Selection bar. Kept above the grid and sticky, because the operator's
          eyes are on the thumbnails and DONE is pressed dozens of times. */}
      <div className="sticky top-0 z-10 mb-3 flex flex-wrap items-center gap-3
                      border-b border-border bg-off-white/95 py-2 backdrop-blur">
        <button
          type="button"
          className="btn-primary px-8 py-3 text-base"
          onClick={done}
          disabled={busy || selected.length === 0}
        >
          {busy ? 'Working…' : `DONE — bundle ${selected.length} page${selected.length === 1 ? '' : 's'}`}
        </button>
        {selected.length > 0 && (
          <>
            <span className="font-mono text-sm text-text-secondary">
              {selected.slice().sort((a, b) => a - b).join(', ')}
            </span>
            <button type="button" className="text-sm text-navy underline
                       disabled:no-underline disabled:opacity-40"
                    onClick={() => setSelected([])} disabled={busy}>
              clear
            </button>
          </>
        )}
        {overlapping.length > 0 && (
          <span className="rounded border border-warning bg-warning/10 px-2 py-1 text-xs">
            {overlapping.length} selected page{overlapping.length === 1 ? ' is' : 's are'}
            {' '}already in {overlapping.length === 1
              ? `#${holder.get(overlapping[0])?.document_id}`
              : 'other documents'}. Bundling again makes a SECOND document
            holding the same pages — right for a combined statement where one
            sheet ends an account and begins the next, wrong otherwise.
          </span>
        )}
        <label className="ml-auto flex items-center gap-2 text-sm text-text-secondary">
          <input type="checkbox" checked={includeClaimed}
                 onChange={(e) => { setIncludeClaimed(e.target.checked); setOffset(0) }} />
          Show pages already bundled
        </label>
      </div>

      {grid && grid.pages.length === 0 ? (
        <div className="card text-center">
          <p className="text-text-secondary">
            {includeClaimed
              ? 'This document has no pages.'
              : 'Every page is in a document. Press Finish.'}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {grid?.pages.map((page, index) => (
            <PageCard
              key={page.page}
              page={page}
              url={thumbs[page.page]}
              selected={selected.includes(page.page)}
              focused={index === focus}
              onClick={() => toggle(page.page)}
            />
          ))}
        </div>
      )}

      {screens > 1 && (
        <div className="mt-4 flex items-center justify-center gap-4">
          <button type="button" className="btn-secondary"
                  onClick={() => setOffset(Math.max(0, offset - PER_SCREEN))}
                  disabled={offset === 0 || busy}>
            Previous
          </button>
          <span className="text-sm text-text-secondary">
            screen {screen} of {screens}
          </span>
          <button type="button" className="btn-secondary"
                  onClick={() => setOffset(offset + PER_SCREEN)}
                  disabled={offset + PER_SCREEN >= remaining || busy}>
            Next
          </button>
        </div>
      )}

      {bundles.length > 0 && (
        <div className="card mt-6">
          <h2 className="mb-2 font-display text-navy">
            Documents split from this file ({bundles.length})
          </h2>
          <ul className="divide-y divide-border/50 text-sm">
            {bundles.map((b) => (
              <li key={b.document_id} className="flex items-center gap-3 py-2">
                <span className="font-mono text-text-secondary">#{b.document_id}</span>
                <span className="font-mono">pages {b.ranges.join(', ')}</span>
                <span className="text-xs text-text-secondary">
                  ({b.page_count} page{b.page_count === 1 ? '' : 's'})
                </span>
                {b.doc_type && (
                  <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs">
                    {b.doc_type.replace(/_/g, ' ')}
                  </span>
                )}
                <span className="ml-auto flex items-center gap-3">
                  {sent[b.document_id] === 'reading' ? (
                    <span className="text-xs text-success">reading as a statement…</span>
                  ) : b.statement_id ? (
                    // The statement as it is PRINTED, not its row id. "#771"
                    // correlates to nothing the reader can see — not the
                    // exceptions queue, not the Accounts tab, not the paper.
                    <Link to={`/app/matters/${session.matter_id}/financials?tab=accounts`}
                          className="text-xs text-navy underline">
                      {b.statement_label ?? `statement #${b.statement_id}`}
                    </Link>
                  ) : (
                    <button type="button"
                            className="text-navy underline
                                       disabled:no-underline disabled:opacity-40"
                            onClick={() => void ingest(b.document_id)}
                            disabled={busy || sent[b.document_id] === 'sending'}>
                      {sent[b.document_id] === 'sending'
                        ? 'sending…' : 'read as statement'}
                    </button>
                  )}
                  {b.can_undo ? (
                    <button type="button"
                            className="text-navy underline
                                       disabled:no-underline disabled:opacity-40"
                            onClick={() => void undo(b.document_id)}
                            disabled={busy}>
                      unbundle
                    </button>
                  ) : (
                    // "in use downstream" explained an internal constraint
                    // rather than telling anybody what to do about it.
                    <span className="text-xs text-text-secondary"
                          title="Reject the statement in Financials first, then this can be
                                 unbundled.">
                      {b.statement_id ? 'read — cannot unbundle' : 'cannot unbundle'}
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>
          {/* Naming is what makes the Acrobat workflow slow, and it is a cost
              imposed by the file system being the data model. Here a document
              is a row; the classifier proposes its description. */}
          <p className="mt-3 text-xs text-text-secondary">
            Nothing needs naming. Each document is identified by its pages and
            Bates range, and its description comes from classification.
          </p>
        </div>
      )}
    </div>
  )
}

/**
 * One thumbnail.
 *
 * Badges show what the page PRINTS — pagination, a header date, an account
 * number. Never a guess at where a document starts: a pile reaches this screen
 * because the automatic signals are weak on it, so a conclusion shown here
 * would have to be undone, which is the cost the bench exists to remove.
 */
function PageCard({ page, url, selected, focused, onClick }: {
  page: BenchPage
  url?: string
  selected: boolean
  focused: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={[
        'group relative overflow-hidden rounded border-2 bg-white text-left transition',
        selected ? 'border-navy ring-2 ring-navy' : 'border-border hover:border-gold',
        focused ? 'outline outline-2 outline-offset-2 outline-gold' : '',
      ].join(' ')}
    >
      <div className="flex aspect-[612/792] items-center justify-center bg-gray-100">
        {url
          ? <img src={url} alt={`Page ${page.page}`} className="h-full w-full object-contain" />
          : <span className="text-xs text-text-secondary">rendering…</span>}
      </div>

      <div className="flex items-center justify-between gap-1 border-t border-border px-2 py-1">
        <span className="font-mono text-sm font-semibold text-navy">{page.page}</span>
        {selected && <span className="text-xs font-semibold text-navy">SELECTED</span>}
        {page.claimed_by_document_id && (
          <span className="rounded bg-navy/10 px-1.5 py-0.5 text-xs font-semibold text-navy">
            in #{page.claimed_by_document_id}
          </span>
        )}
      </div>

      <div className="space-y-0.5 px-2 pb-2 text-xs text-text-secondary">
        {page.page_of && <div className="font-mono">{page.page_of}</div>}
        {page.date && <div className="font-mono">{page.date}</div>}
        {page.account && <div className="font-mono">···{page.account.slice(-4)}</div>}
        {page.blank && <div className="text-warning">blank</div>}
        {page.unreadable && <div className="text-warning">no readable text</div>}
      </div>
    </button>
  )
}
