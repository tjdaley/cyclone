import { useEffect, useState, FormEvent } from 'react'
import { getMatters, getBillingEntries, parseNLBillingEntry, apiFetch } from '../../lib/api'

interface Matter {
  id: number
  matter_name: string
  status: string
}

interface BillingEntry {
  id: number
  entry_type: string
  description: string
  hours: number | null
  rate: number | null
  amount: number
  entry_date: string
  billed: boolean
}

interface ParsedPreview {
  entry_type: string
  description: string
  hours: number | null
  rate: number | null
  amount: number
  matter_id: number | null
}

function formatCurrency(n: number) {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n)
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

export default function BillingPage() {
  const [matters, setMatters]       = useState<Matter[]>([])
  const [matterId, setMatterId]     = useState<number | null>(null)
  const [entries, setEntries]       = useState<BillingEntry[]>([])
  const [loadingEntries, setLoadingEntries] = useState(false)

  // NL billing input
  const [nlText, setNlText]         = useState('')
  const [parsing, setParsing]       = useState(false)
  const [preview, setPreview]       = useState<ParsedPreview | null>(null)
  const [nlError, setNlError]       = useState<string | null>(null)
  const [committing, setCommitting] = useState(false)

  useEffect(() => {
    getMatters().then((data: unknown) => {
      const ms = data as Matter[]
      setMatters(ms.filter(m => m.status === 'active'))
    }).catch(console.error)
  }, [])

  useEffect(() => {
    if (!matterId) return
    setLoadingEntries(true)
    getBillingEntries(matterId)
      .then((data: unknown) => setEntries(data as BillingEntry[]))
      .catch(console.error)
      .finally(() => setLoadingEntries(false))
  }, [matterId])

  async function handleParse(e: FormEvent) {
    e.preventDefault()
    if (!nlText.trim()) return
    setParsing(true)
    setNlError(null)
    setPreview(null)
    try {
      const result = await parseNLBillingEntry(nlText) as ParsedPreview
      setPreview(result)
    } catch (err) {
      setNlError(err instanceof Error ? err.message : 'Parse failed')
    } finally {
      setParsing(false)
    }
  }

  async function handleCommit() {
    if (!preview || !matterId) return
    setCommitting(true)
    try {
      await apiFetch(`/api/v1/billing/entries`, {
        method: 'POST',
        body: JSON.stringify({ ...preview, matter_id: matterId }),
      })
      setPreview(null)
      setNlText('')
      // Refresh entries
      const data = await getBillingEntries(matterId)
      setEntries(data as BillingEntry[])
    } catch (err) {
      setNlError(err instanceof Error ? err.message : 'Commit failed')
    } finally {
      setCommitting(false)
    }
  }

  const unbilledTotal = entries.filter(e => !e.billed).reduce((s, e) => s + e.amount, 0)

  return (
    <div className="px-6 py-8 max-w-5xl mx-auto">
      <div className="mb-8">
        <h1 className="font-display text-3xl text-navy">Billing</h1>
        <p className="text-text-secondary mt-1">Enter time naturally or review unbilled entries.</p>
      </div>

      {/* Matter selector */}
      <div className="card p-5 mb-6">
        <label className="label" htmlFor="matter-select">Select matter</label>
        <select
          id="matter-select"
          className="input mt-1"
          value={matterId ?? ''}
          onChange={e => setMatterId(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">— choose a matter —</option>
          {matters.map(m => (
            <option key={m.id} value={m.id}>{m.matter_name}</option>
          ))}
        </select>
      </div>

      {matterId && (
        <>
          {/* Natural language entry */}
          <div className="card p-5 mb-6">
            <h2 className="font-semibold text-navy mb-3">Natural language entry</h2>
            <form onSubmit={handleParse} className="flex gap-3">
              <input
                className="input flex-1"
                placeholder={`e.g. "bill .5 for drafting settlement proposal"`}
                value={nlText}
                onChange={e => setNlText(e.target.value)}
              />
              <button type="submit" className="btn-primary" disabled={parsing || !nlText.trim()}>
                {parsing ? 'Parsing…' : 'Parse'}
              </button>
            </form>

            {nlError && (
              <p className="mt-3 text-sm text-red-600">{nlError}</p>
            )}

            {preview && (
              <div className="mt-4 rounded-lg border border-border bg-off-white p-4">
                <p className="text-xs text-text-secondary uppercase tracking-widest font-semibold mb-3">Preview — review before committing</p>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm mb-4">
                  <div>
                    <p className="text-text-secondary text-xs mb-0.5">Type</p>
                    <p className="font-medium text-navy capitalize">{preview.entry_type}</p>
                  </div>
                  {preview.hours != null && (
                    <div>
                      <p className="text-text-secondary text-xs mb-0.5">Hours</p>
                      <p className="font-medium text-navy">{preview.hours}</p>
                    </div>
                  )}
                  {preview.rate != null && (
                    <div>
                      <p className="text-text-secondary text-xs mb-0.5">Rate</p>
                      <p className="font-medium text-navy">{formatCurrency(preview.rate)}/hr</p>
                    </div>
                  )}
                  <div>
                    <p className="text-text-secondary text-xs mb-0.5">Amount</p>
                    <p className="font-semibold text-navy">{formatCurrency(preview.amount)}</p>
                  </div>
                </div>
                <p className="text-sm text-navy mb-4">{preview.description}</p>
                <div className="flex gap-3">
                  <button onClick={handleCommit} className="btn-primary" disabled={committing}>
                    {committing ? 'Committing…' : 'Commit entry'}
                  </button>
                  <button onClick={() => setPreview(null)} className="btn-secondary">
                    Discard
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Entry list */}
          <div className="card overflow-hidden">
            <div className="px-5 py-4 border-b border-border flex items-center justify-between">
              <h2 className="font-semibold text-navy">Unbilled entries</h2>
              <span className="text-sm font-semibold text-navy">{formatCurrency(unbilledTotal)}</span>
            </div>

            {loadingEntries && (
              <div className="px-5 py-10 text-center text-text-secondary text-sm">Loading…</div>
            )}

            {!loadingEntries && entries.filter(e => !e.billed).length === 0 && (
              <div className="px-5 py-10 text-center text-text-secondary text-sm">No unbilled entries.</div>
            )}

            {!loadingEntries && entries.filter(e => !e.billed).length > 0 && (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-off-white">
                    <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Date</th>
                    <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Description</th>
                    <th className="text-left px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide hidden md:table-cell">Type</th>
                    <th className="text-right px-5 py-3 text-xs font-semibold text-text-secondary uppercase tracking-wide">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.filter(e => !e.billed).map(e => (
                    <tr key={e.id} className="border-b border-border last:border-0 hover:bg-off-white/60">
                      <td className="px-5 py-3 text-text-secondary whitespace-nowrap">{formatDate(e.entry_date)}</td>
                      <td className="px-5 py-3 text-navy">{e.description}</td>
                      <td className="px-5 py-3 text-text-secondary hidden md:table-cell capitalize">{e.entry_type}</td>
                      <td className="px-5 py-3 text-right font-medium text-navy">{formatCurrency(e.amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  )
}
