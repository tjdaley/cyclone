import { useState } from 'react'
import { openStatementPdf } from '../lib/api'

/**
 * Open the PDF a statement was extracted from, in a new tab.
 *
 * Every list of statements gets one of these, which is the point of it being a
 * component rather than a handler copied into each page: the interesting parts
 * — the popup-blocker dance, and saying plainly when the document is gone —
 * are exactly the parts that would rot in the copy nobody maintains.
 *
 * The icon is a document, not a word, because it sits inside rows that are
 * already dense with pills and figures. The tooltip carries the meaning.
 */
export default function StatementPdfButton({
  statementId,
  label = 'Show statement',
}: {
  statementId: number
  label?: string
}) {
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  const open = async () => {
    setBusy(true)
    setProblem(null)
    try {
      await openStatementPdf(statementId)
    } catch (e) {
      // A dead link is the failure this button exists to replace, so the reason
      // has to be readable rather than a console entry. The server distinguishes
      // "never stored", "purged", and "storage is down" — each calls for a
      // different next move, so its wording is shown rather than replaced.
      setProblem(e instanceof Error ? e.message : 'The source PDF could not be opened.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <button type="button"
        className="text-text-secondary hover:text-navy disabled:opacity-40 align-middle"
        title={label}
        aria-label={label}
        disabled={busy}
        onClick={e => { e.stopPropagation(); void open() }}>
        {/* A page with a folded corner. Inline rather than an icon dependency —
            this is the only glyph the app needs here. */}
        <svg viewBox="0 0 16 16" className="w-4 h-4" fill="none" stroke="currentColor"
          strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M9.5 1.5H4a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V5z" />
          <path d="M9.5 1.5V5H13" />
        </svg>
      </button>

      {problem && (
        <div role="alertdialog" aria-modal="true"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
          onClick={() => setProblem(null)}>
          <div className="card p-5 max-w-md space-y-3" onClick={e => e.stopPropagation()}>
            <h3 className="font-semibold text-navy">The source PDF is not available</h3>
            <p className="text-sm text-text-primary">{problem}</p>
            <div className="text-right">
              <button type="button" className="btn-secondary text-sm"
                onClick={() => setProblem(null)}>Close</button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
