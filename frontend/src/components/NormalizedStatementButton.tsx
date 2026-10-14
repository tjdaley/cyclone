import { useEffect, useRef, useState } from 'react'
import { exportNormalizedStatement, saveFile } from '../lib/api'
import type { ExportFormat } from '../types'

const FORMATS: { format: ExportFormat; label: string }[] = [
  { format: 'pdf', label: 'PDF' },
  { format: 'docx', label: 'Word' },
  { format: 'md', label: 'Markdown' },
  { format: 'csv', label: 'CSV' },
]

/**
 * Download one statement redrawn as a statement, for hand-checking.
 *
 * A row-sized control rather than the shared `ExportButtons` panel, for the
 * same reason the PDF button is: this belongs beside a single statement, and a
 * block with its own name field and four buttons on every row of a twelve-month
 * account would swamp the list it sits in. The four formats are one click
 * further in, behind a menu that stays shut.
 *
 * **It is not an exhibit and does not pretend to be.** The document carries no
 * court caption, and the Rule 1006 notice is replaced by a disclaimer saying it
 * is a pro-forma copy and not an evidentiary substitute — the wording is on the
 * server, so it cannot drift from the document it describes.
 */
export default function NormalizedStatementButton({ statementId, disabled = false }: {
  statementId: number
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState<ExportFormat | null>(null)
  const [error, setError] = useState<string | null>(null)
  const box = useRef<HTMLSpanElement>(null)

  // Close on a click anywhere else, the way a menu is expected to behave.
  useEffect(() => {
    if (!open) return
    function away(event: MouseEvent) {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', away)
    return () => document.removeEventListener('mousedown', away)
  }, [open])

  async function run(format: ExportFormat) {
    setBusy(format)
    setError(null)
    try {
      saveFile(await exportNormalizedStatement(statementId, format))
      setOpen(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not build the statement')
    } finally {
      setBusy(null)
    }
  }

  return (
    <span className="relative inline-block" ref={box}>
      <button type="button"
        className="text-xs text-navy underline disabled:no-underline disabled:opacity-40"
        disabled={disabled}
        title="A pro-forma copy of this statement, to check against the original"
        onClick={() => setOpen(v => !v)}>
        Normalized
      </button>
      {open && (
        <span className="absolute z-20 left-0 mt-1 w-44 rounded border border-border bg-white
                         shadow-lg p-1 block">
          <span className="block px-2 py-1 text-[11px] text-text-secondary leading-snug">
            Pro-forma copy for checking against the original.
          </span>
          {FORMATS.map(({ format, label }) => (
            <button key={format} type="button" disabled={busy !== null}
              className="block w-full text-left px-2 py-1 text-xs text-navy hover:bg-navy/5
                         disabled:opacity-40"
              onClick={() => void run(format)}>
              {busy === format ? 'Building…' : label}
            </button>
          ))}
          {error && <span className="block px-2 py-1 text-[11px] text-red-700">{error}</span>}
        </span>
      )}
    </span>
  )
}
