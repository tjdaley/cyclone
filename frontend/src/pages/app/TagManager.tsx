import { useState } from 'react'
import {
  createMatterTag, updateTransactionTag, deleteTransactionTag,
} from '../../lib/api'
import type { TransactionTag } from '../../types'

export const TAG_COLOR: Record<string, string> = {
  red:    'bg-red-100 text-red-800 border-red-200',
  amber:  'bg-amber-100 text-amber-800 border-amber-200',
  blue:   'bg-blue-100 text-blue-800 border-blue-200',
  purple: 'bg-purple-100 text-purple-800 border-purple-200',
  green:  'bg-green-100 text-green-800 border-green-200',
  gray:   'bg-gray-100 text-gray-700 border-gray-200',
}
export const TAG_COLORS = Object.keys(TAG_COLOR)

export function tagClass(tag: TransactionTag): string {
  return TAG_COLOR[tag.color ?? 'gray'] ?? TAG_COLOR.gray
}

/**
 * Create, rename, recolour, and retire the tags on a matter.
 *
 * Two layers live in one table and the difference matters to whoever is
 * tagging: a **matter tag** states a theory about this case ("Waste: Sister's
 * Wedding") and belongs to it; a **firm-wide tag** is offered on every case, so
 * renaming one silently rewrites the vocabulary of every other matter in the
 * firm. Firm-wide tags are therefore shown here but not editable — changing
 * them is a firm decision, not a case decision.
 *
 * Editing is in place rather than in a dialog. Tags get corrected while
 * somebody is mid-way through classifying a production, and a modal that closes
 * the list underneath them loses their place.
 */
