import { useCallback, useEffect, useState } from 'react'
import { Link, NavLink, useParams } from 'react-router-dom'
import GuideMarkdown from '../components/GuideMarkdown'
import {
  getGuideManifest, getGuideMarkdown, allChapters, neighbours,
} from '../lib/userGuide'
import type { GuideChapter, GuideManifest } from '../types'

/**
 * The user guide, rendered from the markdown in `docs/user-guide`.
 *
 * One component serves `/guide` (the index) and `/guide/:slug` (a chapter),
 * because they share the manifest, the chrome and the navigation — and the
 * index is itself a markdown file in the guide.
 *
 * **Public, like `/privacy` and `/terms`.** The files are static assets served
 * by nginx, so route-level gating would be theatre: anybody with the URL can
 * fetch the markdown directly whatever this component does.
 */

/** The nav lines the guide carries for GitHub readers: `[← Index](README.md) · …`. */
const NAV_LINE = /^\s*\[.*\]\([^)]*\.md\).*$/

/**
 * Drop the chrome the page renders for itself.
 *
 * Each chapter opens with its title, then a line of previous/index/next links,
 * then a rule — and closes with the same line. That exists so the folder reads
 * as a book on GitHub. Here the title is the page header and the links are
 * real navigation, so leaving the markdown copies in would print each of them
 * twice.
 *
 * Only the leading and trailing runs are touched, and only lines that are
 * entirely links: a nav line is recognisable and nothing else in the guide
 * looks like one.
 */
function stripChapterChrome(markdown: string): string {
  const lines = markdown.split('\n')

  let start = 0
  if (lines[start]?.startsWith('# ')) start += 1
  while (start < lines.length) {
    const line = lines[start].trim()
    if (line === '' || line === '---' || (NAV_LINE.test(line) && line.includes('·'))) {
      start += 1
      continue
    }
    break
  }

  let end = lines.length
  while (end > start) {
    const line = lines[end - 1].trim()
    if (line === '' || line === '---' || (NAV_LINE.test(line) && line.includes('·'))) {
      end -= 1
      continue
    }
    break
  }

  return lines.slice(start, end).join('\n')
}

/**
 * The index's prose, without the contents tables.
 *
 * `README.md` is two documents: a table of contents, which this page draws as
 * cards, and the four rules that everything in Part 3 rests on — which are
 * worth reading and have no other home. The split is the first heading that is
 * not a "Part".
 */
function indexProse(markdown: string): string {
  const lines = markdown.split('\n')
  const at = lines.findIndex(line => line.startsWith('## ') && !line.startsWith('## Part'))
  return at < 0 ? '' : lines.slice(at).join('\n')
}

function ChapterCard({ chapter }: { chapter: GuideChapter }) {
  return (
    <Link
      to={`/guide/${chapter.slug}`}
      className="group block rounded-lg border border-border bg-white p-5
                 hover:border-navy hover:shadow-md transition-all"
    >
      <div className="flex items-start gap-4">
        {chapter.number && (
          <span className="shrink-0 w-9 h-9 rounded-full bg-off-white text-navy
                           font-display text-lg flex items-center justify-center
                           group-hover:bg-navy group-hover:text-white transition-colors">
            {chapter.number}
          </span>
        )}
        <div className="min-w-0">
          <h3 className="font-sans font-semibold text-navy leading-snug">
            {chapter.title}
          </h3>
          {chapter.summary && (
            <p className="mt-1 text-sm text-text-secondary leading-6">{chapter.summary}</p>
          )}
        </div>
      </div>
    </Link>
  )
}

function Sidebar({ manifest, slug }: { manifest: GuideManifest; slug?: string }) {
  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `block rounded-md px-3 py-1.5 text-sm leading-snug transition-colors ${
      isActive
        ? 'bg-navy text-white font-medium'
        : 'text-text-secondary hover:bg-off-white hover:text-navy'
    }`

  return (
    <nav aria-label="Guide contents" className="space-y-5">
      <NavLink to="/guide" end className={linkClass}>Contents</NavLink>
      {manifest.parts.map(part => (
        <div key={part.title}>
          <div className="px-3 mb-1 text-[11px] uppercase tracking-widest
                          font-semibold text-text-secondary/70">
            {part.title}
          </div>
          <div className="space-y-0.5">
            {part.chapters.map(chapter => (
              <NavLink key={chapter.slug} to={`/guide/${chapter.slug}`} className={linkClass}>
                <span className={chapter.slug === slug ? 'text-white/70' : 'text-text-secondary/60'}>
                  {chapter.number && `${chapter.number}. `}
                </span>
                {chapter.title}
              </NavLink>
            ))}
          </div>
        </div>
      ))}
    </nav>
  )
}

