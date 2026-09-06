import { supabase } from './supabaseClient'
import type {
  AppConfig,
  UserProfile,
  Client, ClientCreatePayload,
  Matter, MatterCreatePayload, RateOverride,
  MatterStaff, MatterStaffPayload, MatterStaffUpdatePayload, OpposingParty,
  Staff,
  BillingEntry, ParsedBillingPreview,
  DiscoveryDocument, DiscoveryRequestItem, DiscoveryUploadResponse,
  DiscoveryRequestItemUpdatePayload, StandardPrivilege, StandardObjection,
  MatterPleading, MatterPleadingUpdatePayload, MatterClaim, MatterChild, OpposingCounsel,
  MatterChildPayload, MatterChildUpdatePayload,
  MatterClaimPayload, MatterClaimUpdatePayload,
  OpposingCounselPayload, MatterCounselLink,
  MatterCounselLinkPayload, MatterCounselLinkUpdatePayload,
  PleadingIngestPreview, PleadingCommitRequest, PleadingCommitResponse,
  MatterIntakePreview, MatterIntakeCommitRequest, MatterIntakeCommitResponse,
  FinancialAccount, FinancialAccountUpdatePayload, AccountStatement,
  AccountTransaction, StatementIngestSummary, StatementJobStatus,
  TransactionCategory, TransactionCategoryPayload,
  TransactionTag, TransactionTagPayload,
  TransactionSearchFilter, TransactionSearchResult, BulkResult,
  AccountMergePreview, AccountMergeResult,
  TransactionCorrectionPayload, TransactionCorrectionResult,
  StatementReviewResult, StatementRejectResult, AccountDeletePreview,
  UndisclosedReport, ExportFormat, DownloadedFile,
  PayeeClassification, PayeeClassificationPayload, StatementRetryResult,
  FisRequest, FisStatement, FisSetting, FisSettingPayload, FisSchedule,
} from '../types'

// Re-export types so existing call sites that import payloads from '../lib/api'
// keep working without churning every file at once.
export type { UserProfile, ClientCreatePayload, MatterCreatePayload }

/**
 * Generic fetch wrapper that injects the Supabase Bearer token.
 */
/**
 * The sentence a failed response actually wants to say.
 *
 * FastAPI puts it in `detail`, so throwing the raw body put `{"detail":"..."}`
 * in front of the user everywhere an error is displayed — and there are dozens
 * of those. Backend handlers write `detail` as prose meant to be read (which
 * account, what to do next), and none of that survived the braces.
 *
 * Three shapes, because FastAPI emits three: a plain string for an
 * `HTTPException`, a list of `{msg, loc}` objects for a 422 validation failure,
 * and — when the server did not answer in JSON at all, an haproxy 504 being the
 * one that turns up here — no usable body, where the status is all there is.
 */
async function errorMessage(res: Response): Promise<string> {
  const fallback = `Request failed: ${res.status}`
  const body = await res.text()
  if (!body) return fallback
  try {
    const parsed = JSON.parse(body) as { detail?: unknown }
    const detail = parsed.detail
    if (typeof detail === 'string' && detail) return detail
    if (Array.isArray(detail)) {
      const parts = detail
        .map(d => (typeof d === 'object' && d && 'msg' in d ? String((d as { msg: unknown }).msg) : ''))
        .filter(Boolean)
      if (parts.length) return parts.join('; ')
    }
  } catch {
    // Not JSON — an HTML error page from nginx or haproxy. Showing its markup
    // would be worse than showing the status.
    return fallback
  }
  return fallback
}

export async function apiFetch<T>(
  endpoint: string,
  options: RequestInit = {},
): Promise<T> {
  const { data: { session } } = await supabase.auth.getSession()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> | undefined),
  }
  if (session?.access_token) {
    headers['Authorization'] = `Bearer ${session.access_token}`
  }

  const baseUrl = import.meta.env.VITE_API_BASE_URL ?? ''
  const res = await fetch(`${baseUrl}${endpoint}`, { ...options, headers })

  if (!res.ok) {
    throw new Error(await errorMessage(res))
  }
  // 204 No Content has no body to parse — several DELETE endpoints return it.
  if (res.status === 204 || res.headers.get('content-length') === '0') {
    return undefined as T
  }
  return res.json() as Promise<T>
}

/**
 * POST a body and receive a file.
 *
 * Separate from `apiFetch` because a download is not JSON: it needs the blob,
 * the filename out of Content-Disposition, and the bearer token that a plain
 * anchor href could never carry.
 */
export type { DownloadedFile }

export async function apiDownload(
  endpoint: string,
  body: unknown,
): Promise<DownloadedFile> {
  const { data: { session } } = await supabase.auth.getSession()
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (session?.access_token) {
    headers['Authorization'] = `Bearer ${session.access_token}`
  }

  const baseUrl = import.meta.env.VITE_API_BASE_URL ?? ''
  const res = await fetch(`${baseUrl}${endpoint}`, {
    method: 'POST', headers, body: JSON.stringify(body),
  })
  if (!res.ok) {
    // An error body is text, not a file — read it so the message survives.
    throw new Error(await errorMessage(res))
  }

  const disposition = res.headers.get('Content-Disposition') ?? ''
  const match = /filename="([^"]+)"/.exec(disposition)
  const raw = res.headers.get('X-Exhibit-Warnings')

  return {
    blob: await res.blob(),
    filename: match?.[1] ?? 'download',
    warnings: raw ? decodeURIComponent(raw).split(' | ').filter(Boolean) : [],
  }
}

