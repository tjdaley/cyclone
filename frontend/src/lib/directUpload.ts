/**
 * Uploading a received file straight to storage, bypassing the API.
 *
 * Sent through the application, a 74 MB production moves twice inside one
 * request — browser to the API, API to Storage — and haproxy cut that with a
 * 504 while everything was still working. A signed URL takes the application
 * out of the path, which also removes the memory ceiling (every upload route
 * reads the whole body into RAM) and the nginx body-size limit with it.
 */
import { apiFetch } from './api'
import type { ReceivedUpload } from '../types'

interface UploadUrl {
  already_held: boolean
  source_document_id?: number | null
  path: string
  signed_url?: string | null
  token?: string | null
}

/**
 * The file's SHA-256, as hex.
 *
 * Computed here because it is the dedup key, and knowing it before uploading is
 * what lets the server answer "already held" for nothing. `crypto.subtle` needs
 * the whole file as an ArrayBuffer, which is fine at the sizes this accepts and
 * is the reason the limit is not simply removed.
 */
export async function sha256Hex(file: File): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

/**
 * What the remote file storage said, in words, and where it said it.
 *
 * **Naming the place the failure happened is the point.** The browser PUTs
 * straight to the storage service now, so a refusal there never touches
 * Cyclone's logs — somebody reading "the upload failed" would search the API
 * and the worker and find nothing at all. Saying "the remote file storage
 * refused it" is the difference between a support call that starts in the right
 * place and one that starts everywhere else.
 *
 * The transport status is reported ONLY when it disagrees with the one in the
 * body, because the storage service answers HTTP 400 while its body says 413 —
 * and a report of "400" sends somebody looking for a malformed request.
 */
async function storageErrorMessage(res: Response, filename: string): Promise<string> {
  let message = ''
  let code = ''
  let status: string | number = res.status
  try {
    const body = await res.json() as {
      message?: string; error?: string; code?: string; statusCode?: string
    }
    message = body.message || body.error || ''
    code = body.code || ''
    if (body.statusCode) status = body.statusCode
  } catch {
    // Not JSON. The transport status is all there is.
  }

  const where = `The remote file storage refused ${filename}`
  const said = message ? `: ${message}.` : '.'
  const detail = [code, String(status) !== String(res.status)
    ? `status ${status}, sent as ${res.status}` : `status ${status}`]
    .filter(Boolean).join(', ')

  // The one failure worth explaining rather than merely reporting. The storage
  // service enforces its OWN upload cap, set in its project settings and
  // nothing to do with Cyclone's — so a file inside Cyclone's limit can still
  // be refused here, and the person reading this cannot fix it by splitting
  // differently.
  const advice = code === 'EntityTooLarge' || String(status) === '413'
    ? ' That is the storage service\'s own size cap, which is separate from '
      + 'Cyclone\'s and is raised in the storage project settings — ask an '
      + 'administrator. Splitting the file smaller also works.'
    : ''

  return `${where}${said} (${detail}).${advice} Nothing was recorded here, so `
    + 'sending it again is safe.'
}

/**
 * Put a received file in storage and record it.
 *
 * @param onStage Called as the work moves: a 74 MB upload is a long silence
 *   otherwise, and a screen that looks untouched is what makes somebody drag
 *   the same files again.
 */
export async function uploadReceivedDirect(
  matterId: number,
  file: File,
  onStage?: (stage: 'hashing' | 'uploading' | 'recording') => void,
): Promise<ReceivedUpload> {
  onStage?.('hashing')
  const hash = await sha256Hex(file)

  const target = await apiFetch<UploadUrl>(
    `/api/v1/matters/${matterId}/received-documents/upload-url`,
    {
      method: 'POST',
      body: JSON.stringify({
        content_sha256: hash,
        filename: file.name,
        media_type: file.type || 'application/pdf',
      }),
    })

  // Already on the matter: nothing to send at all. On a large production this
  // is the difference between instant and minutes, and it only works because
  // the key is the content rather than the name — productions arrive re-served
  // under names that tell you nothing.
  if (!target.already_held) {
    if (!target.signed_url) {
      throw new Error('The server did not return a place to upload to.')
    }
    onStage?.('uploading')
    const put = await fetch(target.signed_url, {
      method: 'PUT',
      headers: { 'Content-Type': file.type || 'application/pdf' },
      body: file,
    })
    if (!put.ok) {
      throw new Error(await storageErrorMessage(put, file.name))
    }
  }

  onStage?.('recording')
  return apiFetch<ReceivedUpload>(
    `/api/v1/matters/${matterId}/received-documents/register`,
    {
      method: 'POST',
      body: JSON.stringify({
        path: target.path,
        content_sha256: hash,
        filename: file.name,
        byte_count: file.size,
        media_type: file.type || 'application/pdf',
      }),
    })
}