export default function UserGuidePage() {
  const { slug } = useParams<{ slug?: string }>()
  const [manifest, setManifest] = useState<GuideManifest | null>(null)
  const [markdown, setMarkdown] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let live = true
    getGuideManifest()
      .then(m => { if (live) setManifest(m) })
      .catch(e => { if (live) setError(e instanceof Error ? e.message : 'Could not load the guide') })
    return () => { live = false }
  }, [])

  // The slug is resolved against the manifest rather than used as a path, so
  // a URL can only ever reach a file the guide actually published.
  const chapter = manifest && slug
    ? allChapters(manifest).find(c => c.slug === slug) ?? null
    : null

  const load = useCallback(async (file: string) => {
    setLoading(true)
    try {
      setMarkdown(await getGuideMarkdown(file))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load that chapter')
      setMarkdown(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!manifest) return
    if (slug && !chapter) {
      setError(`There is no chapter called "${slug}".`)
      setMarkdown(null)
      setLoading(false)
      return
    }
    void load(chapter ? chapter.file : manifest.index)
  }, [manifest, slug, chapter, load])

  // A fragment cannot be honoured until the markdown is on the page, and the
  // browser has already given up trying by then. Scrolling to the top of a new
  // chapter matters for the same reason: the router keeps the old offset.
  useEffect(() => {
    if (loading) return
    const target = window.location.hash ? window.location.hash.slice(1) : ''
    if (target) {
      const element = document.getElementById(decodeURIComponent(target))
      if (element) {
        element.scrollIntoView({ behavior: 'smooth', block: 'start' })
        return
      }
    }
    window.scrollTo({ top: 0 })
  }, [loading, slug])

  const { previous, next } = manifest && slug
    ? neighbours(manifest, slug)
    : { previous: null, next: null }

  const body = markdown === null ? '' : slug ? stripChapterChrome(markdown) : indexProse(markdown)

  return (
    <div className="min-h-screen bg-white font-sans">
      {/* Same header as /privacy and /terms, so the public pages feel like one site. */}
      <header className="border-b border-border sticky top-0 bg-white/95 backdrop-blur z-20">
        <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between gap-4">
          <div className="flex items-baseline gap-3 min-w-0">
            <Link to="/" className="font-display text-2xl text-navy tracking-tight">Cyclone</Link>
            <Link to="/guide"
              className="text-sm text-text-secondary hover:text-navy transition-colors truncate">
              User Guide
            </Link>
          </div>
          <Link to="/app" className="btn-secondary text-xs px-3 py-1.5">Open Cyclone</Link>
        </div>
      </header>

      <div className="max-w-6xl mx-auto px-6 py-10 lg:flex lg:gap-12">
        {/* Contents — a rail on a wide screen, a disclosure on a narrow one. */}
        {manifest && (
          <>
            <aside className="hidden lg:block w-60 shrink-0">
              <div className="sticky top-24 max-h-[calc(100vh-8rem)] overflow-y-auto pb-8 pr-2">
                <Sidebar manifest={manifest} slug={slug} />
              </div>
            </aside>
            <details className="lg:hidden mb-8 rounded-lg border border-border bg-off-white">
              <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-navy">
                Contents
              </summary>
              <div className="px-2 pb-3">
                <Sidebar manifest={manifest} slug={slug} />
              </div>
            </details>
          </>
        )}

        <main className="min-w-0 flex-1">
          {error && (
            <div className="rounded-lg border border-danger/30 bg-danger/5 px-5 py-4">
              <p className="text-sm text-danger font-medium">{error}</p>
              <Link to="/guide" className="text-sm text-navy underline mt-2 inline-block">
                Back to the contents
              </Link>
            </div>
          )}

          {loading && !error && (
            <div className="space-y-3 animate-pulse" aria-hidden="true">
              <div className="h-9 w-2/3 bg-off-white rounded" />
              <div className="h-4 w-full bg-off-white rounded" />
              <div className="h-4 w-5/6 bg-off-white rounded" />
              <div className="h-4 w-4/6 bg-off-white rounded" />
            </div>
          )}

          {!loading && !error && (
            <>
              {/* The page header the markdown's own H1 was stripped for. */}
              {chapter ? (
                <header className="pb-6 mb-2 border-b border-border">
                  {chapter.number && (
                    <div className="text-xs uppercase tracking-widest text-gold font-semibold mb-2">
                      Chapter {chapter.number}
                    </div>
                  )}
                  <h1 className="font-display text-4xl text-navy leading-tight">
                    {chapter.title}
                  </h1>
                  {chapter.summary && (
                    <p className="mt-3 text-text-secondary leading-7">{chapter.summary}</p>
                  )}
                </header>
              ) : (
                <header className="pb-8 mb-8 border-b border-border">
                  <div className="text-xs uppercase tracking-widest text-gold font-semibold mb-2">
                    Cyclone
                  </div>
                  <h1 className="font-display text-4xl sm:text-5xl text-navy leading-tight">
                    User Guide
                  </h1>
                  <p className="mt-4 text-lg text-text-secondary leading-8 max-w-2xl">
                    For the people who do the work: paralegals, legal assistants, and the
                    attorneys who look over their shoulder. Each chapter is a task — read
                    the one you need.
                  </p>
                </header>
              )}

              {/* The contents, as cards, on the index page only. */}
              {!slug && manifest && (
                <div className="space-y-10 mb-4">
                  {manifest.parts.map(part => (
                    <section key={part.title}>
                      <h2 className="font-display text-2xl text-navy mb-4">{part.title}</h2>
                      <div className="grid gap-3 sm:grid-cols-2">
                        {part.chapters.map(c => <ChapterCard key={c.slug} chapter={c} />)}
                      </div>
                    </section>
                  ))}
                </div>
              )}

              {body && <article><GuideMarkdown markdown={body} /></article>}

              {/* Read straight through, or jump. Both are ordinary ways to use it. */}
              {slug && (previous || next) && (
                <nav className="mt-12 pt-6 border-t border-border flex flex-col
                                sm:flex-row gap-3 justify-between">
                  {previous ? (
                    <Link to={`/guide/${previous.slug}`}
                      className="group flex-1 rounded-lg border border-border p-4
                                 hover:border-navy transition-colors">
                      <div className="text-xs uppercase tracking-widest text-text-secondary/70">
                        ← Previous
                      </div>
                      <div className="text-navy font-medium mt-1 group-hover:underline">
                        {previous.number && `${previous.number}. `}{previous.title}
                      </div>
                    </Link>
                  ) : <div className="flex-1" />}
                  {next ? (
                    <Link to={`/guide/${next.slug}`}
                      className="group flex-1 rounded-lg border border-border p-4
                                 hover:border-navy transition-colors sm:text-right">
                      <div className="text-xs uppercase tracking-widest text-text-secondary/70">
                        Next →
                      </div>
                      <div className="text-navy font-medium mt-1 group-hover:underline">
                        {next.number && `${next.number}. `}{next.title}
                      </div>
                    </Link>
                  ) : <div className="flex-1" />}
                </nav>
              )}
            </>
          )}
        </main>
      </div>

      <footer className="border-t border-border mt-12">
        <div className="max-w-6xl mx-auto px-6 py-8 flex flex-col sm:flex-row
                        items-center justify-between gap-3 text-xs text-text-secondary">
          <span>&copy; {new Date().getFullYear()} Cyclone</span>
          <div className="flex gap-6">
            <Link to="/privacy" className="hover:text-navy transition-colors">Privacy Policy</Link>
            <Link to="/terms" className="hover:text-navy transition-colors">Terms of Use</Link>
            <Link to="/" className="hover:text-navy transition-colors">Home</Link>
          </div>
        </div>
      </footer>
    </div>
  )
}
