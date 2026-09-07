import { useCallback, useEffect, useState } from 'react'
import {
  getPayeeClassifications, createPayeeClassification,
  updatePayeeClassification, deletePayeeClassification,
} from '../../lib/api'
import { HOLDS_LABEL } from '../../types'
import type { PayeeClassification, PayeeClassificationPayload } from '../../types'

const KINDS = ['credit_card', 'loan', 'mortgage', 'line_of_credit', 'other']
const HOLDS = Object.keys(HOLDS_LABEL)

const CLASSIFICATION: Record<string, { label: string; tone: string; blurb: string }> = {
  creditor: {
    label: 'Creditor',
    tone: 'bg-red-100 text-red-800',
    blurb: 'Payments to this payee name a debt. It appears on the report and in the exhibit.',
  },
  custodian: {
    label: 'Holds value',
    tone: 'bg-blue-100 text-blue-800',
    blurb: 'This counterparty holds a balance, a portfolio, or crypto for our party. '
      + 'Traffic in either direction proves the account exists.',
  },
  not_creditor: {
    label: 'Not a creditor',
    tone: 'bg-gray-100 text-gray-700',
    blurb: 'A vendor. Removes the payee from the report — permanently, and on every '
      + 'matter if it is firm-wide.',
  },
}

/**
 * Review and reverse the standing rulings behind the creditor report.
 *
 * The rulings were previously write-only: the triage buttons on the report
 * created them and nothing could show, change, or undo one. A suppression
 * nobody can find again is the worst shape this data can take, because
 * `not_creditor` removes a payee from a report that backs a motion to compel —
 * silently, and for good.
 *
 * **Firm-wide rulings ARE editable here, unlike firm-wide tags.** That is a
 * deliberate difference. A tag is vocabulary and renaming one is cosmetic; a
 * ruling decides whether an account reaches a filing, so the person who finds
 * the wrong one is the person who needs to fix it. What the screen owes them
 * instead is a warning they cannot miss before they change every open case.
 *
 * Two layers are shown separately rather than merged, for the reason the
 * category-rule and tag editors show one at a time: a list that blurred them
 * would offer to change the firm's answer from inside a matter, and nobody
 * would know which they had altered.
 */
