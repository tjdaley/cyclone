import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  getMatter, getClients, getStaff,
  updateMatter,
  getMatterStaff, addMatterStaff, updateMatterStaff, deleteMatterStaff,
  getMatterChildren, createMatterChild, updateMatterChild, deleteMatterChild,
  getMatterClaims, createMatterClaim, updateMatterClaim, deleteMatterClaim,
  getMatterPleadings, getOpposingParties, createOpposingParty, deleteOpposingParty,
  openPleadingPdf,
  getMatterOpposingCounsel, getMatterCounselLinks,
  createOpposingCounsel, linkOpposingCounsel, updateMatterCounselLink, unlinkOpposingCounsel,
} from '../../lib/api'
import type {
  Matter, Client, Staff, MatterStaff, MatterStaffRole, OpposingParty,
  MatterChild, ChildSex, MatterClaim, ClaimKind, MatterPleading, PleadingStatus,
  OpposingCounsel, MatterCounselLink, CounselRole, FullName, ClientAlignment,
} from '../../types'
import { CLIENT_ALIGNMENTS } from '../../types'

const STAFF_ROLES: MatterStaffRole[] = ['originating', 'billing_reviewer', 'assigned']
const COUNSEL_ROLES: CounselRole[] = ['lead', 'co_counsel', 'local_counsel', 'prior_counsel']

/**
 * Display rank for counsel: lead at the top, prior counsel at the bottom,
 * everyone else in between in whatever order they arrived.
 */
const COUNSEL_ROLE_RANK: Record<CounselRole, number> = {
  lead: 0,
  co_counsel: 1,
  local_counsel: 1,
  prior_counsel: 2,
}

const COUNSEL_ROLE_COLOR: Record<CounselRole, string> = {
  lead:          'bg-navy/10 text-navy',
  co_counsel:    'bg-off-white text-text-secondary',
  local_counsel: 'bg-off-white text-text-secondary',
  prior_counsel: 'bg-gray-100 text-gray-500',
}

/** Strip formatting so tel: gets digits (and a leading +) rather than punctuation. */
function telHref(phone: string): string {
  return `tel:${phone.replace(/[^\d+]/g, '')}`
}
const CLAIM_KINDS: ClaimKind[] = ['claim', 'defense', 'affirmative_defense', 'counterclaim']

const PLEADING_STATUS_COLOR: Record<PleadingStatus, string> = {
  live:       'bg-green-100 text-green-800',
  superseded: 'bg-gray-100 text-gray-600',
  withdrawn:  'bg-amber-100 text-amber-800',
  inactive:   'bg-gray-100 text-gray-600',
}

const CLAIM_KIND_COLOR: Record<ClaimKind, string> = {
  claim: 'bg-teal-100 text-teal-800',
  defense: 'bg-amber-100 text-amber-800',
  affirmative_defense: 'bg-purple-100 text-purple-800',
  counterclaim: 'bg-indigo-100 text-indigo-800',
}

function blankName(): FullName {
  return { courtesy_title: null, first_name: '', middle_name: null, last_name: '', suffix: null }
}

function fullName(n: FullName): string {
  return [n.first_name, n.middle_name, n.last_name, n.suffix].filter(Boolean).join(' ')
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric',
  })
}

/** Age in whole years, the way a court cares about it. */
function ageFrom(iso: string): number {
  const dob = new Date(`${iso}T00:00:00`)
  const now = new Date()
  let age = now.getFullYear() - dob.getFullYear()
  const beforeBirthday =
    now.getMonth() < dob.getMonth() ||
    (now.getMonth() === dob.getMonth() && now.getDate() < dob.getDate())
  return beforeBirthday ? age - 1 : age
}

/** Section wrapper: title, count, an add button, and a slot for the add form. */
function Section({ title, count, actionLabel, onAction, children }: {
  title: string
  count?: number
  actionLabel?: string
  onAction?: () => void
  children: React.ReactNode
}) {
  return (
    <div className="card p-5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="font-semibold text-navy">
          {title}
          {count !== undefined && <span className="ml-2 text-sm font-normal text-text-secondary">{count}</span>}
        </h2>
        {actionLabel && onAction && (
          <button type="button" className="text-xs text-navy underline" onClick={onAction}>{actionLabel}</button>
        )}
      </div>
      {children}
    </div>
  )
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-text-secondary">{children}</p>
}

