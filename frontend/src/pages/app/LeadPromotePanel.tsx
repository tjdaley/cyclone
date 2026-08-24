import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getConfig, previewMatterIntake, promoteLead } from '../../lib/api'
import type {
  LeadDetail, MatterIntakePreview, MatterType, DiscoveryLevel, FullName, IntakeNewClient,
} from '../../types'

const MATTER_TYPES: MatterType[] = [
  'divorce', 'child_custody', 'modification', 'enforcement',
  'cps', 'probate', 'estate_planning', 'civil', 'other',
]
const DISCOVERY_LEVELS: DiscoveryLevel[] = ['level_1', 'level_2', 'level_3']

function splitName(value: string): FullName {
  const parts = (value ?? '').trim().split(/\s+/).filter(Boolean)
  return {
    courtesy_title: null,
    first_name: parts[0] ?? '',
    middle_name: parts.length > 2 ? parts.slice(1, -1).join(' ') : null,
    last_name: parts.length > 1 ? parts[parts.length - 1] : '',
    suffix: null,
  }
}

/**
 * Same rule the backend applies (intake_service._match_confidence).
 *
 * First and last name must both agree — adverse spouses share a surname, so
 * a looser rule would match the lead to the wrong side of the caption.
 */
function sameName(a: string, b: string): boolean {
  const ta = (a ?? '').toLowerCase().match(/[a-z]+/g) ?? []
  const tb = (b ?? '').toLowerCase().match(/[a-z]+/g) ?? []
  if (!ta.length || !tb.length) return false
  if (ta[0] !== tb[0] || ta[ta.length - 1] !== tb[tb.length - 1]) return false
  const [shorter, longer] = ta.length <= tb.length ? [new Set(ta), new Set(tb)] : [new Set(tb), new Set(ta)]
  return [...shorter].every(w => longer.has(w))
}

/**
 * Promote a lead into a client + matter.
 *
 * A pleading can be dropped in to fill the case details — the same extraction
 * the Matters page uses. Because the lead tells us who our client is, the
 * "who do we represent" question answers itself whenever a party matches the
 * lead's name.
 */
