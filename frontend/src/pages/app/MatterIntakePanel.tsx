import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { commitMatterIntake, getConfig, promoteLead } from '../../lib/api'
import type {
  MatterIntakePreview, IntakeParty, IntakeAttorney, IntakeNewClient,
  MatterType, DiscoveryLevel, FullName,
} from '../../types'

const MATTER_TYPES: MatterType[] = [
  'divorce', 'child_custody', 'modification', 'enforcement',
  'cps', 'probate', 'estate_planning', 'civil', 'other',
]
const DISCOVERY_LEVELS: DiscoveryLevel[] = ['level_1', 'level_2', 'level_3']

function fullName(n: FullName): string {
  return [n.first_name, n.middle_name, n.last_name, n.suffix].filter(Boolean).join(' ')
}

/** Split "Kaci Lynndon Salmons" into name parts for a prefilled client form. */
function splitName(value: string): FullName {
  const parts = value.trim().split(/\s+/)
  return {
    courtesy_title: null,
    first_name: parts[0] ?? '',
    middle_name: parts.length > 2 ? parts.slice(1, -1).join(' ') : null,
    last_name: parts.length > 1 ? parts[parts.length - 1] : '',
    suffix: null,
  }
}

/** Spell out a partial match so nobody accepts a surname-only hit by reflex. */
function m_confidenceNote(confidence: 'strong' | 'partial'): string {
  return confidence === 'partial' ? ' · surname only — confirm' : ''
}

/**
 * Same rule the backend applies (intake_service._match_confidence).
 *
 * First and last name must both agree — adverse spouses share a surname, so
 * a looser rule would call the opposing party the same person as our client.
 */
function sameName(a: string, b: string): boolean {
  const ta = a.toLowerCase().match(/[a-z]+/g) ?? []
  const tb = b.toLowerCase().match(/[a-z]+/g) ?? []
  if (!ta.length || !tb.length) return false
  if (ta[0] !== tb[0] || ta[ta.length - 1] !== tb[tb.length - 1]) return false
  const [shorter, longer] = ta.length <= tb.length ? [new Set(ta), new Set(tb)] : [new Set(tb), new Set(ta)]
  return [...shorter].every(w => longer.has(w))
}

function Card({ title, children, step }: { title: string; step?: number; children: React.ReactNode }) {
  return (
    <div className="card p-5">
      <h3 className="font-semibold text-navy mb-3">
        {step !== undefined && (
          <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-navy text-white text-xs mr-2">
            {step}
          </span>
        )}
        {title}
      </h3>
      {children}
    </div>
  )
}

