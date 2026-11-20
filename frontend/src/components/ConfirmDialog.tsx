import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'

/**
 * The app's own confirmation dialog, in place of `window.confirm`.
 *
 * **The browser's dialog reads as an error.** It is grey, carries the site's
 * address as its title, and looks the same whether it is asking "discard this
 * statement?" or reporting that something broke — so people learn to dismiss it
 * without reading, which is the opposite of what a confirmation is for. This
 * one is styled like the rest of the app, says what kind of question it is
 * asking (`tone`), and can lay out figures rather than cramming them into a
 * sentence.
 *
 * Used through `useConfirm`, which keeps a handler reading top to bottom:
 *
 *     const [dialog, confirm] = useConfirm()
 *     if (await confirm({...}) !== 'accept') return
 *     ...
 *     return <>{page}{dialog}</>
 *
 * **Cancel takes the focus**, not the action. A confirmation exists for the
 * case where somebody is clicking quickly down a list, and Enter on a focused
 * "Accept anyway" would let that habit straight through the one question asked
 * to interrupt it. Escape and a click on the backdrop also cancel.
 *
 * **More than one action is allowed**, because the honest answer to "accept a
 * statement that does not tie?" is often neither yes nor no but "read it
 * again". The result is the chosen action's key, or null for cancel.
 */

export type ConfirmTone = 'neutral' | 'warning' | 'danger'

export interface ConfirmAction<K extends string = string> {
  key: K
  label: string
  /** `primary` is the navy button; `danger` a red one for what cannot be undone. */
  kind?: 'primary' | 'secondary' | 'danger'
}

export interface ConfirmOptions<K extends string = string> {
  title: string
  body: ReactNode
  actions: ConfirmAction<K>[]
  tone?: ConfirmTone
  cancelLabel?: string
}

const TONE_BORDER: Record<ConfirmTone, string> = {
  neutral: 'border-t-navy',
  warning: 'border-t-amber-400',
  danger: 'border-t-red-600',
}

const TONE_ICON: Record<ConfirmTone, string> = {
  neutral: 'bg-navy/10 text-navy',
  warning: 'bg-amber-100 text-amber-800',
  danger: 'bg-red-100 text-red-700',
}

const ACTION_CLASS: Record<NonNullable<ConfirmAction['kind']>, string> = {
  primary: 'btn-primary',
  secondary: 'btn-secondary',
  danger:
    'inline-flex items-center justify-center px-5 py-2.5 rounded-md bg-red-700 text-white ' +
    'font-medium text-sm hover:bg-red-800 transition-colors focus:outline-none ' +
    'focus:ring-2 focus:ring-red-700/40',
}

function ToneIcon({ tone }: { tone: ConfirmTone }) {
  return (
    <span className={`shrink-0 w-9 h-9 rounded-full flex items-center justify-center ${TONE_ICON[tone]}`}
      aria-hidden="true">
      {tone === 'neutral' ? (
        <svg viewBox="0 0 16 16" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="1.6"
          strokeLinecap="round">
          <circle cx="8" cy="8" r="6.25" /><path d="M8 7.25v4" /><path d="M8 4.75v.01" />
        </svg>
      ) : (
        <svg viewBox="0 0 16 16" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="1.6"
          strokeLinecap="round" strokeLinejoin="round">
          <path d="M8 1.75 15 14H1z" /><path d="M8 6.25v3.5" /><path d="M8 11.75v.01" />
        </svg>
      )}
    </span>
  )
}

function ConfirmDialog<K extends string>({ options, onDone }: {
  options: ConfirmOptions<K>
  onDone: (choice: K | null) => void
}) {
  const tone = options.tone ?? 'neutral'
  const cancelRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    cancelRef.current?.focus()
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onDone(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onDone])

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-start justify-center overflow-y-auto p-4"
      role="alertdialog" aria-modal="true" aria-labelledby="confirm-title"
      onClick={e => { if (e.target === e.currentTarget) onDone(null) }}>
      <div className={`card w-full max-w-lg my-16 bg-white border-t-4 ${TONE_BORDER[tone]} p-5`}>
        <div className="flex gap-3">
          <ToneIcon tone={tone} />
          <div className="min-w-0 flex-1">
            <h2 id="confirm-title" className="font-semibold text-navy text-lg leading-snug">
              {options.title}
            </h2>
            <div className="mt-2 text-sm text-text-primary space-y-2">{options.body}</div>
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-3 mt-6">
          <button ref={cancelRef} type="button" className="btn-secondary" onClick={() => onDone(null)}>
            {options.cancelLabel ?? 'Cancel'}
          </button>
          {options.actions.map(a => (
            <button key={a.key} type="button" className={ACTION_CLASS[a.kind ?? 'primary']}
              onClick={() => onDone(a.key)}>
              {a.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

/**
 * A confirmation that can be awaited.
 *
 * :return: The element to render somewhere in the page, and a function that
 *   opens the dialog and resolves to the chosen action's key, or null when it
 *   was cancelled.
 */
export function useConfirm(): [
  ReactNode,
  <K extends string>(options: ConfirmOptions<K>) => Promise<K | null>,
] {
  const [options, setOptions] = useState<ConfirmOptions<string> | null>(null)
  // The pending promise's resolver lives in a ref, not in state: resolving it
  // inside a state updater is a side effect, and StrictMode runs updaters twice.
  const resolver = useRef<((choice: string | null) => void) | null>(null)

  const confirm = useCallback(<K extends string>(next: ConfirmOptions<K>) =>
    new Promise<K | null>(resolve => {
      // A second confirm while one is open cancels the first rather than
      // leaving its caller awaiting forever.
      resolver.current?.(null)
      resolver.current = choice => resolve(choice as K | null)
      setOptions(next)
    }), [])

  const onDone = useCallback((choice: string | null) => {
    const resolve = resolver.current
    resolver.current = null
    setOptions(null)
    resolve?.(choice)
  }, [])

  const dialog = options ? <ConfirmDialog options={options} onDone={onDone} /> : null
  return [dialog, confirm]
}