export default function LeadPromotePanel({
  lead, onCancel, onPromoted,
}: {
  lead: LeadDetail
  onCancel: () => void
  onPromoted: () => void
}) {
  const navigate = useNavigate()
  const [referralTypes, setReferralTypes] = useState<string[]>([])
  useEffect(() => {
    getConfig().then(c => setReferralTypes(c.referral_types)).catch(() => setReferralTypes([]))
  }, [])

  // Client — prefilled from the lead, which is the whole point of promoting.
  const [client, setClient] = useState<IntakeNewClient>({
    name: splitName(lead.full_name ?? ''),
    auth_email: lead.email ?? '',
    email: lead.email ?? '',
    telephone: lead.telephone ?? '',
    referral_type: '',
    referral_source: lead.lead_source ?? '',
  })

  // Matter
  const [matterName, setMatterName]   = useState('')
  const [shortName, setShortName]     = useState('')
  const [matterType, setMatterType]   = useState<MatterType>('divorce')
  const [state, setState]             = useState(lead.state || 'Texas')
  const [county, setCounty]           = useState('')
  const [courtName, setCourtName]     = useState('')
  const [matterNumber, setMatterNumber] = useState('')
  const [discoveryLevel, setDiscoveryLevel] = useState<DiscoveryLevel | ''>('')

  // Optional pleading
  const [preview, setPreview]   = useState<MatterIntakePreview | null>(null)
  const [ourParty, setOurParty] = useState('')
  const [reading, setReading]   = useState(false)
  const [readStatus, setReadStatus] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const [busy, setBusy]   = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleFile(file: File) {
    if (file.type !== 'application/pdf') { setError('Only PDF files are accepted'); return }
    setReading(true); setError(null); setReadStatus('Uploading…')
    try {
      const p = await previewMatterIntake(file, (status, seconds) => {
        setReadStatus(
          status === 'queued' ? 'Queued for extraction…'
          : status === 'running' ? `Reading the pleading… ${seconds}s`
          : '',
        )
      })
      setPreview(p)
      // Fill anything the attorney has not already typed.
      setMatterName(v => v || p.case.suggested_matter_name || '')
      setMatterType(v => p.case.matter_type ?? v)
      setState(v => p.case.state || v)
      setCounty(v => v || p.case.county || '')
      setCourtName(v => v || p.case.court_name || '')
      setMatterNumber(v => v || p.case.matter_number || '')
      setDiscoveryLevel(v => v || (p.case.discovery_level ?? ''))
      // The lead tells us who we act for — pick the matching party automatically.
      const us = p.parties.find(x => sameName(x.full_name, lead.full_name ?? ''))
      setOurParty(us?.full_name ?? '')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not read that pleading')
    } finally {
      setReading(false); setReadStatus('')
    }
  }

  const adverse = useMemo(
    () => (preview && ourParty ? preview.parties.filter(p => !sameName(p.full_name, ourParty)) : []),
    [preview, ourParty],
  )

  const clientReady =
    client.name.first_name.trim() && client.name.last_name.trim() &&
    client.auth_email.trim() && client.email.trim() && client.telephone.trim() &&
    client.referral_type && client.referral_source.trim()
  const canSubmit = Boolean(clientReady && matterName.trim() && county.trim() && !busy &&
    (!preview || ourParty))

  async function handlePromote() {
    setBusy(true); setError(null)
    try {
      const res = await promoteLead(lead.session_uuid, {
        intake: {
          raw_text: preview?.raw_text ?? '',
          our_party_name: preview ? ourParty : null,
          existing_client_id: null,
          new_client: client,
          matter: {
            matter_name: matterName.trim(),
            short_name: shortName.trim() || null,
            matter_type: matterType,
            state: state.trim() || 'Texas',
            county: county.trim(),
            court_name: courtName.trim() || null,
            matter_number: matterNumber.trim() || null,
            discovery_level: discoveryLevel === '' ? null : discoveryLevel,
            opened_date: null,
          },
          case: preview?.case ?? null,
          parties: preview
            ? preview.parties.map(p => ({ full_name: p.full_name, designation: p.designation }))
            : [],
          attorneys: preview?.attorneys ?? [],
          children: (preview?.children ?? [])
            .filter(c => c.date_of_birth && c.name.first_name && c.name.last_name)
            .map(c => ({
              existing_id: null,
              name: c.name,
              date_of_birth: c.date_of_birth as string,
              sex: c.sex ?? 'other',
              needs_support_after_majority: c.needs_support_after_majority,
            })),
          claims: (preview?.claims ?? []).map(c => ({
            kind: c.kind, label: c.label, narrative: c.narrative,
            statute_rule_cited: c.statute_rule_cited, opposing_party_id: null,
            party_side: c.party_side,
          })),
        },
      })
      onPromoted()
      navigate(`/app/matters/${res.result.matter_id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not promote this lead')
      setBusy(false)
    }
  }

  return (
    <div className="card p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-navy">Promote to client</h3>
        <button type="button" className="text-xs text-text-secondary underline" onClick={onCancel}>
          Cancel
        </button>
      </div>
      <p className="text-xs text-text-secondary">
        This lead already cleared the conflict check, so promoting opens the file directly.
      </p>

      {/* Pleading — optional, and the fastest way to fill the matter in */}
      <div
        onDragOver={e => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={e => { e.preventDefault(); setDragOver(false) }}
        onDrop={e => {
          e.preventDefault(); setDragOver(false)
          const f = e.dataTransfer.files[0]; if (f) handleFile(f)
        }}
        className={`border-2 border-dashed rounded px-4 py-4 text-center transition-colors ${
          dragOver ? 'border-navy bg-navy/5' : 'border-border'}`}
      >
        {reading ? (
          <p className="text-sm text-text-secondary">{readStatus || 'Reading the pleading…'}</p>
        ) : preview ? (
          <div className="text-sm">
            <p className="text-text-primary">{preview.case.title}</p>
            <p className="text-xs text-text-secondary mt-0.5">
              Case details filled in below · {preview.children.length} children ·{' '}
              {preview.claims.length} claims will be created
            </p>
            <button type="button" className="text-xs text-navy underline mt-1"
              onClick={() => { setPreview(null); setOurParty('') }}>
              Remove pleading
            </button>
          </div>
        ) : (
          <>
            <p className="text-sm text-text-secondary">
              Optional: drop a pleading to fill in the case details
            </p>
            <button type="button" className="text-sm text-navy underline mt-1"
              onClick={() => fileRef.current?.click()}>
              or choose a PDF
            </button>
            <input ref={fileRef} type="file" accept="application/pdf" className="hidden"
              onChange={e => { const f = e.target.files?.[0]; if (f) handleFile(f); e.target.value = '' }} />
          </>
        )}
      </div>

      {/* When a pleading is present, confirm which party the lead is */}
      {preview && (
        <div>
          <label className="label">We represent</label>
          <select className="input mt-1" value={ourParty} onChange={e => setOurParty(e.target.value)}>
            <option value="">Select the party…</option>
            {preview.parties.map(p => (
              <option key={p.full_name} value={p.full_name}>
                {p.full_name}{p.designation ? ` — ${p.designation}` : ''}
              </option>
            ))}
          </select>
          {ourParty && sameName(ourParty, lead.full_name ?? '') && (
            <p className="text-xs text-green-700 mt-1">Matched to this lead automatically.</p>
          )}
          {adverse.length > 0 && (
            <p className="text-xs text-text-secondary mt-1">
              Adverse: {adverse.map(p => p.full_name).join(', ')}
            </p>
          )}
        </div>
      )}

      {/* Client */}
      <div className="border-t border-border pt-3">
        <p className="text-xs font-semibold text-text-secondary uppercase tracking-wide mb-2">Client</p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
          <input className="input text-sm" placeholder="First name" value={client.name.first_name}
            onChange={e => setClient(c => ({ ...c, name: { ...c.name, first_name: e.target.value } }))} />
          <input className="input text-sm" placeholder="Middle" value={client.name.middle_name ?? ''}
            onChange={e => setClient(c => ({ ...c, name: { ...c.name, middle_name: e.target.value || null } }))} />
          <input className="input text-sm" placeholder="Last name" value={client.name.last_name}
            onChange={e => setClient(c => ({ ...c, name: { ...c.name, last_name: e.target.value } }))} />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-2 mt-2">
          <input className="input text-sm" placeholder="Email" value={client.email}
            onChange={e => setClient(c => ({ ...c, email: e.target.value }))} />
          <input className="input text-sm" placeholder="Portal login email" value={client.auth_email}
            onChange={e => setClient(c => ({ ...c, auth_email: e.target.value }))} />
          <input className="input text-sm" placeholder="Telephone" value={client.telephone}
            onChange={e => setClient(c => ({ ...c, telephone: e.target.value }))} />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 mt-2">
          <select className="input text-sm" value={client.referral_type}
            onChange={e => setClient(c => ({ ...c, referral_type: e.target.value }))}>
            <option value="">Referral type…</option>
            {referralTypes.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
          <input className="input text-sm" placeholder="Referral source" value={client.referral_source}
            onChange={e => setClient(c => ({ ...c, referral_source: e.target.value }))} />
        </div>
      </div>

      {/* Matter */}
      <div className="border-t border-border pt-3">
        <p className="text-xs font-semibold text-text-secondary uppercase tracking-wide mb-2">Matter</p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          <input className="input text-sm" placeholder="Matter name" value={matterName}
            onChange={e => setMatterName(e.target.value)} />
          <input className="input text-sm" placeholder="Short name (optional)" value={shortName}
            onChange={e => setShortName(e.target.value)} />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-2 mt-2">
          <select className="input text-sm" value={matterType}
            onChange={e => setMatterType(e.target.value as MatterType)}>
            {MATTER_TYPES.map(t => <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>)}
          </select>
          <input className="input text-sm" placeholder="County" value={county}
            onChange={e => setCounty(e.target.value)} />
          <input className="input text-sm" placeholder="State" value={state}
            onChange={e => setState(e.target.value)} />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-2 mt-2">
          <input className="input text-sm" placeholder="Court" value={courtName}
            onChange={e => setCourtName(e.target.value)} />
          <input className="input text-sm" placeholder="Cause number" value={matterNumber}
            onChange={e => setMatterNumber(e.target.value)} />
          <select className="input text-sm" value={discoveryLevel}
            onChange={e => setDiscoveryLevel(e.target.value as DiscoveryLevel | '')}>
            <option value="">Discovery level…</option>
            {DISCOVERY_LEVELS.map(l => <option key={l} value={l}>{l.replace('_', ' ')}</option>)}
          </select>
        </div>
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <div className="flex items-center gap-3">
        <button type="button" className="btn-primary text-sm" disabled={!canSubmit} onClick={handlePromote}>
          {busy ? 'Opening…' : preview ? 'Open client, matter, and pleading' : 'Open client and matter'}
        </button>
        {!clientReady && (
          <span className="text-xs text-text-secondary">Client name, email, phone, and referral are required</span>
        )}
      </div>
    </div>
  )
}
