import { useEffect, useState } from 'react'
import GuideMarkdown from '../../components/GuideMarkdown'

/**
 * The client's own guide, inside the portal.
 *
 * **One page, no table of contents**, which is why it does not reuse
 * `UserGuidePage`: that component navigates a manifest of sixteen chapters, and
 * a client has one page to read. Both render through `GuideMarkdown`, so the
 * two guides look like one product.
 *
 * Published from `docs/client-guide/` by `scripts/sync_user_guide.py`, which is
 * also what keeps it honest: `tests/test_user_guide_sync.py` fails if this copy
 * drifts from the source. Nothing client- or matter-specific may go in it — it
 * is a static asset, served to anyone with the URL, exactly like the staff
 * guide at `/guide`.
 */
export default function PortalGuidePage() {
  const [markdown, setMarkdown] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    fetch('/client-guide/README.md')
      .then(async response => {
        const text = await response.text()
        // The SPA fallback answers 200 with the app shell for a path it cannot
        // find, so the content is checked rather than the status.
        if (!response.ok || text.trimStart().startsWith('<!')) {
          throw new Error('The guide is not published on this server.')
        }
        if (live) setMarkdown(text)
      })
      .catch(e => { if (live) setError(e instanceof Error ? e.message : 'Could not load the guide') })
    return () => { live = false }
  }, [])

  return (
    <div className="max-w-3xl">
      {error && (
        <div className="rounded-lg border border-danger/30 bg-danger/5 px-5 py-4">
          <p className="text-sm text-danger font-medium">{error}</p>
          <p className="text-sm text-text-secondary mt-1">
            Ask the office for a copy — nothing is wrong with your case records.
          </p>
        </div>
      )}
      {!markdown && !error && (
        <div className="space-y-3 animate-pulse" aria-hidden="true">
          <div className="h-8 w-2/3 bg-off-white rounded" />
          <div className="h-4 w-full bg-off-white rounded" />
          <div className="h-4 w-5/6 bg-off-white rounded" />
        </div>
      )}
      {markdown && <article><GuideMarkdown markdown={markdown} /></article>}
    </div>
  )
}