export default function TagManager({ matterId, tags, onChanged, onError }: {
  matterId: number
  tags: TransactionTag[]
  onChanged: () => Promise<void>
  onError: (message: string) => void
}) {
  const [label, setLabel] = useState('')
  const [description, setDescription] = useState('')
  const [color, setColor] = useState('gray')
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState<number | null>(null)

  async function create() {
    if (!label.trim()) return
    setBusy(true)
    try {
      await createMatterTag(matterId, {
        label: label.trim(), description: description.trim() || null, color,
      })
      setLabel(''); setDescription(''); setColor('gray')
      await onChanged()
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Could not create the tag')
    } finally { setBusy(false) }
  }

  async function save(tag: TransactionTag, patch: Partial<TransactionTag>) {
    setBusy(true)
    try {
      await updateTransactionTag(tag.id, {
        label: (patch.label ?? tag.label).trim(),
        description: (patch.description ?? tag.description) || null,
        color: patch.color ?? tag.color,
        is_active: patch.is_active ?? tag.is_active,
      })
      setEditing(null)
      await onChanged()
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Could not save the tag')
    } finally { setBusy(false) }
  }

  async function remove(tag: TransactionTag) {
    setBusy(true)
    try {
      await deleteTransactionTag(tag.id)
      await onChanged()
    } catch (err) {
      // The API refuses with a 409 while the tag is in use. That count is the
      // whole point of the guard, so surface it and make the user confirm.
      const message = err instanceof Error ? err.message : 'Could not delete the tag'
      if (message.includes('force=true')) {
        if (window.confirm(
          `${message}\n\nDelete it anyway? The transactions stay, but this tag comes off all of them ` +
          `— any exhibit built from it dissolves.`,
        )) {
          try {
            await deleteTransactionTag(tag.id, true)
            await onChanged()
          } catch (forced) {
            onError(forced instanceof Error ? forced.message : 'Could not delete the tag')
          }
        }
      } else {
        onError(message)
      }
    } finally { setBusy(false) }
  }

  const matterTags = tags.filter(t => t.matter_id !== null)
  const firmTags = tags.filter(t => t.matter_id === null)

  return (
    <div className="border border-border rounded p-4 space-y-5 bg-off-white/50">
      <p className="text-xs text-text-secondary max-w-3xl">
        A tag groups transactions into an exhibit. One line can carry several —
        that is what separates tags from a category, of which a line has exactly one.
      </p>

      {/* ── This matter's tags ── */}
      <div className="space-y-2">
        <h3 className="text-sm font-medium text-navy">
          This matter&rsquo;s tags
          <span className="ml-2 text-xs font-normal text-text-secondary">
            {matterTags.length} · used only on this case
          </span>
        </h3>

        {matterTags.length === 0 ? (
          <p className="text-xs text-text-secondary">
            None yet. The firm-wide tags below are already available on every line.
          </p>
        ) : (
          <div className="space-y-1">
            {matterTags.map(tag => (
              editing === tag.id ? (
                <TagEditor key={tag.id} tag={tag} busy={busy}
                  onCancel={() => setEditing(null)}
                  onSave={patch => save(tag, patch)} />
              ) : (
                <div key={tag.id}
                  className="flex flex-wrap items-center gap-2 py-1.5 px-2 rounded hover:bg-white">
                  <span className={`text-xs rounded-full px-2.5 py-1 border ${tagClass(tag)}`}>
                    {tag.label}
                  </span>
                  {!tag.is_active && (
                    <span className="text-xs text-text-secondary italic">retired</span>
                  )}
                  <span className="text-xs text-text-secondary flex-1 min-w-[8rem]">
                    {tag.description || <span className="italic opacity-60">no description</span>}
                  </span>
                  <span className="text-xs text-text-secondary tabular-nums">
                    {tag.usage_count ?? 0} line{tag.usage_count === 1 ? '' : 's'}
                  </span>
                  <button type="button" className="text-xs text-navy underline" disabled={busy}
                    onClick={() => setEditing(tag.id)}>Edit</button>
                  <button type="button" className="text-xs text-danger underline" disabled={busy}
                    onClick={() => void remove(tag)}>Delete</button>
                </div>
              )
            ))}
          </div>
        )}
      </div>

      {/* ── Add one ── */}
      <div className="space-y-2 border-t border-border pt-4">
        <h3 className="text-sm font-medium text-navy">Add a tag to this matter</h3>
        <div className="grid gap-2 md:grid-cols-[1fr_1.5fr_auto_auto]">
          <input className="input" placeholder="Waste: Sister's Wedding" value={label}
            aria-label="Tag label" onChange={e => setLabel(e.target.value)} />
          <input className="input" placeholder="What this tag means, for whoever tags next"
            value={description} aria-label="Tag description"
            onChange={e => setDescription(e.target.value)} />
          <select className="input" value={color} aria-label="Tag colour"
            onChange={e => setColor(e.target.value)}>
            {TAG_COLORS.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
          <button type="button" className="btn-primary" disabled={busy || !label.trim()}
            onClick={() => void create()}>Add tag</button>
        </div>
        <p className="text-xs text-text-secondary">
          Write the description for the person who tags next — six months on, &ldquo;Waste&rdquo;
          means nothing without one.
        </p>
      </div>

      {/* ── Firm-wide ── */}
      {firmTags.length > 0 && (
        <div className="space-y-2 border-t border-border pt-4">
          <h3 className="text-sm font-medium text-navy">
            Firm-wide tags
            <span className="ml-2 text-xs font-normal text-text-secondary">
              {firmTags.length} · offered on every matter
            </span>
          </h3>
          <div className="flex flex-wrap gap-2">
            {firmTags.map(tag => (
              <span key={tag.id}
                className={`text-xs rounded-full px-2.5 py-1 border ${tagClass(tag)}`}
                title={tag.description ?? undefined}>
                {tag.label}
                {tag.usage_count ? (
                  <span className="ml-1.5 opacity-70 tabular-nums">{tag.usage_count}</span>
                ) : null}
              </span>
            ))}
          </div>
          <p className="text-xs text-text-secondary">
            Available here but edited elsewhere: renaming one changes it on every case in the
            firm, so it is not a decision to make from inside one matter.
          </p>
        </div>
      )}
    </div>
  )
}

/**
 * One tag, open for editing.
 *
 * Local draft state, committed on Save — so an abandoned edit leaves nothing
 * behind, and a half-typed label never reaches the tag a filter is using.
 */
function TagEditor({ tag, busy, onSave, onCancel }: {
  tag: TransactionTag
  busy: boolean
  onSave: (patch: Partial<TransactionTag>) => Promise<void>
  onCancel: () => void
}) {
  const [label, setLabel] = useState(tag.label)
  const [description, setDescription] = useState(tag.description ?? '')
  const [color, setColor] = useState(tag.color ?? 'gray')
  const [active, setActive] = useState(tag.is_active)

  return (
    <div className="border border-navy/30 bg-white rounded p-3 space-y-2">
      <div className="grid gap-2 md:grid-cols-[1fr_1.5fr_auto]">
        <input className="input" value={label} aria-label="Tag label"
          onChange={e => setLabel(e.target.value)} />
        <input className="input" value={description} aria-label="Tag description"
          placeholder="What this tag means, for whoever tags next"
          onChange={e => setDescription(e.target.value)} />
        <select className="input" value={color} aria-label="Tag colour"
          onChange={e => setColor(e.target.value)}>
          {TAG_COLORS.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <label className="text-xs text-text-secondary flex items-center gap-1.5">
          <input type="checkbox" checked={active} onChange={e => setActive(e.target.checked)} />
          Active
        </label>
        <span className="text-xs text-text-secondary">
          {/* Retiring is the safe alternative to deleting: the lines keep the tag
              and any exhibit built from it still resolves. */}
          Retiring keeps it on the {tag.usage_count ?? 0} line{tag.usage_count === 1 ? '' : 's'} that
          already carry it, but takes it out of the picker.
        </span>
        <span className="ml-auto flex gap-2">
          <button type="button" className="btn-secondary text-xs px-3 py-1"
            disabled={busy} onClick={onCancel}>Cancel</button>
          <button type="button" className="btn-primary text-xs px-3 py-1"
            disabled={busy || !label.trim()}
            onClick={() => void onSave({
              label, description: description.trim() || null, color, is_active: active,
            })}>
            {busy ? 'Saving…' : 'Save'}
          </button>
        </span>
      </div>
    </div>
  )
}
