/**
 * Files as they arrived on a matter, and where each one should go.
 *
 * Every file is kept exactly as served — never split, OCR'd or converted — and
 * the working copies are made from it. An unsplit file is a legitimate state,
 * so this list reports what has been done rather than treating "not split" as
 * an error.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { listReceivedDocuments, rerouteReceivedDocument } from '../../lib/api'
import { uploadReceivedDirect } from '../../lib/directUpload'
import {
  isTooLargeError, tooLargeToUpload, uploadLimitMB, uploadRejectedMessage,
} from '../../lib/uploadLimit'
import type { ReceivedDocument, ReceivedUpload } from '../../types'

export default function ReceivedDocumentsPage() {
  const { matterId } = useParams<{ matterId: string }>()
  const id = Number(matterId)

  const [rows, setRows] = useState<ReceivedDocument[]>([])
  const [verdicts, setVerdicts] = useState<ReceivedUpload[]>([])
  const [dragging, setDragging] = useState(false)
  const [progress, setProgress] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [maxUploadMB, setMaxUploadMB] = useState<number | null>(null)

  // A ref, not state: state does not update until the next render, so two quick
  // drops both read the old value and both run. Set on the same tick, this
  // cannot. Released in `finally` — one unexpected throw would otherwise
  // disable the drop zone for the life of the page, curable only by a reload
  // nobody would think to try.
  const working = useRef(false)

  const load = useCallback(async () => {
    try {
      setRows(await listReceivedDocuments(id))
    } catch (e) {
      setError((e as Error).message)
    }
  }, [id])

  useEffect(() => { void load() }, [load])
  useEffect(() => { void uploadLimitMB().then(setMaxUploadMB) }, [])

  const take = async (files: File[]) => {
    // A re-drag is somebody unsure the first one landed. Refusing it and saying
    // so is better than silence, which is what caused it.
    if (working.current) {
      setError('Still uploading the last batch — give it a moment.')
      return
    }
    if (files.length === 0) return

    working.current = true
    setError(null)
    setVerdicts([])
    try {
      // Busy state goes up BEFORE the first request, not when a result comes
      // back: a dozen files is ten seconds of a screen that otherwise looks
      // untouched, and the natural response is to drag them again.
      const results: ReceivedUpload[] = []
      for (let i = 0; i < files.length; i += 1) {
        // Refused before a byte is sent: nginx rejects an oversized body
        // mid-flight, after the whole upload has been waited out.
        const tooBig = tooLargeToUpload(files[i], maxUploadMB)
        if (tooBig) {
          setError((prev) => [prev, tooBig].filter(Boolean).join('\n'))
          continue
        }
        try {
          results.push(await uploadReceivedDirect(id, files[i], (stage) => {
            // A 74 MB upload is a long silence otherwise, and a screen that
            // looks untouched is what makes somebody drag the same files again.
            const what = stage === 'hashing' ? 'Reading'
              : stage === 'uploading' ? 'Uploading' : 'Recording'
            setProgress(`${what} ${i + 1} of ${files.length}: ${files[i].name}`)
          }))
        } catch (e) {
          // Per-file, as it happens. One bad file in a dropped stack must not
          // discard the report on the others.
          setError((prev) => [prev, isTooLargeError(e)
            ? uploadRejectedMessage(files[i].name, maxUploadMB)
            : `${files[i].name}: ${(e as Error).message}`]
            .filter(Boolean).join('\n'))
        }
        setVerdicts([...results])
      }
      await load()
    } finally {
      working.current = false
      setProgress(null)
    }
  }

  const reroute = async (sourceDocumentId: number) => {
    try {
      const v = await rerouteReceivedDocument(sourceDocumentId)
      setVerdicts([v])
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="p-4">
      <div className="mb-4">
        <h1 className="font-display text-xl text-navy">Received documents</h1>
        <p className="text-sm text-text-secondary">
          Files exactly as they were served on us, received from the client, or
          returned by a subpoena. Nothing is ever written back to one: the
          working copies are split out of it.
        </p>
      </div>

      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          // EVERY file, not files[0]. Productions arrive as a stack, and
          // dropping the rest silently is the worst failure there is: nothing
          // looks wrong.
          void take(Array.from(e.dataTransfer.files))
        }}
        className={[
          'mb-6 rounded-lg border-2 border-dashed p-8 text-center transition',
          dragging ? 'border-gold bg-gold/5' : 'border-border',
        ].join(' ')}
      >
        {progress ? (
          <p className="font-mono text-sm text-navy">{progress}</p>
        ) : (
          <>
            <p className="text-text-secondary">
              Drop PDFs here — a whole production at once is fine.
            </p>
            {maxUploadMB !== null && (
              <p className="mt-1 text-xs text-text-secondary">
                Up to {maxUploadMB} MB each. A larger file has to be split
                first, at a boundary you can see — one year or one account per
                file.
              </p>
            )}
            <label className="mt-3 inline-block cursor-pointer text-sm text-navy underline">
              <input
                type="file"
                multiple
                accept="application/pdf"
                className="hidden"
                onChange={(e) => {
                  void take(Array.from(e.target.files ?? []))
                  e.target.value = ''
                }}
              />
              or choose files
            </label>
          </>
        )}
      </div>

      {error && (
        <div className="card mb-4 border-danger">
          <p className="whitespace-pre-line text-sm text-danger">{error}</p>
        </div>
      )}

      {verdicts.length > 0 && (
        <div className="mb-6 space-y-3">
          {verdicts.map((v) => <Verdict key={v.source_document_id} v={v} />)}
        </div>
      )}

      <div className="card">
        <h2 className="mb-3 font-display text-navy">On record</h2>
        {rows.length === 0 ? (
          <p className="text-sm text-text-secondary">Nothing received yet.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-text-secondary">
                <th className="py-2">File as served</th>
                <th className="py-2">Received</th>
                <th className="py-2 text-right">Pages</th>
                <th className="py-2 text-right">Size</th>
                <th className="py-2 text-right">Documents</th>
                <th className="py-2"></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.source_document_id} className="border-b border-border/50">
                  <td className="py-2 pr-2">{r.original_filename}</td>
                  <td className="py-2 font-mono text-text-secondary">{r.received_on}</td>
                  <td className="py-2 text-right font-mono">{r.page_count ?? '—'}</td>
                  <td className="py-2 text-right font-mono">
                    {(r.byte_count / 1e6).toFixed(1)} MB
                  </td>
                  <td className="py-2 text-right font-mono">
                    {/* Three states, each true of the row it sits on. A
                        fourth -- "started" -- was shown for every file once
                        its survey finished, including ones nobody had
                        touched. */}
                    {r.documents > 0 ? r.documents : !r.surveyed ? (
                      <span className="text-text-secondary">reading…</span>
                    ) : (
                      <span className="text-warning">not split</span>
                    )}
                  </td>
                  <td className="py-2 text-right">
                    {r.surveyed ? (
                      <Link to={`/app/bench/${r.source_document_id}`}
                            className="text-navy underline">
                        {r.documents > 0 ? 'open bench' : 'split'}
                      </Link>
                    ) : (
                      // Offering "split" on a file that cannot be split yet is
                      // what sent somebody to a grid announcing it was finished.
                      <span className="text-text-secondary">not ready</span>
                    )}
                    <button type="button"
                            className="ml-3 text-navy underline"
                            onClick={() => void reroute(r.source_document_id)}>
                      re-check
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

