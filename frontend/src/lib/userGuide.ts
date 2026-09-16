/**
 * Loading the published user guide.
 *
 * **Deliberately not in `api.ts`.** These are static files served by nginx
 * beside the bundle, not FastAPI endpoints: there is no bearer token to inject
 * and no `detail` to unwrap, and putting them behind `apiFetch` would imply an
 * API that does not exist. `scripts/sync_user_guide.py` publishes them from
 * `docs/user-guide/`, which stays the only place anybody writes.
 */
import type { GuideManifest, GuideChapter } from '../types'

/** Where the sync script publishes. Matches `DEST` in that script. */
const BASE = '/user-guide'

/**
 * A fetch failure here is usually one specific thing: the site was built
 * without running the sync script, so the directory is not there. The SPA
 * fallback then answers with `index.html` and a 200, which parses as neither
 * JSON nor markdown — so the content type is checked rather than the status.
 */
function missing(path: string): Error {
  return new Error(
    `The guide is not published at ${path}. Run: python scripts/sync_user_guide.py`,
  )
}

export async function getGuideManifest(): Promise<GuideManifest> {
  const path = `${BASE}/manifest.json`
  const response = await fetch(path)
  if (!response.ok) throw missing(path)
  const body = await response.text()
  try {
    return JSON.parse(body) as GuideManifest
  } catch {
    throw missing(path)
  }
}

/**
 * One chapter's markdown.
 *
 * @param file The manifest's `file`, or the index's own filename. Never taken
 *   from the URL directly — a slug is resolved against the manifest first, so
 *   a crafted path cannot reach for anything else on the server.
 */
export async function getGuideMarkdown(file: string): Promise<string> {
  const path = `${BASE}/${file}`
  const response = await fetch(path)
  if (!response.ok) throw missing(path)
  const text = await response.text()
  // The SPA fallback returns the shell with a 200 for anything it cannot find.
  if (text.trimStart().startsWith('<!')) throw missing(path)
  return text
}

/** Every chapter in reading order, flattened out of the parts. */
export function allChapters(manifest: GuideManifest): GuideChapter[] {
  return manifest.parts.flatMap(part => part.chapters)
}

/** The chapters either side of `slug`, for the footer links. */
export function neighbours(
  manifest: GuideManifest,
  slug: string,
): { previous: GuideChapter | null; next: GuideChapter | null } {
  const chapters = allChapters(manifest)
  const at = chapters.findIndex(chapter => chapter.slug === slug)
  if (at < 0) return { previous: null, next: null }
  return {
    previous: at > 0 ? chapters[at - 1] : null,
    next: at < chapters.length - 1 ? chapters[at + 1] : null,
  }
}

/**
 * The id a heading gets, and the target a `#fragment` link is looking for.
 *
 * GitHub's rules, because the guide's own cross-references were written
 * against them: lowercase, punctuation dropped, spaces to hyphens. Em dashes
 * and the daggers the guide uses as markers fall out with the punctuation.
 */
export function headingId(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\w\- ]+/g, '')
    .trim()
    .replace(/\s+/g, '-')
}

/**
 * Turn a link written for a folder of markdown files into a route.
 *
 * The guide is read on GitHub as much as in the browser, so its links are
 * relative filenames (`05-import-problems.md#flag-reference`). Rewriting them
 * here means neither copy has to know about the other.
 */
export function guideHref(href: string): string {
  if (/^[a-z]+:/i.test(href) || href.startsWith('//')) return href   // external
  if (href.startsWith('#')) return href                              // same page
  const [file, fragment] = href.split('#')
  const suffix = fragment ? `#${fragment}` : ''
  if (!file) return suffix
  const name = file.replace(/^\.\//, '')
  if (!name.endsWith('.md')) return href
  if (name === 'README.md') return `/guide${suffix}`
  return `/guide/${name.slice(0, -3)}${suffix}`
}
