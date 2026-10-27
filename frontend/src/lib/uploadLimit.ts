/**
 * Refusing a file that is too large to upload — before it is uploaded.
 *
 * The limit is enforced by nginx, which rejects the body MID-FLIGHT and answers
 * with its own HTML error page. Without this check a user waits out a 73 MB
 * upload and is then shown "Request failed: 413" — minutes spent to be told
 * nothing they can act on. `File.size` is known the instant a file is dropped.
 */
import { getConfig } from './api'

/** Megabytes, for a message somebody has to read. */
export function sizeMB(bytes: number): string {
  return (bytes / 1_048_576).toFixed(bytes < 10_485_760 ? 1 : 0)
}

/**
 * The server's limit, or null when it could not be learned.
 *
 * Null means NO pre-flight check rather than a guessed number. A hardcoded
 * fallback here would be a fourth copy of a value that already lives in
 * nginx.conf and settings, and the one most likely to be forgotten when it
 * changes — so a stale guess would start refusing files the server would have
 * accepted. Nginx still enforces, and a 413 carries the same message.
 */
export async function uploadLimitMB(): Promise<number | null> {
  try {
    return (await getConfig()).max_upload_mb ?? null
  } catch {
    return null
  }
}

/**
 * Why this file cannot be uploaded, or null if it can (or if the limit is
 * unknown, in which case the server will say).
 *
 * **The advice deliberately does not say "split into N MB chunks".** Splitting
 * a PDF by size cuts through documents: on a 665 MB production that is a dozen
 * seams, and a statement straddling one lands in two separate uploads where
 * nothing can ever bundle it back together. Splitting at a boundary the person
 * can see — one year, one account — costs them nothing extra and keeps every
 * document whole.
 */
export function tooLargeToUpload(file: File, maxMB: number | null): string | null {
  if (maxMB === null || file.size / 1_048_576 <= maxMB) return null
  return `${file.name} is ${sizeMB(file.size)} MB and the limit is ${maxMB} MB. `
    + `Split it into at least ${partsNeeded(file.size, maxMB)} files, cutting `
    + 'only where one document ends and the next begins: a year or an account '
    + 'per file if it is a run of statements, or at whatever boundaries you can '
    + 'see if it holds a mixture of things. Avoid splitting by size — that '
    + 'lands in the middle of a document, and the two halves then sit in '
    + 'separate uploads where nothing can put them back together.'
}

/**
 * How many files this has to become, at least.
 *
 * A tenth of headroom, because a split is never even: cutting at document
 * boundaries makes parts of unequal size, and "6 files" that turns out to need
 * 7 means doing it twice. The number is the point — "split it up" leaves
 * somebody guessing, and guessing low is the guess they make.
 */
function partsNeeded(bytes: number, maxMB: number): number {
  return Math.max(2, Math.ceil(bytes / 1_048_576 / (maxMB * 0.9)))
}

/**
 * The same advice for a 413 that got past the check — an unknown limit, or one
 * that changed under a page left open.
 */
export function uploadRejectedMessage(name: string, maxMB: number | null): string {
  return `${name} was rejected by the server as too large`
    + (maxMB === null ? '. ' : ` — the limit is ${maxMB} MB. `)
    + 'Split it at a boundary you can see, such as one year or one account per '
    + 'file, rather than by size.'
}

/**
 * The largest file worth POSTing through the API, in MB.
 *
 * NOT a guess: 50 MB was the nginx body limit this path lived under for its
 * whole life, so it is the largest size it is known to have carried. Above it,
 * a 74 MB upload timed out at the gateway while everything was still working —
 * the bytes move twice inside one request, browser to the API and API to
 * storage, and nothing can make that instant.
 *
 * Received documents uploads straight to storage over a signed URL and has no
 * such ceiling, so a large file goes there instead. This is a routing rule, not
 * a refusal: nothing is turned away, it is pointed at the door that works.
 */
export const MAX_DIRECT_POST_MB = 50

/**
 * Should this file go to Received documents rather than through the statement
 * import? Returns the reason, or null if the import can carry it.
 */
export function belongsInReceivedDocuments(file: File): string | null {
  if (file.size / 1_048_576 <= MAX_DIRECT_POST_MB) return null
  return `${file.name} is ${sizeMB(file.size)} MB. Upload it under Received `
    + 'documents instead — that page sends the file straight to storage, where '
    + 'a file this size will not time out on the way. A production that large '
    + 'is almost certainly several documents anyway, and Received documents is '
    + 'where it gets split.'
}

/** Did this error come from the body-size limit? */
export function isTooLargeError(e: unknown): boolean {
  return e instanceof Error && /\b413\b/.test(e.message)
}