/**
 * The routing verdict, with the numbers behind it.
 *
 * The figures are shown alongside the decision deliberately. "This goes to the
 * bench" is an instruction; "8% of pages carry their own pagination" is a
 * reason, and somebody reading it can tell when the routing is wrong.
 */
function Verdict({ v }: { v: ReceivedUpload }) {
  const tone = v.route === 'automatic' ? 'border-success'
    : v.route === 'ocr_first' ? 'border-warning'
    : v.route === null ? 'border-border' : 'border-navy'

  return (
    <div className={`card border-l-4 ${tone}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-display text-navy">{v.original_filename}</h3>
        {v.already_held && (
          <span className="text-xs text-text-secondary">
            already on this matter — nothing stored again
          </span>
        )}
      </div>

      <p className="mt-1 text-sm">{v.reason}</p>

      {v.route === null ? (
        <p className="mt-3 text-sm text-text-secondary">
          The file is safe. Reload in a moment to see what it is.
        </p>
      ) : (
      <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 font-mono text-xs text-text-secondary">
        <span>{v.pages} pages</span>
        <span>{Math.round(100 * v.text_share)}% readable text</span>
        <span>{Math.round(100 * v.pagination_coverage)}% self-paginating</span>
        <span>
          {v.likely_documents} document{v.likely_documents === 1 ? '' : 's'} at least
        </span>
      </div>
      )}

      {v.route === 'bench' && (
        <Link to={`/app/bench/${v.source_document_id}`}
              className="btn-primary mt-3 inline-block">
          Split it
        </Link>
      )}
      {v.route === 'ocr_first' && (
        <p className="mt-3 text-sm text-text-secondary">
          Adding a text layer is not built yet. Until it is, OCR the file
          elsewhere and upload the result — or split it by hand on the bench,
          where the thumbnails are all you need.
        </p>
      )}
      {v.route === 'automatic' && (
        <p className="mt-3 text-sm text-text-secondary">
          The automatic split is not built yet. For now, split it on the bench —
          it should go quickly, since nearly every page says which document it
          belongs to.
        </p>
      )}
    </div>
  )
}