export default function PayeeRulingManager({ matterId, onChanged }: {
  matterId: number
  onChanged?: () => void
}) {
  const [firm, setFirm] = useState<PayeeClassification[]>([])
  const [mine, setMine] = useState<PayeeClassification[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState<number | null>(null)
  const [draft, setDraft] = useState<PayeeClassificationPayload | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [firmRows, matterRows] = await Promise.all([
        getPayeeClassifications(),
        getPayeeClassifications(matterId),
      ])
      setFirm(firmRows)
      setMine(matterRows)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the rulings')
    } finally {
      setLoading(false)
    }
  }, [matterId])

  useEffect(() => { void load() }, [load])

  function startEdit(row: PayeeClassification) {
    setEditing(row.id)
    setDraft({
      pattern: row.pattern,
      classification: row.classification as PayeeClassificationPayload['classification'],
      matter_id: row.matter_id,
      creditor_name: row.creditor_name,
      creditor_type: row.creditor_type,
      holds: row.holds ?? [],
      note: row.note,
      is_active: row.is_active,
    })
  }

  async function save(row: PayeeClassification) {
    if (!draft) return
    // A firm-wide ruling governs every open case. Changing one from inside a
    // matter is legitimate and sometimes necessary — it is how a bad seed gets
    // fixed — but it must never happen because somebody thought they were
    // editing this case.
    if (row.is_firm_wide && !window.confirm(
      `"${draft.pattern}" is a firm-wide ruling. Saving changes it for every matter `
      + 'in the firm, not just this one.\n\nTo change it only here, delete nothing and '
      + 'add a matter ruling for the same payee instead — it overrides the firm answer.',
    )) return

    setBusy(true)
    try {
      // The server rejects `holds` on anything but a custodian, so the payload
      // is shaped to the classification rather than sent whole.
      const payload: PayeeClassificationPayload = {
        ...draft,
        holds: draft.classification === 'custodian' ? (draft.holds ?? []) : [],
        creditor_type: draft.classification === 'creditor' ? draft.creditor_type : null,
      }
      await updatePayeeClassification(row.id, payload)
      setEditing(null); setDraft(null)
      await load()
      onChanged?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the ruling')
    } finally { setBusy(false) }
  }

  async function remove(row: PayeeClassification) {
    const scope = row.is_firm_wide
      ? 'every matter in the firm'
      : 'this matter'
    if (!window.confirm(
      `Delete the ruling for "${row.pattern}"?\n\nIt applies to ${scope}. `
      + 'The payee returns to the queue of things nobody has ruled on; nothing '
      + 'else changes, because the report is recomputed from the transactions '
      + 'every time it is read.',
    )) return
    setBusy(true)
    try {
      await deletePayeeClassification(row.id)
      await load()
      onChanged?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete the ruling')
    } finally { setBusy(false) }
  }

  async function addHere(payload: PayeeClassificationPayload) {
    setBusy(true)
    try {
      await createPayeeClassification({ ...payload, matter_id: matterId })
      await load()
      onChanged?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not add the ruling')
    } finally { setBusy(false) }
  }

  function toggleHold(kind: string) {
    if (!draft) return
    const current = draft.holds ?? []
    setDraft({
      ...draft,
      holds: current.includes(kind)
        ? current.filter(k => k !== kind)
        : [...current, kind],
    })
  }

  function renderRow(row: PayeeClassification) {
    const meta = CLASSIFICATION[row.classification]
    if (editing === row.id && draft) {
      return (
        <tr key={row.id} className="border-b border-border bg-off-white align-top">
          <td colSpan={4} className="py-3 px-3">
            <div className="space-y-2">
              <div className="flex flex-wrap gap-2 items-center">
                <input className="input text-sm w-48" value={draft.pattern}
                  onChange={e => setDraft({ ...draft, pattern: e.target.value })}
                  placeholder="Payee pattern" />
                <select className="input text-sm w-40" value={draft.classification}
                  onChange={e => setDraft({
                    ...draft,
                    classification: e.target.value as PayeeClassificationPayload['classification'],
                  })}>
                  {Object.entries(CLASSIFICATION).map(([value, c]) => (
                    <option key={value} value={value}>{c.label}</option>
                  ))}
                </select>
                <input className="input text-sm w-52"
                  value={draft.creditor_name ?? ''}
                  onChange={e => setDraft({ ...draft, creditor_name: e.target.value || null })}
                  placeholder="Name for a motion" />
                {draft.classification === 'creditor' && (
                  <select className="input text-sm w-40" value={draft.creditor_type ?? ''}
                    onChange={e => setDraft({ ...draft, creditor_type: e.target.value || null })}>
                    <option value="">Kind of debt…</option>
                    {KINDS.map(k => (
                      <option key={k} value={k}>{k.replace(/_/g, ' ')}</option>
                    ))}
                  </select>
                )}
              </div>

              {/* A custodian must say what it holds — that set IS the request
                  for production, and one platform commonly carries several. */}
              {draft.classification === 'custodian' && (
                <div className="flex flex-wrap gap-1.5 items-center">
                  <span className="text-xs text-text-secondary mr-1">Ask for:</span>
                  {HOLDS.map(kind => (
                    <button key={kind} type="button" onClick={() => toggleHold(kind)}
                      className={`text-xs px-2 py-1 rounded border ${
                        (draft.holds ?? []).includes(kind)
                          ? 'bg-blue-100 text-blue-800 border-blue-200'
                          : 'bg-white text-text-secondary border-border'}`}>
                      {HOLDS_LABEL[kind]}
                    </button>
                  ))}
                </div>
              )}

              <div className="flex flex-wrap gap-2 items-center">
                <input className="input text-sm flex-1 min-w-[16rem]" value={draft.note ?? ''}
                  onChange={e => setDraft({ ...draft, note: e.target.value || null })}
                  placeholder="Why — for whoever inherits this" />
                <label className="text-xs text-text-secondary flex items-center gap-1">
                  <input type="checkbox" checked={draft.is_active ?? true}
                    onChange={e => setDraft({ ...draft, is_active: e.target.checked })} />
                  Active
                </label>
                <button className="btn-primary text-sm" disabled={busy}
                  onClick={() => void save(row)}>{busy ? 'Saving…' : 'Save'}</button>
                <button className="btn-secondary text-sm"
                  onClick={() => { setEditing(null); setDraft(null) }}>Cancel</button>
              </div>

              {row.is_firm_wide && (
                <p className="text-xs text-warning">
                  This ruling governs every matter in the firm. To change it for this case
                  only, leave it alone and add a matter ruling for the same payee — a matter
                  ruling overrides the firm's answer.
                </p>
              )}
            </div>
          </td>
        </tr>
      )
    }

    return (
      <tr key={row.id} className="border-b border-border last:border-0 align-top">
        <td className="py-2 pr-4">
          <div className="font-medium text-navy">
            {row.creditor_name ?? row.pattern}
            {!row.is_active && (
              <span className="ml-2 text-[11px] text-text-secondary">(retired)</span>
            )}
          </div>
          <div className="text-xs text-text-secondary font-mono">{row.pattern}</div>
          {row.note && (
            <div className="text-xs text-text-secondary mt-0.5">{row.note}</div>
          )}
        </td>
        <td className="py-2 pr-4">
          <span className={`text-[11px] px-1.5 py-0.5 rounded ${
            meta?.tone ?? 'bg-off-white text-text-secondary'}`}>
            {meta?.label ?? row.classification}
          </span>
        </td>
        <td className="py-2 pr-4 text-xs text-text-secondary">
          {row.classification === 'custodian'
            ? (row.holds ?? []).map(k => HOLDS_LABEL[k] ?? k).join(', ')
            : row.creditor_type?.replace(/_/g, ' ') ?? '—'}
        </td>
        <td className="py-2 text-right whitespace-nowrap">
          <button className="text-xs text-navy hover:underline mr-3"
            onClick={() => startEdit(row)}>Edit</button>
          <button className="text-xs text-danger hover:underline" disabled={busy}
            onClick={() => void remove(row)}>{busy ? 'Working…' : 'Delete'}</button>
        </td>
      </tr>
    )
  }

  function renderTable(rows: PayeeClassification[], empty: string) {
    if (rows.length === 0) {
      return <p className="text-sm text-text-secondary py-2">{empty}</p>
    }
    return (
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide
                           text-text-secondary border-b border-border">
              <th className="py-2 pr-4 font-medium">Payee</th>
              <th className="py-2 pr-4 font-medium">Ruling</th>
              <th className="py-2 pr-4 font-medium">Asks for</th>
              <th className="py-2 font-medium text-right">&nbsp;</th>
            </tr>
          </thead>
          <tbody>{rows.map(renderRow)}</tbody>
        </table>
      </div>
    )
  }

  const sorted = (rows: PayeeClassification[]) =>
    [...rows].sort((a, b) => a.pattern.localeCompare(b.pattern))

  return (
    <div className="card space-y-4">
      <div>
        <h3 className="text-sm font-medium text-navy">Standing rulings about payees</h3>
        <p className="text-xs text-text-secondary mt-1">
          What the firm has decided a payee is. A ruling is an opinion, not evidence —
          deleting one changes nothing except what the next report says, because the
          report is recomputed from the transactions every time it is read.
        </p>
      </div>

      {error && <p className="text-sm text-danger">{error}</p>}
      {loading && <p className="text-sm text-text-secondary">Loading…</p>}

      {!loading && (
        <>
          <div className="space-y-1">
            <h4 className="text-xs uppercase tracking-wide text-text-secondary">
              This matter — {mine.length} ruling{mine.length === 1 ? '' : 's'}
            </h4>
            {renderTable(sorted(mine),
              'No rulings specific to this matter. Anything decided here overrides the '
              + 'firm-wide answer for this case alone.')}
          </div>

          <div className="space-y-1 pt-2">
            <h4 className="text-xs uppercase tracking-wide text-text-secondary">
              Firm-wide — {firm.length} ruling{firm.length === 1 ? '' : 's'}
            </h4>
            <p className="text-xs text-warning">
              These apply to every matter. Editing one here changes what every other case
              reports; to change an answer for this case only, add a matter ruling above.
            </p>
            {renderTable(sorted(firm), 'No firm-wide rulings yet.')}
          </div>

          {/* The quickest correction there is: a firm-wide answer that is wrong
              for this household. Adding beats editing, because it leaves every
              other case alone. */}
          <NewRuling busy={busy} onAdd={addHere} />
        </>
      )}
    </div>
  )
}

