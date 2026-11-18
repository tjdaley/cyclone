import { useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { getClient, getMatter } from '../lib/api'
import type { Client, Matter } from '../types'

/**
 * Which matter this page is working on, at the top of every page that works on
 * one: the way back to its dashboard, its name and status, the court facts, and
 * the client.
 *
 * Pass `matter` (and `client`) when the page already holds them, so an edit on
 * the page shows here at once; otherwise the header loads them itself. Renders
 * nothing without a matter, which is how Discovery and Pleadings look before
 * one is chosen.
 */
export default function MatterHeader({ matterId, matter: given, client: givenClient, back = 'matter', actions }: {
  matterId: number | null | undefined
  matter?: Matter | null
  client?: Client | null
  /** Where the link at the top goes: this matter's dashboard, or the opening page from the dashboard itself. */
  back?: 'matter' | 'dashboard'
  /** Links shown at the right of the name. */
  actions?: ReactNode
}) {
  const [loaded, setLoaded] = useState<Matter | null>(null)
  const [loadedClient, setLoadedClient] = useState<Client | null>(null)
  const matter = given ?? loaded
  const client = givenClient ?? loadedClient

  useEffect(() => {
    if (!matterId || given) return
    let current = true
    getMatter(matterId).then(m => { if (current) setLoaded(m) }).catch(() => undefined)
    return () => { current = false }
  }, [matterId, given])

  useEffect(() => {
    if (!matter || givenClient !== undefined) return
    let current = true
    getClient(matter.client_id).then(c => { if (current) setLoadedClient(c) }).catch(() => undefined)
    return () => { current = false }
  }, [matter?.client_id, givenClient])  // eslint-disable-line react-hooks/exhaustive-deps

  if (!matterId) return null

  const backLink = back === 'dashboard'
    ? <Link to="/app/dashboard" className="text-xs text-text-secondary hover:text-navy">← Dashboard</Link>
    : <Link to={`/app/matters/${matterId}`} className="text-xs text-text-secondary hover:text-navy">← Matter dashboard</Link>

  if (!matter) return <div>{backLink}</div>

  const facts = [
    matter.matter_number,
    matter.billing_id && `Billing ID ${matter.billing_id}`,
    matter.court_name,
    `${matter.county} County, ${matter.state}`,
    matter.matter_type.replace(/_/g, ' '),
    matter.discovery_level ? matter.discovery_level.replace('_', ' ') : null,
  ].filter(Boolean).join(' · ')

  return (
    <div>
      {backLink}
      <div className="flex flex-wrap items-baseline gap-3 mt-1">
        <p className="font-display text-2xl text-navy">{matter.short_name ?? matter.matter_name}</p>
        <span className="text-xs rounded-full px-2.5 py-1 font-medium capitalize bg-off-white text-text-secondary border border-border">
          {matter.status.replace(/_/g, ' ')}
        </span>
        {matter.is_pro_bono && (
          <span className="text-xs rounded-full px-2 py-0.5 bg-purple-100 text-purple-700">Pro bono</span>
        )}
        {actions && <span className="ml-auto flex flex-wrap gap-3">{actions}</span>}
      </div>
      <p className="text-sm text-text-secondary mt-1">{facts}</p>
      {client && (
        <p className="text-sm text-text-secondary">
          Client: <span className="text-text-primary">
            {[client.name.first_name, client.name.middle_name, client.name.last_name, client.name.suffix]
              .filter(Boolean).join(' ')}
          </span>
        </p>
      )}
    </div>
  )
}