export default function MatterIntakePanel({
  preview, onCancel,
}: {
  preview: MatterIntakePreview
  onCancel: () => void
}) {
  const navigate = useNavigate()
  const [referralTypes, setReferralTypes] = useState<string[]>([])

  useEffect(() => {
    getConfig().then(c => setReferralTypes(c.referral_types)).catch(() => setReferralTypes([]))
  }, [])

  // Step 1 — the single answer everything else derives from.
  const [ourParty, setOurParty] = useState<string>('')

  // Step 2 — which record that party is. A number is an existing client, a
  // `lead:<uuid>` promotes that lead, and 'new' creates a client from scratch.
  const [clientChoice, setClientChoice] = useState<number | string>('')
  const [newClient, setNewClient] = useState<IntakeNewClient>({
    name: { courtesy_title: null, first_name: '', middle_name: null, last_name: '', suffix: null },
    auth_email: '', email: '', telephone: '', referral_type: '', referral_source: '',
  })

  // Step 3 — the matter, prefilled from the case style.
  const [matterName, setMatterName]   = useState(preview.case.suggested_matter_name ?? '')
  const [shortName, setShortName]     = useState('')
  const [matterType, setMatterType]   = useState<MatterType>(preview.case.matter_type ?? 'divorce')
  const [state, setState]             = useState(preview.case.state ?? 'Texas')
  const [county, setCounty]           = useState(preview.case.county ?? '')
  const [courtName, setCourtName]     = useState(preview.case.court_name ?? '')
  const [matterNumber, setMatterNumber] = useState(preview.case.matter_number ?? '')
  const [discoveryLevel, setDiscoveryLevel] = useState<DiscoveryLevel | ''>(preview.case.discovery_level ?? '')

  const [committing, setCommitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const selectedParty: IntakeParty | undefined = preview.parties.find(p => p.full_name === ourParty)

  // Everything adverse follows from the party choice — shown so the attorney
  // can see the consequence before committing.
  const { adverseParties, opposingCounsel, ourCounsel, unsavableCounsel } = useMemo(() => {
    if (!ourParty) {
      return { adverseParties: [], opposingCounsel: [], ourCounsel: [], unsavableCounsel: [] }
    }
    const adverse = preview.parties.filter(p => !sameName(p.full_name, ourParty))
    const ours: IntakeAttorney[] = []
    const theirs: IntakeAttorney[] = []
    const unsavable: IntakeAttorney[] = []
    preview.attorneys.forEach(a => {
      if (a.represents && sameName(a.represents, ourParty)) { ours.push(a); return }
      if (!a.bar_state || !a.bar_number) { unsavable.push(a); return }
      theirs.push(a)
    })
    return { adverseParties: adverse, opposingCounsel: theirs, ourCounsel: ours, unsavableCounsel: unsavable }
  }, [ourParty, preview.parties, preview.attorneys])

  // Prefill from whichever party was chosen, preferring an existing client,
  // then a lead (already conflict-checked and better data), then a fresh form.
  useEffect(() => {
    if (!ourParty) return
    const match = preview.parties.find(p => p.full_name === ourParty)
    const lead = match?.lead_matches[0]
    setNewClient(c => ({
      ...c,
      name: splitName(ourParty),
      email: lead?.email ?? c.email,
      auth_email: lead?.email ?? c.auth_email,
      telephone: lead?.telephone ?? c.telephone,
    }))
    if (match?.client_matches.length) setClientChoice(match.client_matches[0].client_id)
    else if (lead) setClientChoice(`lead:${lead.session_uuid}`)
    else setClientChoice('new')
  }, [ourParty, preview.parties])

  const promotingLead = typeof clientChoice === 'string' && clientChoice.startsWith('lead:')
  const leadUuid = promotingLead ? clientChoice.slice(5) : null
  const needsClientForm = clientChoice === 'new' || promotingLead

  const clientReady = clientChoice !== '' && (
    !needsClientForm || (
      newClient.name.first_name.trim() && newClient.name.last_name.trim() &&
      newClient.auth_email.trim() && newClient.email.trim() &&
      newClient.telephone.trim() && newClient.referral_type && newClient.referral_source.trim()
    )
  )
  const canCommit = Boolean(ourParty && clientReady && matterName.trim() && county.trim() && !committing)

  async function handleCommit() {
    setCommitting(true); setError(null)
    try {
      const intake = {
        raw_text: preview.raw_text,
        our_party_name: ourParty,
        existing_client_id: needsClientForm ? null : Number(clientChoice),
        new_client: needsClientForm ? newClient : null,
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
        case: preview.case,
        parties: preview.parties.map(p => ({ full_name: p.full_name, designation: p.designation })),
        attorneys: preview.attorneys,
        children: preview.children
          .filter(c => c.date_of_birth && c.name.first_name && c.name.last_name)
          .map(c => ({
            existing_id: null,
            name: c.name,
            date_of_birth: c.date_of_birth as string,
            sex: c.sex ?? 'other',
            needs_support_after_majority: c.needs_support_after_majority,
          })),
        claims: preview.claims.map(c => ({
          kind: c.kind, label: c.label, narrative: c.narrative,
          statute_rule_cited: c.statute_rule_cited, opposing_party_id: null,
        })),
      }

      // Promoting a lead goes through the lead endpoint so the conversion is
      // recorded on it; otherwise this is an ordinary intake.
      const result = leadUuid
        ? (await promoteLead(leadUuid, { intake })).result
        : await commitMatterIntake(intake)

      navigate(`/app/matters/${result.matter_id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not open the matter')
      setCommitting(false)
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-display text-xl text-navy">Open a matter from this pleading</h2>
          <p className="text-sm text-text-secondary">
            {preview.case.title}
            {preview.case.matter_number && ` · ${preview.case.matter_number}`}
          </p>
        </div>
        <button type="button" className="text-sm text-text-secondary underline" onClick={onCancel}>
          Discard
        </button>
      </div>

      {preview.warnings.length > 0 && (
        <div className="card p-4 bg-amber-50 border border-amber-200">
          <ul className="text-sm text-amber-800 list-disc list-inside space-y-1">
            {preview.warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </div>
      )}

      {/* ── 1. Who do we represent? ── */}
      <Card title="Who do we represent?" step={1}>
        {preview.parties.length === 0 ? (
          <p className="text-sm text-red-600">
            No parties were found in this document — it may not be a pleading, or the case style
            could not be read.
          </p>
        ) : (
          <div className="space-y-2">
            {preview.parties.map(p => (
              <label key={p.full_name}
                className={`flex items-center gap-3 p-3 rounded border cursor-pointer transition-colors ${
                  ourParty === p.full_name ? 'border-navy bg-navy/5' : 'border-border hover:bg-off-white'}`}>
                <input type="radio" name="our-party" className="w-4 h-4 accent-navy"
                  checked={ourParty === p.full_name}
                  onChange={() => setOurParty(p.full_name)} />
                <span className="text-sm font-medium text-text-primary">{p.full_name}</span>
                {p.designation && (
                  <span className="text-xs text-text-secondary capitalize">{p.designation}</span>
                )}
                {p.client_matches.length > 0 ? (
                  <span className="ml-auto text-xs rounded-full px-2 py-0.5 bg-green-100 text-green-800">
                    {p.client_matches[0].confidence === 'strong' ? 'existing client' : 'possible client match'}
                  </span>
                ) : p.lead_matches.length > 0 && (
                  <span className="ml-auto text-xs rounded-full px-2 py-0.5 bg-blue-100 text-blue-800">
                    {p.lead_matches[0].confidence === 'strong' ? 'existing lead' : 'possible lead match'}
                  </span>
                )}
              </label>
            ))}
          </div>
        )}
      </Card>

      {ourParty && (
        <>
          {/* ── 2. Which client record? ── */}
          <Card title="Client record" step={2}>
            <div className="space-y-2">
              {(selectedParty?.client_matches ?? []).map(m => (
                <label key={m.client_id}
                  className={`flex items-center gap-3 p-3 rounded border cursor-pointer ${
                    clientChoice === m.client_id ? 'border-navy bg-navy/5' : 'border-border hover:bg-off-white'}`}>
                  <input type="radio" name="client-choice" className="w-4 h-4 accent-navy"
                    checked={clientChoice === m.client_id}
                    onChange={() => setClientChoice(m.client_id)} />
                  <span className="text-sm text-text-primary">{m.full_name}</span>
                  <span className="text-xs text-text-secondary">
                    {m.confidence === 'strong' ? 'name matches' : 'surname only — confirm'}
                  </span>
                </label>
              ))}
              {/* Leads come before "create new": one already passed the
                  conflict check, so promoting it is the intended path. */}
              {(selectedParty?.lead_matches ?? []).map(l => (
                <label key={l.session_uuid}
                  className={`flex items-center gap-3 p-3 rounded border cursor-pointer ${
                    clientChoice === `lead:${l.session_uuid}` ? 'border-navy bg-navy/5' : 'border-border hover:bg-off-white'}`}>
                  <input type="radio" name="client-choice" className="w-4 h-4 accent-navy"
                    checked={clientChoice === `lead:${l.session_uuid}`}
                    onChange={() => setClientChoice(`lead:${l.session_uuid}`)} />
                  <span className="text-sm text-text-primary">{l.full_name}</span>
                  <span className="text-xs rounded-full px-2 py-0.5 bg-blue-100 text-blue-800">lead</span>
                  <span className="text-xs text-text-secondary">
                    {l.status.replace(/_/g, ' ')}
                    {l.email && ` · ${l.email}`}
                    {m_confidenceNote(l.confidence)}
                  </span>
                </label>
              ))}

              <label className={`flex items-center gap-3 p-3 rounded border cursor-pointer ${
                clientChoice === 'new' ? 'border-navy bg-navy/5' : 'border-border hover:bg-off-white'}`}>
                <input type="radio" name="client-choice" className="w-4 h-4 accent-navy"
                  checked={clientChoice === 'new'} onChange={() => setClientChoice('new')} />
                <span className="text-sm text-text-primary">
                  {(selectedParty?.client_matches.length ?? 0) + (selectedParty?.lead_matches.length ?? 0)
                    ? 'None of these — create a new client'
                    : 'Create a new client'}
                </span>
              </label>
            </div>

            {needsClientForm && (
              <div className="mt-3 border-t border-border pt-3 space-y-2">
                <p className="text-xs text-text-secondary">
                  {promotingLead
                    ? 'Contact details come from the lead. Confirm them and add a referral to open the client file.'
                    : 'The pleading gives us a name only. The rest is required to open a client file.'}
                </p>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                  <input className="input text-sm" placeholder="First name" value={newClient.name.first_name}
                    onChange={e => setNewClient(c => ({ ...c, name: { ...c.name, first_name: e.target.value } }))} />
                  <input className="input text-sm" placeholder="Middle" value={newClient.name.middle_name ?? ''}
                    onChange={e => setNewClient(c => ({ ...c, name: { ...c.name, middle_name: e.target.value || null } }))} />
                  <input className="input text-sm" placeholder="Last name" value={newClient.name.last_name}
                    onChange={e => setNewClient(c => ({ ...c, name: { ...c.name, last_name: e.target.value } }))} />
                </div>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                  <input className="input text-sm" placeholder="Email" value={newClient.email}
                    onChange={e => setNewClient(c => ({ ...c, email: e.target.value, auth_email: c.auth_email || e.target.value }))} />
                  <input className="input text-sm" placeholder="Portal login email" value={newClient.auth_email}
                    onChange={e => setNewClient(c => ({ ...c, auth_email: e.target.value }))} />
                  <input className="input text-sm" placeholder="Telephone" value={newClient.telephone}
                    onChange={e => setNewClient(c => ({ ...c, telephone: e.target.value }))} />
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                  <select className="input text-sm" value={newClient.referral_type}
                    onChange={e => setNewClient(c => ({ ...c, referral_type: e.target.value }))}>
                    <option value="">Referral type…</option>
                    {referralTypes.map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                  <input className="input text-sm" placeholder="Referral source" value={newClient.referral_source}
                    onChange={e => setNewClient(c => ({ ...c, referral_source: e.target.value }))} />
                </div>
              </div>
            )}
          </Card>

          {/* ── 3. What follows from that choice ── */}
          <Card title="Adverse parties and counsel" step={3}>
            <p className="text-xs text-text-secondary mb-3">
              Derived from your answer above. Everything that is not {ourParty} is adverse.
            </p>
            <div className="grid md:grid-cols-2 gap-4 text-sm">
              <div>
                <p className="text-xs font-semibold text-text-secondary uppercase tracking-wide mb-1.5">
                  Opposing parties
                </p>
                {adverseParties.length === 0 ? (
                  <p className="text-text-secondary text-sm">None</p>
                ) : adverseParties.map(p => (
                  <div key={p.full_name} className="text-text-primary">
                    {p.full_name}
                    {p.designation && <span className="text-xs text-text-secondary ml-2 capitalize">{p.designation}</span>}
                  </div>
                ))}
              </div>
              <div>
                <p className="text-xs font-semibold text-text-secondary uppercase tracking-wide mb-1.5">
                  Opposing counsel
                </p>
                {opposingCounsel.length === 0 ? (
                  <p className="text-text-secondary text-sm">None</p>
                ) : opposingCounsel.map((a, i) => (
                  <div key={i} className="text-text-primary">
                    {fullName(a.name)}
                    {a.firm_name && <span className="text-xs text-text-secondary ml-2">{a.firm_name}</span>}
                  </div>
                ))}
              </div>
            </div>

            {(ourCounsel.length > 0 || unsavableCounsel.length > 0) && (
              <div className="mt-3 pt-3 border-t border-border space-y-1">
                {ourCounsel.map((a, i) => (
                  <p key={`ours-${i}`} className="text-xs text-text-secondary">
                    {fullName(a.name)} appears for us — not saved as opposing counsel.
                  </p>
                ))}
                {unsavableCounsel.map((a, i) => (
                  <p key={`nobar-${i}`} className="text-xs text-amber-700">
                    {fullName(a.name)} has no bar number in this document, so cannot be saved yet —
                    add them on the matter page afterwards.
                  </p>
                ))}
              </div>
            )}
          </Card>

          {/* ── 4. The matter ── */}
          <Card title="Matter" step={4}>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="label">Matter name</label>
                <input className="input mt-1" value={matterName} onChange={e => setMatterName(e.target.value)} />
              </div>
              <div>
                <label className="label">Short name</label>
                <input className="input mt-1" placeholder="Optional" value={shortName}
                  onChange={e => setShortName(e.target.value)} />
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-3">
              <div>
                <label className="label">Type</label>
                <select className="input mt-1" value={matterType}
                  onChange={e => setMatterType(e.target.value as MatterType)}>
                  {MATTER_TYPES.map(t => <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>)}
                </select>
              </div>
              <div>
                <label className="label">County</label>
                <input className="input mt-1" value={county} onChange={e => setCounty(e.target.value)} />
              </div>
              <div>
                <label className="label">State</label>
                <input className="input mt-1" value={state} onChange={e => setState(e.target.value)} />
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-3">
              <div>
                <label className="label">Court</label>
                <input className="input mt-1" value={courtName} onChange={e => setCourtName(e.target.value)} />
              </div>
              <div>
                <label className="label">Cause number</label>
                <input className="input mt-1" value={matterNumber} onChange={e => setMatterNumber(e.target.value)} />
              </div>
              <div>
                <label className="label">Discovery level</label>
                <select className="input mt-1" value={discoveryLevel}
                  onChange={e => setDiscoveryLevel(e.target.value as DiscoveryLevel | '')}>
                  <option value="">—</option>
                  {DISCOVERY_LEVELS.map(l => <option key={l} value={l}>{l.replace('_', ' ')}</option>)}
                </select>
              </div>
            </div>
          </Card>

          {/* ── 5. Carried through ── */}
          <Card title="Also created from this pleading" step={5}>
            <p className="text-sm text-text-secondary">
              {preview.children.length} children · {preview.claims.length} claims ·
              the pleading itself, with its text stored for later re-extraction.
              All of it is editable on the matter page once the file is open.
            </p>
          </Card>

          {error && <p className="text-sm text-red-600">{error}</p>}

          <div className="flex items-center gap-3">
            <button type="button" className="btn-primary" disabled={!canCommit} onClick={handleCommit}>
              {committing ? 'Opening…' : 'Open matter'}
            </button>
            <button type="button" className="btn-secondary" onClick={onCancel}>Cancel</button>
            {!clientReady && clientChoice === 'new' && (
              <span className="text-xs text-text-secondary">Fill in the client details to continue</span>
            )}
          </div>
        </>
      )}
    </div>
  )
}