/** Add a ruling scoped to this matter. */
function NewRuling({ busy, onAdd }: {
  busy: boolean
  onAdd: (payload: PayeeClassificationPayload) => Promise<void>
}) {
  const [pattern, setPattern] = useState('')
  const [classification, setClassification] =
    useState<PayeeClassificationPayload['classification']>('creditor')
  const [name, setName] = useState('')
  const [holds, setHolds] = useState<string[]>([])

  const ready = pattern.trim().length >= 3
    && (classification !== 'custodian' || holds.length > 0)

  return (
    <div className="border-t border-border pt-3 space-y-2">
      <h4 className="text-xs uppercase tracking-wide text-text-secondary">
        Add a ruling for this matter
      </h4>
      <div className="flex flex-wrap gap-2 items-center">
        <input className="input text-sm w-48" value={pattern}
          onChange={e => setPattern(e.target.value)}
          placeholder="Payee, three characters or more" />
        <select className="input text-sm w-40" value={classification}
          onChange={e => {
            setClassification(e.target.value as PayeeClassificationPayload['classification'])
            setHolds([])
          }}>
          {Object.entries(CLASSIFICATION).map(([value, c]) => (
            <option key={value} value={value}>{c.label}</option>
          ))}
        </select>
        <input className="input text-sm w-52" value={name}
          onChange={e => setName(e.target.value)}
          placeholder="Name for a motion" />
        <button className="btn-primary text-sm" disabled={busy || !ready}
          onClick={() => {
            void onAdd({
              pattern: pattern.trim(),
              classification,
              creditor_name: name.trim() || null,
              holds: classification === 'custodian' ? holds : [],
            })
            setPattern(''); setName(''); setHolds([])
          }}>
          {busy ? 'Saving…' : 'Add'}
        </button>
      </div>

      {classification === 'custodian' && (
        <div className="flex flex-wrap gap-1.5 items-center">
          <span className="text-xs text-text-secondary mr-1">Ask for:</span>
          {HOLDS.map(kind => (
            <button key={kind} type="button"
              onClick={() => setHolds(h =>
                h.includes(kind) ? h.filter(k => k !== kind) : [...h, kind])}
              className={`text-xs px-2 py-1 rounded border ${
                holds.includes(kind)
                  ? 'bg-blue-100 text-blue-800 border-blue-200'
                  : 'bg-white text-text-secondary border-border'}`}>
              {HOLDS_LABEL[kind]}
            </button>
          ))}
        </div>
      )}

      <p className="text-xs text-text-secondary">{CLASSIFICATION[classification].blurb}</p>
    </div>
  )
}