export default function MatterDetailPage() {
  const { matterId: matterIdParam } = useParams<{ matterId: string }>()
  const matterId = Number(matterIdParam)

  const [matter, setMatter]   = useState<Matter | null>(null)
  const [client, setClient]   = useState<Client | null>(null)
  const [staff, setStaff]     = useState<Staff[]>([])
  const [parties, setParties] = useState<OpposingParty[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState<string | null>(null)

  const [matterStaff, setMatterStaff] = useState<MatterStaff[]>([])
  const [children, setChildren]       = useState<MatterChild[]>([])
  const [claims, setClaims]           = useState<MatterClaim[]>([])
  const [pleadings, setPleadings]     = useState<MatterPleading[]>([])
  const [counsel, setCounsel]         = useState<OpposingCounsel[]>([])
  const [links, setLinks]             = useState<MatterCounselLink[]>([])

  // One error line per section keeps a failed save next to the thing that failed.
  const [sectionError, setSectionError] = useState<Record<string, string | null>>({})
  function fail(section: string, e: unknown) {
    setSectionError(prev => ({ ...prev, [section]: e instanceof Error ? e.message : 'Something went wrong' }))
  }
  function clearFail(section: string) {
    setSectionError(prev => ({ ...prev, [section]: null }))
  }

  useEffect(() => {
    if (!matterId) return
    let cancelled = false
    setLoading(true)
    Promise.all([
      getMatter(matterId), getClients(), getStaff(), getOpposingParties(matterId),
      getMatterStaff(matterId), getMatterChildren(matterId), getMatterClaims(matterId),
      getMatterPleadings(matterId), getMatterOpposingCounsel(matterId), getMatterCounselLinks(matterId),
    ])
      .then(([m, cl, st, op, ms, ch, cla, pl, oc, lk]) => {
        if (cancelled) return
        setMatter(m)
        setClient(cl.find(c => c.id === m.client_id) ?? null)
        setStaff(st); setParties(op); setMatterStaff(ms); setChildren(ch)
        setClaims(cla); setPleadings(pl); setCounsel(oc); setLinks(lk)
      })
      .catch(e => !cancelled && setError(e instanceof Error ? e.message : 'Failed to load matter'))
      .finally(() => !cancelled && setLoading(false))
    return () => { cancelled = true }
  }, [matterId])

  const staffName = (id: number) => {
    const s = staff.find(x => x.id === id)
    return s ? fullName(s.name) : `Staff #${id}`
  }
  const partyName = (id: number | null) =>
    id === null ? null : (parties.find(p => p.id === id)?.full_name ?? `Party #${id}`)

  // ── Staff ───────────────────────────────────────────────────────────
  const [addingStaff, setAddingStaff] = useState(false)
  const [newStaffId, setNewStaffId]   = useState<number | ''>('')
  const [newStaffRole, setNewStaffRole] = useState<MatterStaffRole>('assigned')
  const [newStaffSplit, setNewStaffSplit] = useState('')

  const originationTotal = useMemo(
    () => matterStaff.filter(s => s.role === 'originating')
                     .reduce((sum, s) => sum + (s.split_pct ?? 0), 0),
    [matterStaff],
  )

  async function handleAddStaff() {
    if (newStaffId === '') return
    clearFail('staff')
    try {
      const row = await addMatterStaff(matterId, {
        staff_id: Number(newStaffId),
        role: newStaffRole,
        split_pct: newStaffSplit === '' ? null : Number(newStaffSplit),
      })
      setMatterStaff(prev => [...prev, row])
      setAddingStaff(false); setNewStaffId(''); setNewStaffSplit(''); setNewStaffRole('assigned')
    } catch (e) { fail('staff', e) }
  }

  async function handleStaffSplit(row: MatterStaff, value: string) {
    clearFail('staff')
    try {
      const updated = await updateMatterStaff(matterId, row.id, {
        split_pct: value === '' ? null : Number(value),
      })
      setMatterStaff(prev => prev.map(s => s.id === row.id ? updated : s))
    } catch (e) { fail('staff', e) }
  }

  async function handleStaffRole(row: MatterStaff, role: MatterStaffRole) {
    clearFail('staff')
    try {
      const updated = await updateMatterStaff(matterId, row.id, { role })
      setMatterStaff(prev => prev.map(s => s.id === row.id ? updated : s))
    } catch (e) { fail('staff', e) }
  }

  async function handleRemoveStaff(row: MatterStaff) {
    clearFail('staff')
    try {
      await deleteMatterStaff(matterId, row.id)
      setMatterStaff(prev => prev.filter(s => s.id !== row.id))
    } catch (e) { fail('staff', e) }
  }

  // ── Opposing parties ────────────────────────────────────────────────
  const [addingParty, setAddingParty] = useState(false)
  const [newParty, setNewParty] = useState({ full_name: '', relationship: '' })

  async function handleAddParty() {
    clearFail('parties')
    try {
      const row = await createOpposingParty(matterId, newParty.full_name, newParty.relationship || null)
      setParties(prev => [...prev, row])
      setAddingParty(false)
      setNewParty({ full_name: '', relationship: '' })
    } catch (e) { fail('parties', e) }
  }

  async function handleRemoveParty(party: OpposingParty) {
    clearFail('parties')
    try {
      await deleteOpposingParty(matterId, party.id)
      setParties(prev => prev.filter(p => p.id !== party.id))
    } catch (e) { fail('parties', e) }
  }

  // ── Children ────────────────────────────────────────────────────────
  const [addingChild, setAddingChild] = useState(false)
  const [newChild, setNewChild] = useState({
    name: blankName(), date_of_birth: '', sex: 'female' as ChildSex,
  })

  async function handleAddChild() {
    clearFail('children')
    try {
      const row = await createMatterChild(matterId, {
        name: newChild.name, date_of_birth: newChild.date_of_birth, sex: newChild.sex,
      })
      setChildren(prev => [...prev, row])
      setAddingChild(false)
      setNewChild({ name: blankName(), date_of_birth: '', sex: 'female' })
    } catch (e) { fail('children', e) }
  }

  async function handleToggleSupport(child: MatterChild) {
    clearFail('children')
    try {
      const updated = await updateMatterChild(child.id, {
        needs_support_after_majority: !child.needs_support_after_majority,
      })
      setChildren(prev => prev.map(c => c.id === child.id ? updated : c))
    } catch (e) { fail('children', e) }
  }

  async function handleRemoveChild(child: MatterChild) {
    clearFail('children')
    try {
      await deleteMatterChild(child.id)
      setChildren(prev => prev.filter(c => c.id !== child.id))
    } catch (e) { fail('children', e) }
  }

  // ── Opposing counsel ────────────────────────────────────────────────
  const [addingCounsel, setAddingCounsel] = useState(false)
  const [newCounsel, setNewCounsel] = useState({
    name: blankName(), firm_name: '', email: '', telephone: '',
    bar_state: 'TX', bar_number: '', role: 'lead' as CounselRole,
    opposing_party_id: '' as number | '',
  })

  async function handleAddCounsel() {
    clearFail('counsel')
    try {
      // Create-or-reuse the shared counsel record, then attach it here.
      const oc = await createOpposingCounsel({
        name: newCounsel.name,
        firm_name: newCounsel.firm_name || null,
        email: newCounsel.email || null,
        telephone: newCounsel.telephone || null,
        bar_state: newCounsel.bar_state,
        bar_number: newCounsel.bar_number,
      })
      const link = await linkOpposingCounsel(matterId, {
        opposing_counsel_id: oc.id,
        role: newCounsel.role,
        opposing_party_id: newCounsel.opposing_party_id === '' ? null : Number(newCounsel.opposing_party_id),
      })
      setCounsel(prev => prev.some(c => c.id === oc.id) ? prev : [...prev, oc])
      setLinks(prev => [...prev, link])
      setAddingCounsel(false)
      setNewCounsel({
        name: blankName(), firm_name: '', email: '', telephone: '',
        bar_state: 'TX', bar_number: '', role: 'lead', opposing_party_id: '',
      })
    } catch (e) { fail('counsel', e) }
  }

  async function handleLinkField(link: MatterCounselLink, patch: { role?: CounselRole; opposing_party_id?: number | null }) {
    clearFail('counsel')
    try {
      const updated = await updateMatterCounselLink(matterId, link.id, patch)
      setLinks(prev => prev.map(l => l.id === link.id ? updated : l))
    } catch (e) { fail('counsel', e) }
  }

  async function handleUnlink(link: MatterCounselLink) {
    clearFail('counsel')
    try {
      await unlinkOpposingCounsel(matterId, link.id)
      setLinks(prev => prev.filter(l => l.id !== link.id))
    } catch (e) { fail('counsel', e) }
  }

  // Lead first, prior counsel last, everyone else in between. Sorted for display
  // only — the stored order is meaningless.
  const sortedLinks = useMemo(
    () => [...links].sort((a, b) => COUNSEL_ROLE_RANK[a.role] - COUNSEL_ROLE_RANK[b.role]),
    [links],
  )

  // ── Claims ──────────────────────────────────────────────────────────
  const [addingClaim, setAddingClaim] = useState(false)
  const [newClaim, setNewClaim] = useState({
    matter_pleading_id: '' as number | '', kind: 'claim' as ClaimKind,
    label: '', narrative: '', statute_rule_cited: '',
  })
  const [editingClaimId, setEditingClaimId] = useState<number | null>(null)
  const [claimDraft, setClaimDraft] = useState({ label: '', narrative: '', statute_rule_cited: '', kind: 'claim' as ClaimKind })

  const [showAllClaims, setShowAllClaims] = useState(false)

  const claimsByPleading = useMemo(() => {
    const grouped = new Map<number, MatterClaim[]>()
    claims.forEach(c => {
      grouped.set(c.matter_pleading_id, [...(grouped.get(c.matter_pleading_id) ?? []), c])
    })
    return grouped
  }, [claims])

  // Only claims pleaded in a live pleading count as the current case.
  const withClaims = pleadings.filter(p => claimsByPleading.has(p.id))
  const visibleClaimPleadings = showAllClaims ? withClaims : withClaims.filter(p => p.status === 'live')
  const hiddenClaimCount = withClaims
    .filter(p => p.status !== 'live')
    .reduce((sum, p) => sum + (claimsByPleading.get(p.id) ?? []).length, 0)

  async function handleAddClaim() {
    if (newClaim.matter_pleading_id === '') return
    clearFail('claims')
    try {
      const row = await createMatterClaim(matterId, {
        matter_pleading_id: Number(newClaim.matter_pleading_id),
        kind: newClaim.kind,
        label: newClaim.label,
        narrative: newClaim.narrative,
        statute_rule_cited: newClaim.statute_rule_cited || null,
      })
      setClaims(prev => [...prev, row])
      setAddingClaim(false)
      setNewClaim({ matter_pleading_id: '', kind: 'claim', label: '', narrative: '', statute_rule_cited: '' })
    } catch (e) { fail('claims', e) }
  }

  function startEditClaim(claim: MatterClaim) {
    setEditingClaimId(claim.id)
    setClaimDraft({
      label: claim.label, narrative: claim.narrative,
      statute_rule_cited: claim.statute_rule_cited ?? '', kind: claim.kind,
    })
  }

  async function handleSaveClaim(claim: MatterClaim) {
    clearFail('claims')
    try {
      const updated = await updateMatterClaim(claim.id, {
        label: claimDraft.label,
        narrative: claimDraft.narrative,
        statute_rule_cited: claimDraft.statute_rule_cited || null,
        kind: claimDraft.kind,
      })
      setClaims(prev => prev.map(c => c.id === claim.id ? updated : c))
      setEditingClaimId(null)
    } catch (e) { fail('claims', e) }
  }

  async function handleDeleteClaim(claim: MatterClaim) {
    clearFail('claims')
    try {
      await deleteMatterClaim(claim.id)
      setClaims(prev => prev.filter(c => c.id !== claim.id))
    } catch (e) { fail('claims', e) }
  }

  // ── Render ──────────────────────────────────────────────────────────

  if (loading) {
    return <div className="px-6 py-16 text-center text-text-secondary text-sm">Loading matter…</div>
  }
  if (error || !matter) {
    return (
      <div className="px-6 py-16 text-center">
        <p className="text-red-600 text-sm mb-4">{error ?? 'Matter not found'}</p>
        <Link to="/app/matters" className="text-navy underline text-sm">Back to matters</Link>
      </div>
    )
  }

  const unassignedStaff = staff.filter(s => !matterStaff.some(ms => ms.staff_id === s.id && ms.role === newStaffRole))

  return (
    <div className="px-6 py-8 max-w-5xl mx-auto space-y-5">
      {/* Header */}
      <div>
        <Link to="/app/matters" className="text-xs text-text-secondary hover:text-navy">← Matters</Link>
        <div className="flex flex-wrap items-baseline gap-3 mt-1">
          <h1 className="font-display text-2xl text-navy">{matter.short_name ?? matter.matter_name}</h1>
          <span className="text-xs rounded-full px-2.5 py-1 font-medium capitalize bg-off-white text-text-secondary border border-border">
            {matter.status.replace(/_/g, ' ')}
          </span>
          {matter.is_pro_bono && (
            <span className="text-xs rounded-full px-2 py-0.5 bg-purple-100 text-purple-700">Pro bono</span>
          )}
          <Link to={`/app/matters/${matterId}/financials`}
            className="ml-auto text-sm text-navy underline">Financials →</Link>
        </div>
        <p className="text-sm text-text-secondary mt-1">
          {[
            matter.matter_number,
            matter.court_name,
            `${matter.county} County, ${matter.state}`,
            matter.matter_type.replace(/_/g, ' '),
            matter.discovery_level ? matter.discovery_level.replace('_', ' ') : null,
          ].filter(Boolean).join(' · ')}
        </p>
        {client && (
          <p className="text-sm text-text-secondary">
            Client: <span className="text-text-primary">{fullName(client.name)}</span>
          </p>
        )}
      </div>

      {/* ── Exhibit caption ── */}
      <CaptionSection matter={matter} onSaved={setMatter} />

      {/* ── Opposing parties ── */}
      <Section title="Opposing parties" count={parties.length}
        actionLabel={addingParty ? 'Cancel' : '+ Add party'}
        onAction={() => setAddingParty(v => !v)}>
        {sectionError.parties && <p className="text-xs text-red-600 mb-2">{sectionError.parties}</p>}

        {addingParty && (
          <div className="border border-border rounded p-3 mb-3 grid grid-cols-1 md:grid-cols-3 gap-2">
            <input className="input text-sm md:col-span-1" placeholder="Full name"
              value={newParty.full_name}
              onChange={e => setNewParty(p => ({ ...p, full_name: e.target.value }))} />
            <input className="input text-sm" placeholder="Relationship (e.g. spouse)"
              value={newParty.relationship}
              onChange={e => setNewParty(p => ({ ...p, relationship: e.target.value }))} />
            <button type="button" className="btn-primary text-sm"
              disabled={!newParty.full_name.trim()} onClick={handleAddParty}>Add</button>
          </div>
        )}

        {parties.length === 0 ? (
          <Empty>
            None on this matter. Counsel and claims can only be assigned to a party that exists here.
          </Empty>
        ) : (
          <div className="space-y-2">
            {parties.map(party => (
              <div key={party.id} className="flex flex-wrap items-center gap-3 text-sm">
                <span className="font-medium text-text-primary">{party.full_name}</span>
                {party.relationship && (
                  <span className="text-xs text-text-secondary">{party.relationship}</span>
                )}
                <span className="text-xs text-text-secondary">
                  {links.filter(l => l.opposing_party_id === party.id).length} counsel
                </span>
                <button type="button" className="text-red-500 text-xs ml-auto"
                  onClick={() => handleRemoveParty(party)}>Remove</button>
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* ── Staff ── */}
      <Section title="Staff" count={matterStaff.length}
        actionLabel={addingStaff ? 'Cancel' : '+ Assign staff'}
        onAction={() => setAddingStaff(v => !v)}>
        {sectionError.staff && <p className="text-xs text-red-600 mb-2">{sectionError.staff}</p>}

        {addingStaff && (
          <div className="border border-border rounded p-3 mb-3 grid grid-cols-1 md:grid-cols-4 gap-2">
            <select className="input text-sm" value={newStaffId}
              onChange={e => setNewStaffId(e.target.value === '' ? '' : Number(e.target.value))}>
              <option value="">Select staff…</option>
              {unassignedStaff.map(s => <option key={s.id} value={s.id}>{fullName(s.name)}</option>)}
            </select>
            <select className="input text-sm" value={newStaffRole}
              onChange={e => setNewStaffRole(e.target.value as MatterStaffRole)}>
              {STAFF_ROLES.map(r => <option key={r} value={r}>{r.replace(/_/g, ' ')}</option>)}
            </select>
            <input className="input text-sm" type="number" min={0} max={100} placeholder="Split %"
              value={newStaffSplit} onChange={e => setNewStaffSplit(e.target.value)}
              disabled={newStaffRole !== 'originating'} />
            <button type="button" className="btn-primary text-sm" disabled={newStaffId === ''}
              onClick={handleAddStaff}>Assign</button>
          </div>
        )}

        {matterStaff.length === 0 ? <Empty>No staff assigned.</Empty> : (
          <div className="space-y-2">
            {matterStaff.map(row => (
              <div key={row.id} className="flex flex-wrap items-center gap-2 text-sm">
                <span className="text-text-primary font-medium min-w-[10rem]">{staffName(row.staff_id)}</span>
                <select className="input text-xs w-40 py-1" value={row.role}
                  onChange={e => handleStaffRole(row, e.target.value as MatterStaffRole)}>
                  {STAFF_ROLES.map(r => <option key={r} value={r}>{r.replace(/_/g, ' ')}</option>)}
                </select>
                {row.role === 'originating' ? (
                  <input className="input text-xs w-24 py-1" type="number" min={0} max={100}
                    defaultValue={row.split_pct ?? ''} placeholder="%"
                    onBlur={e => {
                      const next = e.target.value === '' ? null : Number(e.target.value)
                      if (next !== row.split_pct) handleStaffSplit(row, e.target.value)
                    }} />
                ) : <span className="text-xs text-text-secondary w-24">—</span>}
                <button type="button" className="text-red-500 text-xs ml-auto"
                  onClick={() => handleRemoveStaff(row)}>Remove</button>
              </div>
            ))}

            {/* Origination is capped at 100% by a database trigger; show the running total. */}
            {matterStaff.some(s => s.role === 'originating') && (
              <div className="pt-2 mt-1 border-t border-border">
                <div className="flex items-center gap-3 text-xs">
                  <span className="text-text-secondary w-24">Origination</span>
                  <div className="flex-1 h-2 rounded-full bg-off-white overflow-hidden max-w-xs">
                    <div className={`h-full ${originationTotal > 100 ? 'bg-danger' : 'bg-navy'}`}
                      style={{ width: `${Math.min(originationTotal, 100)}%` }} />
                  </div>
                  <span className={originationTotal > 100 ? 'text-red-600 font-medium' : 'text-text-secondary'}>
                    {originationTotal}% of 100%
                  </span>
                </div>
              </div>
            )}
          </div>
        )}
      </Section>

      {/* ── Children ── */}
      <Section title="Children" count={children.length}
        actionLabel={addingChild ? 'Cancel' : '+ Add child'}
        onAction={() => setAddingChild(v => !v)}>
        {sectionError.children && <p className="text-xs text-red-600 mb-2">{sectionError.children}</p>}

        {addingChild && (
          <div className="border border-border rounded p-3 mb-3 grid grid-cols-1 md:grid-cols-5 gap-2">
            <input className="input text-sm" placeholder="First name" value={newChild.name.first_name}
              onChange={e => setNewChild(c => ({ ...c, name: { ...c.name, first_name: e.target.value } }))} />
            <input className="input text-sm" placeholder="Middle" value={newChild.name.middle_name ?? ''}
              onChange={e => setNewChild(c => ({ ...c, name: { ...c.name, middle_name: e.target.value || null } }))} />
            <input className="input text-sm" placeholder="Last name" value={newChild.name.last_name}
              onChange={e => setNewChild(c => ({ ...c, name: { ...c.name, last_name: e.target.value } }))} />
            <input className="input text-sm" type="date" value={newChild.date_of_birth}
              onChange={e => setNewChild(c => ({ ...c, date_of_birth: e.target.value }))} />
            <div className="flex gap-2">
              <select className="input text-sm" value={newChild.sex}
                onChange={e => setNewChild(c => ({ ...c, sex: e.target.value as ChildSex }))}>
                <option value="female">Female</option>
                <option value="male">Male</option>
                <option value="other">Other</option>
              </select>
              <button type="button" className="btn-primary text-sm whitespace-nowrap"
                disabled={!newChild.name.first_name || !newChild.name.last_name || !newChild.date_of_birth}
                onClick={handleAddChild}>Add</button>
            </div>
          </div>
        )}

        {children.length === 0 ? <Empty>No children on this matter.</Empty> : (
          <div className="space-y-2">
            {children.map(child => (
              <div key={child.id} className="flex flex-wrap items-center gap-3 text-sm">
                <span className="font-medium text-text-primary">{fullName(child.name)}</span>
                <span className="text-xs text-text-secondary capitalize">{child.sex}</span>
                <span className="text-xs text-text-secondary">
                  {formatDate(child.date_of_birth)} · {ageFrom(child.date_of_birth)}y
                </span>
                <label className="flex items-center gap-1.5 text-xs cursor-pointer">
                  <input type="checkbox" className="w-3.5 h-3.5 accent-navy"
                    checked={child.needs_support_after_majority}
                    onChange={() => handleToggleSupport(child)} />
                  <span className="text-text-secondary">support past majority</span>
                </label>
                <button type="button" className="text-red-500 text-xs ml-auto"
                  onClick={() => handleRemoveChild(child)}>Remove</button>
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* ── Opposing counsel ── */}
      <Section title="Opposing counsel" count={links.length}
        actionLabel={addingCounsel ? 'Cancel' : '+ Add counsel'}
        onAction={() => setAddingCounsel(v => !v)}>
        {sectionError.counsel && <p className="text-xs text-red-600 mb-2">{sectionError.counsel}</p>}

        {addingCounsel && (
          <div className="border border-border rounded p-3 mb-3 space-y-2">
            <p className="text-xs text-text-secondary">
              An attorney already known by this bar number is reused, not duplicated.
            </p>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
              <input className="input text-sm" placeholder="First name" value={newCounsel.name.first_name}
                onChange={e => setNewCounsel(c => ({ ...c, name: { ...c.name, first_name: e.target.value } }))} />
              <input className="input text-sm" placeholder="Last name" value={newCounsel.name.last_name}
                onChange={e => setNewCounsel(c => ({ ...c, name: { ...c.name, last_name: e.target.value } }))} />
              <input className="input text-sm" placeholder="Firm" value={newCounsel.firm_name}
                onChange={e => setNewCounsel(c => ({ ...c, firm_name: e.target.value }))} />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-2">
              <input className="input text-sm" placeholder="Bar state" value={newCounsel.bar_state}
                onChange={e => setNewCounsel(c => ({ ...c, bar_state: e.target.value }))} />
              <input className="input text-sm" placeholder="Bar number" value={newCounsel.bar_number}
                onChange={e => setNewCounsel(c => ({ ...c, bar_number: e.target.value }))} />
              <input className="input text-sm" placeholder="Email" value={newCounsel.email}
                onChange={e => setNewCounsel(c => ({ ...c, email: e.target.value }))} />
              <input className="input text-sm" placeholder="Telephone" value={newCounsel.telephone}
                onChange={e => setNewCounsel(c => ({ ...c, telephone: e.target.value }))} />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
              <select className="input text-sm" value={newCounsel.role}
                onChange={e => setNewCounsel(c => ({ ...c, role: e.target.value as CounselRole }))}>
                {COUNSEL_ROLES.map(r => <option key={r} value={r}>{r.replace(/_/g, ' ')}</option>)}
              </select>
              <select className="input text-sm" value={newCounsel.opposing_party_id}
                onChange={e => setNewCounsel(c => ({
                  ...c, opposing_party_id: e.target.value === '' ? '' : Number(e.target.value),
                }))}>
                <option value="">Represents… (optional)</option>
                {parties.map(p => <option key={p.id} value={p.id}>{p.full_name}</option>)}
              </select>
              <button type="button" className="btn-primary text-sm"
                disabled={!newCounsel.name.last_name || !newCounsel.bar_state || !newCounsel.bar_number}
                onClick={handleAddCounsel}>Add counsel</button>
            </div>
          </div>
        )}

        {links.length === 0 ? <Empty>No opposing counsel on this matter.</Empty> : (
          <div className="space-y-3">
            {sortedLinks.map(link => {
              const oc = counsel.find(c => c.id === link.opposing_counsel_id)
              const isPrior = link.role === 'prior_counsel'
              return (
                <div key={link.id} className={`border border-border rounded p-3 ${isPrior ? 'opacity-70' : ''}`}>
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="font-medium text-text-primary">
                      {oc ? fullName(oc.name) : `Counsel #${link.opposing_counsel_id}`}
                    </span>
                    <span className={`text-xs rounded-full px-2 py-0.5 font-medium ${COUNSEL_ROLE_COLOR[link.role]}`}>
                      {link.role.replace(/_/g, ' ')}
                    </span>
                    {oc?.firm_name && <span className="text-sm text-text-secondary">{oc.firm_name}</span>}
                    {oc && (
                      <span className="text-xs font-mono text-text-secondary">
                        {oc.bar_state} {oc.bar_number}
                      </span>
                    )}
                    <button type="button" className="text-red-500 text-xs ml-auto"
                      onClick={() => handleUnlink(link)}>Remove from matter</button>
                  </div>

                  {/* Contact actions — one click to write or call. */}
                  {oc && (oc.email || oc.telephone || oc.cell_phone) && (
                    <div className="flex flex-wrap items-center gap-3 mt-1.5">
                      {oc.email && (
                        <a href={`mailto:${oc.email}`} className="text-xs text-navy underline"
                          title={`Email ${oc.email}`}>
                          ✉ {oc.email}
                        </a>
                      )}
                      {oc.telephone && (
                        <a href={telHref(oc.telephone)} className="text-xs text-navy underline"
                          title={`Call ${oc.telephone}`}>
                          ✆ {oc.telephone}
                        </a>
                      )}
                      {oc.cell_phone && (
                        <a href={telHref(oc.cell_phone)} className="text-xs text-navy underline"
                          title={`Call cell ${oc.cell_phone}`}>
                          ✆ {oc.cell_phone} <span className="text-text-secondary">cell</span>
                        </a>
                      )}
                    </div>
                  )}
                  <div className="flex flex-wrap items-center gap-2 mt-2">
                    <select className="input text-xs w-36 py-1" value={link.role}
                      onChange={e => handleLinkField(link, { role: e.target.value as CounselRole })}>
                      {COUNSEL_ROLES.map(r => <option key={r} value={r}>{r.replace(/_/g, ' ')}</option>)}
                    </select>
                    <select className="input text-xs w-48 py-1" value={link.opposing_party_id ?? ''}
                      onChange={e => handleLinkField(link, {
                        opposing_party_id: e.target.value === '' ? null : Number(e.target.value),
                      })}>
                      <option value="">Represents…</option>
                      {parties.map(p => <option key={p.id} value={p.id}>{p.full_name}</option>)}
                    </select>
                    {link.opposing_party_id !== null && (
                      <span className="text-xs text-text-secondary">
                        for {partyName(link.opposing_party_id)}
                      </span>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </Section>

      {/* ── Claims, grouped by the pleading they were pleaded in ── */}
      <Section title="Claims" count={claims.length}
        actionLabel={addingClaim ? 'Cancel' : '+ Add claim'}
        onAction={() => setAddingClaim(v => !v)}>
        {sectionError.claims && <p className="text-xs text-red-600 mb-2">{sectionError.claims}</p>}

        {addingClaim && (
          <div className="border border-border rounded p-3 mb-3 space-y-2">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
              <select className="input text-sm" value={newClaim.matter_pleading_id}
                onChange={e => setNewClaim(c => ({
                  ...c, matter_pleading_id: e.target.value === '' ? '' : Number(e.target.value),
                }))}>
                <option value="">Pleading…</option>
                {pleadings.map(p => <option key={p.id} value={p.id}>{p.title}</option>)}
              </select>
              <select className="input text-sm" value={newClaim.kind}
                onChange={e => setNewClaim(c => ({ ...c, kind: e.target.value as ClaimKind }))}>
                {CLAIM_KINDS.map(k => <option key={k} value={k}>{k.replace(/_/g, ' ')}</option>)}
              </select>
              <input className="input text-sm" placeholder="Label" value={newClaim.label}
                onChange={e => setNewClaim(c => ({ ...c, label: e.target.value }))} />
            </div>
            <textarea className="input text-sm w-full h-20 resize-y" placeholder="Narrative as pleaded"
              value={newClaim.narrative} onChange={e => setNewClaim(c => ({ ...c, narrative: e.target.value }))} />
            <div className="flex gap-2">
              <input className="input text-sm flex-1" placeholder="Statute or rule cited (optional)"
                value={newClaim.statute_rule_cited}
                onChange={e => setNewClaim(c => ({ ...c, statute_rule_cited: e.target.value }))} />
              <button type="button" className="btn-primary text-sm"
                disabled={newClaim.matter_pleading_id === '' || !newClaim.label || !newClaim.narrative}
                onClick={handleAddClaim}>Add claim</button>
            </div>
            {pleadings.length === 0 && (
              <p className="text-xs text-amber-700">
                No pleadings on this matter yet — a claim has to belong to one.{' '}
                <Link to="/app/pleadings" className="underline">Ingest a pleading</Link> first.
              </p>
            )}
          </div>
        )}

        {claims.length === 0 ? <Empty>No claims recorded.</Empty> : (
          <div className="space-y-4">
            {/* Claims from superseded or withdrawn pleadings are history — hidden
                until asked for, rather than mixed in with the operative ones. */}
            {hiddenClaimCount > 0 && (
              <button type="button" className="text-xs text-navy underline"
                onClick={() => setShowAllClaims(v => !v)}>
                {showAllClaims
                  ? `Hide ${hiddenClaimCount} from non-live pleadings`
                  : `Show ${hiddenClaimCount} from non-live pleadings`}
              </button>
            )}
            {visibleClaimPleadings.length === 0 && (
              <Empty>No claims on live pleadings.</Empty>
            )}
            {visibleClaimPleadings
              .map(pleading => (
                <div key={pleading.id} className={pleading.status === 'live' ? '' : 'opacity-60'}>
                  <p className="text-xs font-semibold text-text-secondary uppercase tracking-wide mb-1.5">
                    {pleading.title}
                    {pleading.status !== 'live' && (
                      <span className="ml-2 font-normal normal-case">({pleading.status})</span>
                    )}
                    {pleading.filed_date && <span className="ml-2 font-normal normal-case">filed {formatDate(pleading.filed_date)}</span>}
                  </p>
                  <div className="space-y-1.5">
                    {(claimsByPleading.get(pleading.id) ?? []).map(claim => (
                      <div key={claim.id} className="border border-border rounded p-2.5">
                        {editingClaimId === claim.id ? (
                          <div className="space-y-2">
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                              <select className="input text-sm" value={claimDraft.kind}
                                onChange={e => setClaimDraft(d => ({ ...d, kind: e.target.value as ClaimKind }))}>
                                {CLAIM_KINDS.map(k => <option key={k} value={k}>{k.replace(/_/g, ' ')}</option>)}
                              </select>
                              <input className="input text-sm" value={claimDraft.label}
                                onChange={e => setClaimDraft(d => ({ ...d, label: e.target.value }))} />
                            </div>
                            <textarea className="input text-sm w-full h-24 resize-y" value={claimDraft.narrative}
                              onChange={e => setClaimDraft(d => ({ ...d, narrative: e.target.value }))} />
                            <input className="input text-sm w-full" placeholder="Statute or rule cited"
                              value={claimDraft.statute_rule_cited}
                              onChange={e => setClaimDraft(d => ({ ...d, statute_rule_cited: e.target.value }))} />
                            <div className="flex gap-2">
                              <button type="button" className="btn-primary text-xs"
                                onClick={() => handleSaveClaim(claim)}>Save</button>
                              <button type="button" className="text-xs text-text-secondary underline"
                                onClick={() => setEditingClaimId(null)}>Cancel</button>
                            </div>
                          </div>
                        ) : (
                          <>
                            <div className="flex flex-wrap items-baseline gap-2">
                              <span className={`text-xs rounded-full px-2 py-0.5 font-medium ${CLAIM_KIND_COLOR[claim.kind]}`}>
                                {claim.kind.replace(/_/g, ' ')}
                              </span>
                              <span className="font-medium text-text-primary text-sm">{claim.label}</span>
                              {claim.opposing_party_id !== null && (
                                <span className="text-xs text-text-secondary">
                                  {partyName(claim.opposing_party_id)}
                                </span>
                              )}
                              <span className="ml-auto flex gap-2">
                                <button type="button" className="text-xs text-navy underline"
                                  onClick={() => startEditClaim(claim)}>Edit</button>
                                <button type="button" className="text-xs text-red-500"
                                  onClick={() => handleDeleteClaim(claim)}>Delete</button>
                              </span>
                            </div>
                            <p className="text-sm text-text-secondary mt-1 whitespace-pre-wrap">{claim.narrative}</p>
                            {claim.statute_rule_cited && (
                              <p className="text-xs font-mono text-text-secondary mt-1">{claim.statute_rule_cited}</p>
                            )}
                          </>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ))}

            {/* A claim whose pleading is missing would otherwise render nowhere. */}
            {claims.filter(c => !pleadings.some(p => p.id === c.matter_pleading_id)).map(orphan => (
              <div key={orphan.id} className="border border-amber-300 bg-amber-50 rounded p-2.5 text-sm">
                <span className="font-medium">{orphan.label}</span>
                <span className="text-xs text-amber-700 ml-2">
                  attached to pleading #{orphan.matter_pleading_id}, which is not on this matter
                </span>
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* ── Pleadings (read-only pointer) ── */}
      <Section title="Pleadings" count={pleadings.length}>
        {sectionError.pleadings && <p className="text-xs text-red-600 mb-2">{sectionError.pleadings}</p>}
        {pleadings.length === 0 ? (
          <Empty>
            None ingested. <Link to="/app/pleadings" className="text-navy underline">Ingest a pleading</Link>.
          </Empty>
        ) : (
          <div className="space-y-1.5">
            {pleadings.map(p => (
              <div key={p.id} className={`flex flex-wrap items-baseline gap-2 text-sm ${p.status === 'live' ? '' : 'opacity-60'}`}>
                <span className="text-text-primary">{p.title}</span>
                <span className={`text-xs rounded-full px-2 py-0.5 font-medium ${PLEADING_STATUS_COLOR[p.status]}`}>
                  {p.status}
                </span>
                {p.is_supplement && (
                  <span className="text-xs rounded-full px-2 py-0.5 bg-blue-100 text-blue-800">supplement</span>
                )}
                <span className="text-xs text-text-secondary">
                  filed {formatDate(p.filed_date)} · served {formatDate(p.served_date)}
                </span>
                <span className="ml-auto flex items-baseline gap-3">
                  <span className="text-xs text-text-secondary">
                    {(claimsByPleading.get(p.id) ?? []).length} claims
                  </span>
                  {p.storage_path && (
                    <button type="button" className="text-xs text-navy underline"
                      onClick={() => openPleadingPdf(p.id).catch(e => fail('pleadings', e))}>
                      View PDF
                    </button>
                  )}
                </span>
              </div>
            ))}
          </div>
        )}
      </Section>
    </div>
  )
}


/**
 * The two caption fields nothing else in Cyclone can supply.
 *
 * Everything else a court caption needs is already on the matter — cause
 * number, court, county, state. These two are not derivable: `matter_name` is
 * the internal short name ("Salmons divorce"), which is not how a caption
 * reads, and which side we are on is captured at intake only for the OTHER
 * parties.
 *
 * They live here rather than in the export dialog because they are facts about
 * the case, not about one document. Set once, and every exhibit the matter ever
 * produces is headed correctly.
 */
function CaptionSection({ matter, onSaved }: {
  matter: Matter
  onSaved: (m: Matter) => void
}) {
  const [style, setStyle] = useState(matter.case_style ?? '')
  const [alignment, setAlignment] = useState<ClientAlignment | ''>(matter.client_alignment ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const dirty = style !== (matter.case_style ?? '') || alignment !== (matter.client_alignment ?? '')

  async function save() {
    setBusy(true)
    setError(null)
    try {
      onSaved(await updateMatter(matter.id, {
        case_style: style.trim() || null,
        client_alignment: alignment || null,
      }))
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save')
    } finally {
      setBusy(false)
    }
  }

  const alignmentLabel = CLIENT_ALIGNMENTS.find(a => a.value === alignment)?.label

  return (
    <Section title="Exhibit caption">
      <p className="text-sm text-text-secondary mb-3">
        Heads every exhibit generated from this matter. The cause number, court, and
        county come from the matter itself; these two do not exist anywhere else.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="md:col-span-2">
          <label className="label" htmlFor="case-style">Case style</label>
          <textarea id="case-style" className="input text-sm w-full" rows={2}
            placeholder="IN THE MATTER OF THE MARRIAGE OF JANE DOE AND JOHN DOE"
            value={style} onChange={e => setStyle(e.target.value)} />
          <p className="text-xs text-text-secondary mt-1">
            As written on a filing — not the short name.
          </p>
        </div>
        <div>
          <label className="label" htmlFor="alignment">We represent the</label>
          <select id="alignment" className="input text-sm w-full"
            value={alignment} onChange={e => setAlignment(e.target.value as ClientAlignment | '')}>
            <option value="">— not set —</option>
            {CLIENT_ALIGNMENTS.map(a => (
              <option key={a.value} value={a.value}>{a.label}</option>
            ))}
          </select>
          <p className="text-xs text-text-secondary mt-1">
            Titles the exhibit.
          </p>
        </div>
      </div>

      {/* What the caption will actually say. Cheaper to check here than to
          find out from a document already handed to someone. */}
      <div className="mt-4 border border-border rounded bg-off-white px-4 py-3 text-center space-y-1">
        <p className="text-sm font-bold text-navy">
          Cause No: <span className="underline">{matter.matter_number || '__________'}</span>
        </p>
        <p className="text-sm font-bold text-navy">
          In the {matter.court_name || '__________'} of {matter.county} County, {matter.state}
        </p>
        <p className="text-sm text-text-primary">
          {style.trim() || <span className="text-text-secondary italic">{matter.matter_name} — set a case style</span>}
        </p>
        <p className="text-sm font-bold text-navy pt-1">
          {alignmentLabel ? `${alignmentLabel}'s ` : ''}[exhibit name]
        </p>
      </div>

      {error && <p className="text-xs text-red-600 mt-2">{error}</p>}

      <div className="flex items-center gap-3 mt-3">
        <button type="button" className="btn-primary text-sm"
          disabled={!dirty || busy} onClick={() => void save()}>
          {busy ? 'Saving…' : 'Save caption'}
        </button>
        {saved && <span className="text-xs text-success">Saved</span>}
      </div>
    </Section>
  )
}
