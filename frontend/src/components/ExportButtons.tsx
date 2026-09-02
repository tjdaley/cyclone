import { useState } from 'react'
import { saveFile } from '../lib/api'
import type { DownloadedFile, ExportFormat } from '../types'

const FORMATS: ExportFormat[] = ['csv', 'md', 'docx', 'pdf']

/**
 * Take a report out of Cyclone, in any of the four formats.
 *
 * Shared rather than copied because every report Cyclone grows needs the same
 * control, and two copies would drift — one of them would quietly stop showing
 * the caption warnings, which are the part nobody notices is missing.
 *
 * The caller supplies only the request; everything about presenting the result
 * lives here. That includes the warnings: the exhibit formats build a case
 * caption from the matter, and whatever the matter could not supply was printed
 * as a blank. Saying so at the moment of download is the only thing standing
 * between a blank and a filing.
 */
export default function ExportButtons({
  onExport,
  name,
  onNameChange,
  count,
  hint,
  disabled = false,
}: {
  /** Runs the export. Errors are caught and shown here. */
  onExport: (format: ExportFormat) => Promise<DownloadedFile>
  /** Exhibit name — titles the document and names the file. */
  name: string
  onNameChange: (next: string) => void
  /** Rows the export will contain, shown so nobody exports an empty document. */
  count: number
  /** One line explaining what the formats differ in. */
  hint?: string
  disabled?: boolean
}) {
  const [busy, setBusy] = useState<ExportFormat | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])

  async function run(format: ExportFormat) {
    setBusy(format)
    setError(null)
    setWarnings([])
    try {
      const file = await onExport(format)
      saveFile(file)
      setWarnings(file.warnings)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Export failed')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="border-t border-border pt-3 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <label className="text-xs text-text-secondary" htmlFor="exhibit-name">Export as</label>
        <input id="exhibit-name" className="input text-sm py-1 w-64" value={name}
          onChange={e => onNameChange(e.target.value)} aria-label="Exhibit name" />
        {FORMATS.map(format => (
          <button key={format} type="button" className="btn-secondary text-xs px-3 py-1"
            disabled={disabled || busy !== null}
            onClick={() => void run(format)}>
            {busy === format ? 'Building…' : format.toUpperCase()}
          </button>
        ))}
        <span className="text-xs text-text-secondary">
          {count.toLocaleString()} row{count === 1 ? '' : 's'}
          {hint ? ` · ${hint}` : ''}
        </span>
      </div>

      {error && (
        <div className="p-2 border border-red-300 bg-red-50 text-xs text-red-700 rounded">{error}</div>
      )}

      {warnings.length > 0 && (
        <div className="p-2 border border-amber-300 bg-amber-50 text-xs text-amber-900 rounded space-y-1">
          <p className="font-medium">The exhibit downloaded with blanks in its caption:</p>
          <ul className="list-disc ml-4">
            {warnings.map((warning, i) => <li key={i}>{warning}</li>)}
          </ul>
        </div>
      )}
    </div>
  )
}