/** Hand a downloaded file to the browser. */
export function saveFile(file: DownloadedFile): void {
  const url = URL.createObjectURL(file.blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = file.filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  // Revoking immediately can cancel the download in some browsers.
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

// ── Public config ─────────────────────────────────────────────────────

export async function getConfig(): Promise<AppConfig> {
  return apiFetch<AppConfig>('/api/config')
}

// ── Auth ──────────────────────────────────────────────────────────────

export async function getMe(): Promise<UserProfile> {
  return apiFetch<UserProfile>('/api/v1/auth/me')
}

export async function correlateStaff(): Promise<void> {
  await apiFetch<unknown>('/api/v1/auth/correlate-staff', { method: 'POST' })
}

// ── Staff ─────────────────────────────────────────────────────────────

export async function getStaff(): Promise<Staff[]> {
  return apiFetch<Staff[]>('/api/v1/staff')
}

export async function createStaff(payload: import('../types').StaffCreatePayload): Promise<Staff> {
  return apiFetch<Staff>('/api/v1/staff', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateStaff(staffId: number, payload: Record<string, unknown>): Promise<Staff> {
  return apiFetch<Staff>(`/api/v1/staff/${staffId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

// ── Staff auth roles (user_roles rows) ────────────────────────────────

import type { StaffRoleSet } from '../types'

export async function getStaffRoles(staffId: number): Promise<StaffRoleSet> {
  return apiFetch<StaffRoleSet>(`/api/v1/staff/${staffId}/roles`)
}

export async function setStaffRoles(staffId: number, roles: string[]): Promise<StaffRoleSet> {
  return apiFetch<StaffRoleSet>(`/api/v1/staff/${staffId}/roles`, {
    method: 'PUT',
    body: JSON.stringify({ roles }),
  })
}

// ── Knowledge base (CRM agent KB) ─────────────────────────────────────

import type { KbArticle, KbArticleCreatePayload, KbArticleUpdatePayload } from '../types'

export async function getKbArticles(): Promise<KbArticle[]> {
  return apiFetch<KbArticle[]>('/api/v1/kb-articles')
}

export async function createKbArticle(payload: KbArticleCreatePayload): Promise<KbArticle> {
  return apiFetch<KbArticle>('/api/v1/kb-articles', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateKbArticle(articleId: number, payload: KbArticleUpdatePayload): Promise<KbArticle> {
  return apiFetch<KbArticle>(`/api/v1/kb-articles/${articleId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function deleteKbArticle(articleId: number): Promise<void> {
  await apiFetch<unknown>(`/api/v1/kb-articles/${articleId}`, { method: 'DELETE' })
}

// ── Lead agent runs (HITL draft approval) ─────────────────────────────

import type { LeadAgentRun } from '../types'

export async function sendDraft(runId: number, body: string): Promise<LeadAgentRun> {
  return apiFetch<LeadAgentRun>(`/api/v1/lead-agent-runs/${runId}/send`, {
    method: 'POST',
    body: JSON.stringify({ body }),
  })
}

export async function rejectDraft(runId: number, reason?: string): Promise<LeadAgentRun> {
  return apiFetch<LeadAgentRun>(`/api/v1/lead-agent-runs/${runId}/reject`, {
    method: 'POST',
    body: JSON.stringify({ reason: reason ?? null }),
  })
}

export async function getEditedRuns(limit = 20): Promise<import('../types').EditedRunSummary[]> {
  return apiFetch<import('../types').EditedRunSummary[]>(`/api/v1/lead-agent-runs/edited?limit=${limit}`)
}

// ── Attorney lead responders (CRM escalation routing) ─────────────────

import type { AttorneyResponderSet } from '../types'

export async function getAttorneyResponders(attorneyStaffId: number): Promise<AttorneyResponderSet> {
  return apiFetch<AttorneyResponderSet>(`/api/v1/attorney-lead-responders/${attorneyStaffId}`)
}

export async function setAttorneyResponders(
  attorneyStaffId: number,
  responderStaffIds: number[],
): Promise<AttorneyResponderSet> {
  return apiFetch<AttorneyResponderSet>(`/api/v1/attorney-lead-responders/${attorneyStaffId}`, {
    method: 'PUT',
    body: JSON.stringify({ responder_staff_ids: responderStaffIds }),
  })
}

// ── Clients ───────────────────────────────────────────────────────────

export async function getClients(): Promise<Client[]> {
  return apiFetch<Client[]>('/api/v1/clients')
}

export async function createClient(payload: ClientCreatePayload): Promise<Client> {
  return apiFetch<Client>('/api/v1/clients', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateClient(clientId: number, payload: Record<string, unknown>): Promise<Client> {
  return apiFetch<Client>(`/api/v1/clients/${clientId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export interface ConflictCheckResult {
  has_conflict: boolean
  hit_count: number
  hits: Array<{ id: number; full_name: string; role: string; matter_caption: string }>
}

export async function conflictCheck(
  prospectiveName: string,
  opposingNames: string[],
): Promise<ConflictCheckResult> {
  return apiFetch<ConflictCheckResult>('/api/v1/clients/conflict-check', {
    method: 'POST',
    body: JSON.stringify({
      full_name: prospectiveName,
      opposing_names: opposingNames,
    }),
  })
}

// ── Matters ───────────────────────────────────────────────────────────

export async function getMatters(): Promise<Matter[]> {
  return apiFetch<Matter[]>('/api/v1/matters')
}

export async function getMatter(matterId: number): Promise<Matter> {
  return apiFetch<Matter>(`/api/v1/matters/${matterId}`)
}

export async function createMatter(payload: MatterCreatePayload): Promise<Matter> {
  return apiFetch<Matter>('/api/v1/matters', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateMatter(matterId: number, payload: Record<string, unknown>): Promise<Matter> {
  return apiFetch<Matter>(`/api/v1/matters/${matterId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

// ── Rate Overrides ────────────────────────────────────────────────────

export async function getRateOverrides(matterId: number): Promise<RateOverride[]> {
  return apiFetch<RateOverride[]>(`/api/v1/matters/${matterId}/rate-overrides`)
}

export async function setRateOverride(matterId: number, staffId: number, rate: number): Promise<RateOverride> {
  return apiFetch<RateOverride>(`/api/v1/matters/${matterId}/rate-overrides`, {
    method: 'POST',
    body: JSON.stringify({ staff_id: staffId, rate }),
  })
}

export async function deleteRateOverride(matterId: number, overrideId: number): Promise<void> {
  await apiFetch<unknown>(`/api/v1/matters/${matterId}/rate-overrides/${overrideId}`, {
    method: 'DELETE',
  })
}

// ── Matter intake from a pleading ─────────────────────────────────────

interface IntakeJob {
  id: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  result: MatterIntakePreview | null
  error: string | null
}

/** Hand a pleading to the worker. Returns immediately with a job to poll. */
async function uploadMatterIntake(file: File): Promise<IntakeJob> {
  const { data: { session } } = await supabase.auth.getSession()
  const form = new FormData()
  form.append('file', file)

  const baseUrl = import.meta.env.VITE_API_BASE_URL ?? ''
  const headers: Record<string, string> = {}
  if (session?.access_token) headers['Authorization'] = `Bearer ${session.access_token}`

  const res = await fetch(`${baseUrl}/api/v1/matters/intake/upload`, {
    method: 'POST', headers, body: form,
  })
  if (!res.ok) {
    throw new Error(await errorMessage(res))
  }
  return res.json()
}

/**
 * Read a pleading for intake: upload, then poll until the worker is done.
 *
 * Extraction is one LLM vision call per image-only page plus two more over the
 * text — minutes on a scanned document, which no proxy will hold a connection
 * for. The upload returns a job id instead, and this polls it.
 *
 * @param onStatus Called with each status change, for a progress message.
 * @param signal Abort to stop polling (the job keeps running server-side).
 */
export async function previewMatterIntake(
  file: File,
  onStatus?: (status: IntakeJob['status'], secondsWaited: number) => void,
  signal?: AbortSignal,
): Promise<MatterIntakePreview> {
  const job = await uploadMatterIntake(file)
  onStatus?.(job.status, 0)

  const startedAt = Date.now()
  const POLL_MS = 2000
  // A long statement is read in passes and can run past twenty minutes. The old
  // fifteen-minute ceiling reported failure over jobs that went on to succeed,
  // which is the worst thing for this particular message to get wrong.
  const GIVE_UP_MS = 45 * 60 * 1000   // A long scanned pleading is still minutes

  for (;;) {
    if (signal?.aborted) throw new Error('Cancelled')
    await new Promise(r => setTimeout(r, POLL_MS))

    const current = await apiFetch<IntakeJob>(`/api/v1/matters/intake/jobs/${job.id}`)
    const waited = Math.round((Date.now() - startedAt) / 1000)
    onStatus?.(current.status, waited)

    if (current.status === 'succeeded' && current.result) return current.result
    if (current.status === 'failed') throw new Error(current.error || 'Extraction failed')
    if (Date.now() - startedAt > GIVE_UP_MS) {
      throw new Error(
        'Still running after 45 minutes. The job may yet finish — check the job log before ' +
        'uploading this again, or it will be ingested twice.',
      )
    }
  }
}

export async function commitMatterIntake(
  payload: MatterIntakeCommitRequest,
): Promise<MatterIntakeCommitResponse> {
  return apiFetch<MatterIntakeCommitResponse>('/api/v1/matters/intake/commit', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

// ── Matter Staff ──────────────────────────────────────────────────────

export async function getMatterStaff(matterId: number): Promise<MatterStaff[]> {
  return apiFetch<MatterStaff[]>(`/api/v1/matters/${matterId}/staff`)
}

export async function addMatterStaff(matterId: number, payload: MatterStaffPayload): Promise<MatterStaff> {
  return apiFetch<MatterStaff>(`/api/v1/matters/${matterId}/staff`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateMatterStaff(
  matterId: number,
  rowId: number,
  payload: MatterStaffUpdatePayload,
): Promise<MatterStaff> {
  return apiFetch<MatterStaff>(`/api/v1/matters/${matterId}/staff/${rowId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function deleteMatterStaff(matterId: number, rowId: number): Promise<void> {
  await apiFetch<unknown>(`/api/v1/matters/${matterId}/staff/${rowId}`, { method: 'DELETE' })
}

// ── Opposing Parties ──────────────────────────────────────────────────

export async function getOpposingParties(matterId: number): Promise<OpposingParty[]> {
  return apiFetch<OpposingParty[]>(`/api/v1/matters/${matterId}/opposing-parties`)
}

export async function createOpposingParty(
  matterId: number,
  fullName: string,
  relationship?: string | null,
): Promise<OpposingParty> {
  return apiFetch<OpposingParty>(`/api/v1/matters/${matterId}/opposing-parties`, {
    method: 'POST',
    body: JSON.stringify({ full_name: fullName, relationship: relationship ?? null }),
  })
}

export async function deleteOpposingParty(matterId: number, partyId: number): Promise<void> {
  await apiFetch<unknown>(`/api/v1/matters/${matterId}/opposing-parties/${partyId}`, { method: 'DELETE' })
}

// ── Billing ───────────────────────────────────────────────────────────

export async function getBillingEntries(matterId: number): Promise<BillingEntry[]> {
  return apiFetch<BillingEntry[]>(`/api/v1/billing/entries?matter_id=${matterId}`)
}

export async function parseNLBillingEntry(text: string, matterId?: number): Promise<ParsedBillingPreview> {
  return apiFetch<ParsedBillingPreview>('/api/v1/billing/parse', {
    method: 'POST',
    body: JSON.stringify({ text, matter_id: matterId }),
  })
}

// ── Discovery ─────────────────────────────────────────────────────────

export async function getDiscoveryDocuments(matterId: number): Promise<DiscoveryDocument[]> {
  return apiFetch<DiscoveryDocument[]>(`/api/v1/discovery/${matterId}/documents`)
}

export async function getDiscoveryItems(documentId: number): Promise<DiscoveryRequestItem[]> {
  return apiFetch<DiscoveryRequestItem[]>(`/api/v1/discovery/documents/${documentId}/items`)
}

export async function uploadDiscoveryPDF(
  matterId: number,
  file: File,
  propoundedDate?: string,
): Promise<DiscoveryUploadResponse> {
  // Cannot use apiFetch here — multipart/form-data needs the browser to
  // set the Content-Type boundary. This is the one justified exception.
  const { data: { session } } = await supabase.auth.getSession()
  const form = new FormData()
  form.append('file', file)
  form.append('matter_id', String(matterId))
  if (propoundedDate) form.append('propounded_date', propoundedDate)

  const baseUrl = import.meta.env.VITE_API_BASE_URL ?? ''
  const headers: Record<string, string> = {}
  if (session?.access_token) {
    headers['Authorization'] = `Bearer ${session.access_token}`
  }

  const res = await fetch(`${baseUrl}/api/v1/discovery/upload`, {
    method: 'POST',
    headers,
    body: form,
  })
  if (!res.ok) {
    throw new Error(await errorMessage(res))
  }
  return res.json()
}

export async function downloadDiscoveryDocx(documentId: number): Promise<void> {
  const { data: { session } } = await supabase.auth.getSession()
  const baseUrl = import.meta.env.VITE_API_BASE_URL ?? ''
  const headers: Record<string, string> = {}
  if (session?.access_token) headers['Authorization'] = `Bearer ${session.access_token}`

  const res = await fetch(`${baseUrl}/api/v1/discovery/documents/${documentId}/download`, { headers })
  if (!res.ok) {
    throw new Error(await errorMessage(res))
  }

  const blob = await res.blob()
  const disposition = res.headers.get('Content-Disposition') ?? ''
  const match = disposition.match(/filename="?([^"]+)"?/)
  const filename = match?.[1] ?? 'discovery_responses.docx'

  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

export async function updateDiscoveryItem(
  itemId: number,
  payload: DiscoveryRequestItemUpdatePayload,
): Promise<DiscoveryRequestItem> {
  return apiFetch<DiscoveryRequestItem>(`/api/v1/discovery/items/${itemId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function getStandardPrivileges(): Promise<StandardPrivilege[]> {
  return apiFetch<StandardPrivilege[]>('/api/v1/discovery/standard-privileges')
}

export async function getStandardObjections(requestType: string): Promise<StandardObjection[]> {
  return apiFetch<StandardObjection[]>(`/api/v1/discovery/standard-objections?request_type=${encodeURIComponent(requestType)}`)
}

// ── Pleadings ─────────────────────────────────────────────────────────

export async function previewPleading(matterId: number, file: File): Promise<PleadingIngestPreview> {
  // Multipart upload, bypasses apiFetch
  const { data: { session } } = await supabase.auth.getSession()
  const form = new FormData()
  form.append('file', file)
  form.append('matter_id', String(matterId))

  const baseUrl = import.meta.env.VITE_API_BASE_URL ?? ''
  const headers: Record<string, string> = {}
  if (session?.access_token) headers['Authorization'] = `Bearer ${session.access_token}`

  const res = await fetch(`${baseUrl}/api/v1/pleadings/preview`, { method: 'POST', headers, body: form })
  if (!res.ok) {
    throw new Error(await errorMessage(res))
  }
  return res.json()
}

export async function commitPleading(payload: PleadingCommitRequest): Promise<PleadingCommitResponse> {
  return apiFetch<PleadingCommitResponse>('/api/v1/pleadings/commit', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function getMatterPleadings(matterId: number): Promise<MatterPleading[]> {
  return apiFetch<MatterPleading[]>(`/api/v1/matters/${matterId}/pleadings`)
}

export async function updatePleading(
  pleadingId: number,
  payload: MatterPleadingUpdatePayload,
): Promise<MatterPleading> {
  return apiFetch<MatterPleading>(`/api/v1/pleadings/${pleadingId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

/** Store the original PDF against a committed pleading. Multipart, so it bypasses apiFetch. */
export async function uploadPleadingPdf(pleadingId: number, file: File): Promise<MatterPleading> {
  const { data: { session } } = await supabase.auth.getSession()
  const form = new FormData()
  form.append('file', file)

  const baseUrl = import.meta.env.VITE_API_BASE_URL ?? ''
  const headers: Record<string, string> = {}
  if (session?.access_token) headers['Authorization'] = `Bearer ${session.access_token}`

  const res = await fetch(`${baseUrl}/api/v1/pleadings/${pleadingId}/pdf`, {
    method: 'POST', headers, body: form,
  })
  if (!res.ok) {
    throw new Error(await errorMessage(res))
  }
  return res.json()
}

/**
 * Open the stored PDF in a new tab.
 *
 * The signed URL carries its own authorization, so the browser can navigate to
 * it directly — a plain link to our API would arrive without the bearer token.
 * The window is opened synchronously and pointed at the URL once it arrives, so
 * the popup blocker sees a real click rather than an async open.
 */
export async function openPleadingPdf(pleadingId: number): Promise<void> {
  const tab = window.open('', '_blank')
  try {
    const { url } = await apiFetch<{ url: string; expires_in: number }>(
      `/api/v1/pleadings/${pleadingId}/pdf-url`,
    )
    if (tab) tab.location.href = url
    else window.location.href = url
  } catch (e) {
    tab?.close()
    throw e
  }
}

export async function getMatterClaims(matterId: number): Promise<MatterClaim[]> {
  return apiFetch<MatterClaim[]>(`/api/v1/matters/${matterId}/claims`)
}

export async function getMatterChildren(matterId: number): Promise<MatterChild[]> {
  return apiFetch<MatterChild[]>(`/api/v1/matters/${matterId}/children`)
}

export async function getMatterOpposingCounsel(matterId: number): Promise<OpposingCounsel[]> {
  return apiFetch<OpposingCounsel[]>(`/api/v1/matters/${matterId}/opposing-counsel`)
}

export async function updateOpposingCounsel(ocId: number, payload: Partial<OpposingCounsel>): Promise<OpposingCounsel> {
  return apiFetch<OpposingCounsel>(`/api/v1/opposing-counsel/${ocId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

/**
 * Create a counsel record, or get back the existing one.
 *
 * The backend dedups on (bar_state, bar_number) and returns the known row
 * unmodified rather than creating a second one.
 */
export async function createOpposingCounsel(payload: OpposingCounselPayload): Promise<OpposingCounsel> {
  return apiFetch<OpposingCounsel>('/api/v1/opposing-counsel', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

// ── Matter ↔ Counsel links ────────────────────────────────────────────

export async function getMatterCounselLinks(matterId: number): Promise<MatterCounselLink[]> {
  return apiFetch<MatterCounselLink[]>(`/api/v1/matters/${matterId}/opposing-counsel/links`)
}

export async function linkOpposingCounsel(
  matterId: number,
  payload: MatterCounselLinkPayload,
): Promise<MatterCounselLink> {
  return apiFetch<MatterCounselLink>(`/api/v1/matters/${matterId}/opposing-counsel`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateMatterCounselLink(
  matterId: number,
  linkId: number,
  payload: MatterCounselLinkUpdatePayload,
): Promise<MatterCounselLink> {
  return apiFetch<MatterCounselLink>(`/api/v1/matters/${matterId}/opposing-counsel/${linkId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function unlinkOpposingCounsel(matterId: number, linkId: number): Promise<void> {
  await apiFetch<unknown>(`/api/v1/matters/${matterId}/opposing-counsel/${linkId}`, { method: 'DELETE' })
}

// ── Matter Children (CRUD) ────────────────────────────────────────────

export async function createMatterChild(matterId: number, payload: MatterChildPayload): Promise<MatterChild> {
  return apiFetch<MatterChild>(`/api/v1/matters/${matterId}/children`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateMatterChild(childId: number, payload: MatterChildUpdatePayload): Promise<MatterChild> {
  return apiFetch<MatterChild>(`/api/v1/children/${childId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function deleteMatterChild(childId: number): Promise<void> {
  await apiFetch<unknown>(`/api/v1/children/${childId}`, { method: 'DELETE' })
}

// ── Matter Claims (CRUD) ──────────────────────────────────────────────

export async function createMatterClaim(matterId: number, payload: MatterClaimPayload): Promise<MatterClaim> {
  return apiFetch<MatterClaim>(`/api/v1/matters/${matterId}/claims`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateMatterClaim(claimId: number, payload: MatterClaimUpdatePayload): Promise<MatterClaim> {
  return apiFetch<MatterClaim>(`/api/v1/claims/${claimId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function deleteMatterClaim(claimId: number): Promise<void> {
  await apiFetch<unknown>(`/api/v1/claims/${claimId}`, { method: 'DELETE' })
}


// ── Financial: statements, accounts, transactions ─────────────────────

/**
 * Upload a statement PDF and return the job reading it, without waiting.
 *
 * Split from the waiting so a stack of files can be handed over in one go. The
 * upload takes about a second; the read takes minutes, and the worker runs
 * several at once. Uploading one and waiting for it before sending the next
 * left that pool with a single job to run no matter how many files were
 * dropped, which is the whole reason a thirteen-month upload had to be one
 * enormous PDF to finish at all.
 */
export async function queueStatement(
  matterId: number,
  file: File,
  batesPrefix?: string,
): Promise<{ id: string; warnings: string[] }> {
  const { data: { session } } = await supabase.auth.getSession()
  const form = new FormData()
  form.append('file', file)
  if (batesPrefix?.trim()) form.append('bates_prefix', batesPrefix.trim())

  const baseUrl = import.meta.env.VITE_API_BASE_URL ?? ''
  const headers: Record<string, string> = {}
  if (session?.access_token) headers['Authorization'] = `Bearer ${session.access_token}`

  const res = await fetch(`${baseUrl}/api/v1/matters/${matterId}/statements/upload`, {
    method: 'POST', headers, body: form,
  })
  if (!res.ok) {
    throw new Error(await errorMessage(res))
  }
  const job: { id: string; warnings?: string[] } = await res.json()
  return { id: job.id, warnings: job.warnings ?? [] }
}

/**
 * Upload a statement PDF and poll until the worker is done.
 *
 * One PDF may hold several statements; extraction is one LLM call per
 * statement, so this goes through the job queue like matter intake.
 */
export async function ingestStatement(
  matterId: number,
  file: File,
  onStatus?: (status: StatementJobStatus['status'], secondsWaited: number) => void,
  batesPrefix?: string,
): Promise<StatementIngestSummary> {
  const { data: { session } } = await supabase.auth.getSession()
  const form = new FormData()
  form.append('file', file)
  // Optional and rarely needed: the stamp is found by pattern. This only helps
  // when a document carries two competing series or an unusual one.
  if (batesPrefix?.trim()) form.append('bates_prefix', batesPrefix.trim())

  const baseUrl = import.meta.env.VITE_API_BASE_URL ?? ''
  const headers: Record<string, string> = {}
  if (session?.access_token) headers['Authorization'] = `Bearer ${session.access_token}`

  const res = await fetch(`${baseUrl}/api/v1/matters/${matterId}/statements/upload`, {
    method: 'POST', headers, body: form,
  })
  if (!res.ok) {
    throw new Error(await errorMessage(res))
  }
  const job: { id: string } = await res.json()
  return awaitStatementJob(job.id, onStatus)
}

/**
 * Poll a statement ingest to a terminal state.
 *
 * Shared by the upload and by a retry, which queues the same kind of job with
 * a document already in storage. The give-up ceiling belongs in one place: it
 * was raised from fifteen minutes to forty-five because a long statement is
 * read in passes, and reporting failure over a job that went on to succeed is
 * the worst thing this particular message can get wrong.
 */
export async function awaitStatementJob(
  jobId: string,
  onStatus?: (status: StatementJobStatus['status'], seconds: number) => void,
): Promise<StatementIngestSummary> {
  onStatus?.('queued', 0)
  const startedAt = Date.now()
  const GIVE_UP_MS = 45 * 60 * 1000
  for (;;) {
    await new Promise(r => setTimeout(r, 2000))
    const current = await apiFetch<StatementJobStatus>(`/api/v1/statements/jobs/${jobId}`)
    onStatus?.(current.status, Math.round((Date.now() - startedAt) / 1000))
    if (current.status === 'succeeded' && current.result) return current.result
    if (current.status === 'failed') throw new Error(current.error || 'Extraction failed')
    if (Date.now() - startedAt > GIVE_UP_MS) {
      throw new Error(
        'Still running after 45 minutes. The job may yet finish — check the job log before ' +
        'uploading this again, or it will be ingested twice.',
      )
    }
  }
}

export async function getFinancialAccounts(matterId: number): Promise<FinancialAccount[]> {
  return apiFetch<FinancialAccount[]>(`/api/v1/matters/${matterId}/financial-accounts`)
}

/**
 * Accounts referenced by this matter's transactions that the matter does not hold.
 *
 * Derived on demand rather than stored: adding a missing account to the matter
 * takes it off this list, which is the whole workflow.
 */
/**
 * Export the current query.
 *
 * `csv` is the clean extraction; `md`, `docx`, and `pdf` are full exhibits
 * carrying the case caption and the verification notice. The export covers
 * every matching line, not the page on screen.
 */
export async function exportTransactions(
  matterId: number,
  filter: TransactionSearchFilter,
  format: ExportFormat,
  exhibitName: string,
): Promise<DownloadedFile> {
  return apiDownload(
    `/api/v1/matters/${matterId}/transactions/export`,
    { ...filter, format, exhibit_name: exhibitName },
  )
}

/**
 * Export the referenced-but-not-produced list — the motion-to-compel exhibit.
 *
 * Takes no filter, unlike the transaction export: the list is the whole of what
 * was found, so there is no query to reproduce.
 */
export async function exportUndisclosedAccounts(
  matterId: number,
  format: ExportFormat,
  exhibitName: string,
): Promise<DownloadedFile> {
  return apiDownload(
    `/api/v1/matters/${matterId}/undisclosed-accounts/export`,
    { format, exhibit_name: exhibitName },
  )
}

/**
 * Open the PDF a statement was extracted from, in a new tab.
 *
 * Same shape as `openPleadingPdf`, including the synchronous `window.open` —
 * the popup blocker judges a tab by whether a real click opened it, and a tab
 * opened after an await is not one.
 *
 * The `#page=` fragment is a hint most PDF viewers honour and none break on.
 * It matters because one upload routinely holds a whole production, so without
 * it the reader lands on page 1 of sixty and hunts for the month they clicked.
 * The page is where the statement's first TRANSACTION is printed, so it can be
 * a page or two into the statement rather than at its head.
 */
export async function openStatementPdf(statementId: number): Promise<void> {
  const tab = window.open('', '_blank')
  try {
    const { url, page } = await apiFetch<{
      url: string; expires_in: number; page: number | null; source_filename: string | null
    }>(`/api/v1/statements/${statementId}/pdf-url`)
    const target = page ? `${url}#page=${page}` : url
    if (tab) tab.location.href = target
    else window.location.href = target
  } catch (e) {
    // Leaving a blank tab open behind an error message is its own small
    // mystery — close it before the caller shows what went wrong.
    tab?.close()
    throw e
  }
}

/**
 * Discard what an upload produced and read the PDF again.
 *
 * Acts on the document, not the statement — the result says how many statements
 * went, which can be more than one. A 409 means the source PDF could not be
 * retrieved and nothing was deleted.
 */
export async function retryStatement(statementId: number): Promise<StatementRetryResult> {
  return apiFetch<StatementRetryResult>(`/api/v1/statements/${statementId}/retry`, {
    method: 'POST',
  })
}

export async function getUndisclosedAccounts(matterId: number): Promise<UndisclosedReport> {
  return apiFetch<UndisclosedReport>(`/api/v1/matters/${matterId}/undisclosed-accounts`)
}

/**
 * Record what a payee is, so the creditor scan stops asking.
 *
 * Omit `matter_id` for the firm's answer, applied to every matter. That is the
 * right scope for a utility or a national card issuer; a Zelle payee who might
 * be a private lender belongs to one household and takes the matter id.
 */
export async function createPayeeClassification(
  payload: PayeeClassificationPayload,
): Promise<PayeeClassification> {
  return apiFetch<PayeeClassification>('/api/v1/payee-classifications', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updatePayeeClassification(
  id: number,
  payload: PayeeClassificationPayload,
): Promise<PayeeClassification> {
  return apiFetch<PayeeClassification>(`/api/v1/payee-classifications/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function deletePayeeClassification(id: number): Promise<void> {
  await apiFetch<void>(`/api/v1/payee-classifications/${id}`, { method: 'DELETE' })
}

export async function updateFinancialAccount(
  accountId: number,
  payload: FinancialAccountUpdatePayload,
): Promise<FinancialAccount> {
  return apiFetch<FinancialAccount>(`/api/v1/financial-accounts/${accountId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

/** What merging one account into another would do, and what stands in the way. */
export async function previewAccountMerge(
  sourceAccountId: number,
  targetAccountId: number,
): Promise<AccountMergePreview> {
  return apiFetch<AccountMergePreview>(
    `/api/v1/financial-accounts/${sourceAccountId}/merge-preview/${targetAccountId}`,
  )
}

/**
 * Move every statement off one account onto another, then delete it.
 *
 * `force` clears non-blocking conflicts only — a blocking one is refused
 * regardless, with a 409 explaining why.
 */
export async function mergeAccounts(
  sourceAccountId: number,
  targetAccountId: number,
  force = false,
): Promise<AccountMergeResult> {
  return apiFetch<AccountMergeResult>(`/api/v1/financial-accounts/${sourceAccountId}/merge`, {
    method: 'POST',
    body: JSON.stringify({ target_account_id: targetAccountId, force }),
  })
}

export async function getAccountStatements(accountId: number): Promise<AccountStatement[]> {
  return apiFetch<AccountStatement[]>(`/api/v1/financial-accounts/${accountId}/statements`)
}

/** Statements that did not clear on their own — the exceptions queue. */
export async function getStatementExceptions(matterId: number): Promise<AccountStatement[]> {
  return apiFetch<AccountStatement[]>(`/api/v1/matters/${matterId}/statements/exceptions`)
}

/**
 * Clear a statement out of the exceptions queue.
 *
 * `accepted` keeps it, unreconciled or not. `rejected` **deletes** it, its
 * transactions, and — when nothing of value would go with it — the account the
 * bad import created, so the result carries no statement.
 */
export async function reviewStatement(
  statementId: number,
  reviewStatus: 'accepted' | 'rejected',
): Promise<StatementReviewResult> {
  return apiFetch<StatementReviewResult>(`/api/v1/statements/${statementId}/review`, {
    method: 'PATCH',
    body: JSON.stringify({ review_status: reviewStatus }),
  })
}

/**
 * Correct a value on an ingested line.
 *
 * Send only the fields that actually changed — the server writes one audit
 * entry per changed field, so posting a whole form invents a history of edits
 * that never happened.
 */
export async function correctTransaction(
  transactionId: number,
  payload: TransactionCorrectionPayload,
): Promise<TransactionCorrectionResult> {
  return apiFetch<TransactionCorrectionResult>(`/api/v1/transactions/${transactionId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

/**
 * Drop a line from its statement.
 *
 * Hidden and excluded from totals, not destroyed. The statement comes back
 * re-reconciled — if extraction invented the line, the balance ties better
 * without it.
 */
export async function deleteTransaction(
  transactionId: number,
  reason?: string,
): Promise<TransactionCorrectionResult> {
  return apiFetch<TransactionCorrectionResult>(`/api/v1/transactions/${transactionId}/delete`, {
    method: 'POST',
    body: JSON.stringify({ reason: reason?.trim() || null }),
  })
}

/** Put a dropped line back, and re-reconcile the statement with it. */
export async function restoreTransaction(
  transactionId: number,
): Promise<TransactionCorrectionResult> {
  return apiFetch<TransactionCorrectionResult>(`/api/v1/transactions/${transactionId}/restore`, {
    method: 'POST',
  })
}

/**
 * Delete a statement, its transactions, and an account left empty by it.
 *
 * The same discard as rejecting from the exceptions queue, reached from the
 * statement itself — a statement can look fine on ingest and only later turn
 * out to be a mess. The source PDF stays in storage.
 */
export async function deleteStatement(statementId: number): Promise<StatementRejectResult> {
  return apiFetch<StatementRejectResult>(`/api/v1/statements/${statementId}`, { method: 'DELETE' })
}

/** What deleting this account would take with it, and any reason to pause. */
export async function previewAccountDelete(accountId: number): Promise<AccountDeletePreview> {
  return apiFetch<AccountDeletePreview>(`/api/v1/financial-accounts/${accountId}/delete-preview`)
}

export async function deleteAccount(accountId: number): Promise<AccountDeletePreview> {
  return apiFetch<AccountDeletePreview>(`/api/v1/financial-accounts/${accountId}`, {
    method: 'DELETE',
  })
}

export async function getStatementTransactions(statementId: number): Promise<AccountTransaction[]> {
  return apiFetch<AccountTransaction[]>(`/api/v1/statements/${statementId}/transactions`)
}

/** An account's whole history in date order — the waste/reimbursement query. */
export async function getAccountTransactions(accountId: number): Promise<AccountTransaction[]> {
  return apiFetch<AccountTransaction[]>(`/api/v1/financial-accounts/${accountId}/transactions`)
}

// ── Transaction categories (firm-wide chart of accounts) ──────────────

export async function getTransactionCategories(includeInactive = false): Promise<TransactionCategory[]> {
  const q = includeInactive ? '?include_inactive=true' : ''
  return apiFetch<TransactionCategory[]>(`/api/v1/transaction-categories${q}`)
}

export async function createTransactionCategory(
  payload: TransactionCategoryPayload,
): Promise<TransactionCategory> {
  return apiFetch<TransactionCategory>('/api/v1/transaction-categories', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateTransactionCategory(
  categoryId: number,
  payload: TransactionCategoryPayload,
): Promise<TransactionCategory> {
  return apiFetch<TransactionCategory>(`/api/v1/transaction-categories/${categoryId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function deleteTransactionCategory(categoryId: number): Promise<void> {
  await apiFetch<unknown>(`/api/v1/transaction-categories/${categoryId}`, { method: 'DELETE' })
}

// ── Transaction tags ──────────────────────────────────────────────────

/** Firm-wide tags plus this matter's own, with usage counts. */
export async function getTransactionTags(
  matterId: number,
  includeInactive = false,
): Promise<TransactionTag[]> {
  const q = includeInactive ? '?include_inactive=true' : ''
  return apiFetch<TransactionTag[]>(`/api/v1/matters/${matterId}/transaction-tags${q}`)
}

export async function createMatterTag(
  matterId: number,
  payload: TransactionTagPayload,
): Promise<TransactionTag> {
  return apiFetch<TransactionTag>(`/api/v1/matters/${matterId}/transaction-tags`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function createFirmTag(payload: TransactionTagPayload): Promise<TransactionTag> {
  return apiFetch<TransactionTag>('/api/v1/transaction-tags', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateTransactionTag(
  tagId: number,
  payload: TransactionTagPayload,
): Promise<TransactionTag> {
  return apiFetch<TransactionTag>(`/api/v1/transaction-tags/${tagId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

/**
 * Delete a tag. Refused with a 409 while it is in use unless `force` is set,
 * because the delete cascades — it would dissolve an exhibit without a trace.
 */
export async function deleteTransactionTag(tagId: number, force = false): Promise<void> {
  const q = force ? '?force=true' : ''
  await apiFetch<unknown>(`/api/v1/transaction-tags/${tagId}${q}`, { method: 'DELETE' })
}

// ── Transaction search and bulk classification ────────────────────────

/**
 * Filter a matter's transactions.
 *
 * POST rather than GET: the filter carries three id arrays and a free-text
 * term, which as a query string is unreadable and can run past a URL limit.
 */
// ── Financial Information Statement ───────────────────────────────────

/**
 * Average a person's income and expenses over a window of whole months.
 *
 * Computed on demand, never stored: the statement is a view of the transactions
 * as they stand, so re-filing one line changes it. That is what makes the FIS
 * screen a working surface rather than a report regenerated somewhere else.
 */
export async function buildFis(matterId: number, request: FisRequest): Promise<FisStatement> {
  return apiFetch<FisStatement>(`/api/v1/matters/${matterId}/fis`, {
    method: 'POST',
    body: JSON.stringify(request),
  })
}

/**
 * Export the statement.
 *
 * Sends the whole selection, so the document is provably the one on screen.
 */
export async function exportFis(
  matterId: number,
  request: FisRequest,
  format: ExportFormat,
  exhibitName: string,
  compressed: boolean,
): Promise<DownloadedFile> {
  return apiDownload(
    `/api/v1/matters/${matterId}/fis/export`,
    { ...request, format, exhibit_name: exhibitName, compressed },
  )
}

/**
 * Every transaction behind the statement, grouped by category.
 *
 * The monthly figures come from the statement itself, so the two cannot
 * disagree — which is the whole point of handing this up on cross.
 */
export async function buildFisSchedule(
  matterId: number,
  request: FisRequest,
  categoryIds?: number[] | null,
): Promise<FisSchedule> {
  return apiFetch<FisSchedule>(`/api/v1/matters/${matterId}/fis/schedule`, {
    method: 'POST',
    body: JSON.stringify({ ...request, category_ids: categoryIds ?? null }),
  })
}

export async function exportFisSchedule(
  matterId: number,
  request: FisRequest,
  format: ExportFormat,
  exhibitName: string,
  categoryIds?: number[] | null,
): Promise<DownloadedFile> {
  return apiDownload(`/api/v1/matters/${matterId}/fis/schedule/export`, {
    ...request, category_ids: categoryIds ?? null,
    format, exhibit_name: exhibitName,
  })
}

/**
 * Confirm automatic assignments a person checked and agreed with.
 *
 * Leaves the category alone: agreeing with a rule is not the same act as filing
 * a line, and rewriting the source would erase the evidence the rule got it
 * right.
 */
export async function markTransactionsReviewed(
  matterId: number,
  transactionIds: number[],
): Promise<BulkResult> {
  return apiFetch<BulkResult>(`/api/v1/matters/${matterId}/transactions/review`, {
    method: 'POST',
    body: JSON.stringify({ transaction_ids: transactionIds }),
  })
}

/** Every payment schedule in force for one person, firm defaults included. */
export async function getFisSettings(
  clientId?: number | null,
  opposingPartyId?: number | null,
): Promise<FisSetting[]> {
  const params = new URLSearchParams()
  if (clientId) params.set('client_id', String(clientId))
  if (opposingPartyId) params.set('opposing_party_id', String(opposingPartyId))
  const q = params.toString() ? `?${params}` : ''
  return apiFetch<FisSetting[]>(`/api/v1/fis-settings${q}`)
}

/** Record a payment schedule, replacing this person's existing one. */
export async function saveFisSetting(payload: FisSettingPayload): Promise<FisSetting> {
  return apiFetch<FisSetting>('/api/v1/fis-settings', {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

/** Drop a schedule so the layer beneath it applies again. */
export async function deleteFisSetting(settingId: number): Promise<void> {
  await apiFetch<unknown>(`/api/v1/fis-settings/${settingId}`, { method: 'DELETE' })
}

export async function searchTransactions(
  matterId: number,
  filter: TransactionSearchFilter,
): Promise<TransactionSearchResult> {
  return apiFetch<TransactionSearchResult>(`/api/v1/matters/${matterId}/transactions/search`, {
    method: 'POST',
    body: JSON.stringify(filter),
  })
}

export async function categorizeTransactions(
  matterId: number,
  transactionIds: number[],
  categoryId: number | null,
): Promise<BulkResult> {
  return apiFetch<BulkResult>(`/api/v1/matters/${matterId}/transactions/categorize`, {
    method: 'POST',
    body: JSON.stringify({ transaction_ids: transactionIds, category_id: categoryId }),
  })
}

/** Apply or remove one tag across a set of transactions. */
export async function tagTransactions(
  matterId: number,
  transactionIds: number[],
  tagId: number,
  remove = false,
): Promise<BulkResult> {
  return apiFetch<BulkResult>(`/api/v1/matters/${matterId}/transactions/tag`, {
    method: 'POST',
    body: JSON.stringify({ transaction_ids: transactionIds, tag_id: tagId, remove }),
  })
}

// ── Leads (CRM) ───────────────────────────────────────────────────────

import type {
  LeadListItem, LeadDetail, LeadAction,
  StatusUpdatePayload, AssignPayload, PriorityUpdatePayload, FollowUpPayload,
  AgentTogglePayload, AddNotePayload, AddActionPayload,
  LeadPromotePayload, LeadPromoteResponse, LeadLinkClientPayload,
} from '../types'

export async function getLeads(limit = 100, offset = 0): Promise<LeadListItem[]> {
  const qs = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  return apiFetch<LeadListItem[]>(`/api/v1/leads?${qs.toString()}`)
}

export async function getLead(sessionUuid: string): Promise<LeadDetail> {
  return apiFetch<LeadDetail>(`/api/v1/leads/${sessionUuid}`)
}

export async function getLeadActions(sessionUuid: string): Promise<LeadAction[]> {
  return apiFetch<LeadAction[]>(`/api/v1/leads/${sessionUuid}/actions`)
}

export async function updateLeadStatus(sessionUuid: string, payload: StatusUpdatePayload): Promise<LeadDetail> {
  return apiFetch<LeadDetail>(`/api/v1/leads/${sessionUuid}/status`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function assignLead(sessionUuid: string, payload: AssignPayload): Promise<LeadDetail> {
  return apiFetch<LeadDetail>(`/api/v1/leads/${sessionUuid}/assign`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function updateLeadPriority(sessionUuid: string, payload: PriorityUpdatePayload): Promise<LeadDetail> {
  return apiFetch<LeadDetail>(`/api/v1/leads/${sessionUuid}/priority`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function setLeadFollowUp(sessionUuid: string, payload: FollowUpPayload): Promise<LeadDetail> {
  return apiFetch<LeadDetail>(`/api/v1/leads/${sessionUuid}/follow-up`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function toggleLeadAgent(sessionUuid: string, payload: AgentTogglePayload): Promise<LeadDetail> {
  return apiFetch<LeadDetail>(`/api/v1/leads/${sessionUuid}/agent`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function addLeadNote(sessionUuid: string, payload: AddNotePayload): Promise<LeadAction> {
  return apiFetch<LeadAction>(`/api/v1/leads/${sessionUuid}/notes`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

/**
 * Open a client and matter from a lead.
 *
 * The lead already cleared the conflict check, which is why this is the
 * intended way a client gets created.
 */
export async function promoteLead(
  sessionUuid: string,
  payload: LeadPromotePayload,
): Promise<LeadPromoteResponse> {
  return apiFetch<LeadPromoteResponse>(`/api/v1/leads/${sessionUuid}/promote`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

/** Point a lead at a client that already exists. Creates nothing. */
export async function linkLeadClient(
  sessionUuid: string,
  payload: LeadLinkClientPayload,
): Promise<LeadDetail> {
  return apiFetch<LeadDetail>(`/api/v1/leads/${sessionUuid}/link-client`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function addLeadAction(sessionUuid: string, payload: AddActionPayload): Promise<LeadAction> {
  return apiFetch<LeadAction>(`/api/v1/leads/${sessionUuid}/actions`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
