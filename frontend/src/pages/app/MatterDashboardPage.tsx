import { useEffect, useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { getClient, getMatter, markMatterViewed } from '../../lib/api'
import MatterHeader from '../../components/MatterHeader'
import type { Client, Matter } from '../../types'

/**
 * The tile icons. Drawn inline in one line style, as the sidebar's are: the
 * project carries no icon library, and one set of strokes keeps them looking
 * like a family. Each is a 24-unit square.
 */
function Icon({ children }: { children: ReactNode }) {
  return (
    <svg className="w-8 h-8 text-navy" fill="none" stroke="currentColor" strokeWidth={1.5}
      strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24" aria-hidden="true">
      {children}
    </svg>
  )
}

const ICONS = {
  edit: <Icon><path d="M15.5 4.5l4 4L9 19H5v-4L15.5 4.5z" /><path d="M13 7l4 4" /><path d="M4 21h16" /></Icon>,
  details: <Icon><circle cx="9" cy="8" r="3.5" /><path d="M2.5 20c0-3.6 2.9-6.5 6.5-6.5s6.5 2.9 6.5 6.5" />
    <circle cx="17" cy="9" r="2.5" /><path d="M16 13.6c3.1-.4 5.5 2.1 5.5 5.4" /></Icon>,
  pleadings: <Icon><path d="M12 3v18" /><path d="M7 21h10" /><path d="M5 6h14" />
    <path d="M5 6l-3 7a3 3 0 0 0 6 0L5 6z" /><path d="M19 6l-3 7a3 3 0 0 0 6 0l-3-7z" /></Icon>,
  discovery: <Icon><rect x="5" y="4" width="14" height="17" rx="2" /><rect x="9" y="2" width="6" height="4" rx="1" />
    <path d="M9 11h6" /><path d="M9 15h6" /></Icon>,
  grid: <Icon><rect x="3" y="4" width="18" height="16" rx="1.5" /><path d="M3 9h18" /><path d="M3 14.5h18" />
    <path d="M9 4v16" /><path d="M15 4v16" /></Icon>,
  received: <Icon><path d="M5 4h14l2 9v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-6l2-9z" />
    <path d="M3 13h5l1.5 3h5L16 13h5" /></Icon>,
  import: <Icon><path d="M12 15V3" /><path d="M7.5 7.5L12 3l4.5 4.5" /><path d="M4 14v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5" /></Icon>,
  accounts: <Icon><path d="M3 9.5L12 4l9 5.5" /><path d="M4 10h16" /><path d="M6 10v7" /><path d="M10 10v7" />
    <path d="M14 10v7" /><path d="M18 10v7" /><path d="M3 20h18" /></Icon>,
  transactions: <Icon><circle cx="10.5" cy="10.5" r="6.5" /><path d="M15.5 15.5L21 21" />
    <path d="M12.5 8.3c-.4-.5-1.1-.8-2-.8-1.2 0-2 .6-2 1.5 0 2 4 1 4 3 0 .9-.9 1.5-2 1.5-.9 0-1.7-.4-2.1-1" />
    <path d="M10.5 6.5v1" /><path d="M10.5 13.5v1" /></Icon>,
  inventory: <Icon><path d="M8 6h13" /><path d="M8 12h13" /><path d="M8 18h13" />
    <circle cx="4" cy="6" r="1" /><circle cx="4" cy="12" r="1" /><circle cx="4" cy="18" r="1" /></Icon>,
  fis: <Icon><path d="M11 3.5a8.5 8.5 0 1 0 9.5 9.5H11V3.5z" /><path d="M14 2.5a7.5 7.5 0 0 1 7.5 7.5H14V2.5z" /></Icon>,
}

interface Tile {
  label: string
  detail: string
  to: string
  icon: ReactNode
}

/**
 * One matter, and every place its work is done.
 *
 * Navigation only: each tile opens a page that already exists, already scoped
 * to this matter. Counts of what is waiting are a planned second pass.
 */
function groups(id: number): { title: string; tiles: Tile[] }[] {
  const financials = (tab: string) => `/app/matters/${id}/financials?tab=${tab}`
  return [
    {
      title: 'Matter',
      tiles: [
        { label: 'Edit matter', to: `/app/matters/${id}/edit`, icon: ICONS.edit,
          detail: 'Name, status, court, cause number, billing ID, dates, retainer and rate overrides' },
        { label: 'Matter details', to: `/app/matters/${id}/details`, icon: ICONS.details,
          detail: 'Parties, staff, children, counsel, claims, caption, discovery scope, client portal and financial professionals' },
      ],
    },
    {
      title: 'Pleadings',
      tiles: [
        { label: 'Pleadings', to: `/app/pleadings?matter=${id}`, icon: ICONS.pleadings,
          detail: 'Ingest a pleading, review what it claims, and track which is live' },
      ],
    },
    {
      title: 'Discovery',
      tiles: [
        { label: 'Discovery requests', to: `/app/discovery?matter=${id}`, icon: ICONS.discovery,
          detail: 'Requests served on our client, and the responses' },
        { label: 'Statements held', to: financials('compliance'), icon: ICONS.grid,
          detail: 'Which months each side has produced, and the gaps' },
      ],
    },
    {
      title: 'Financials',
      tiles: [
        { label: 'Received documents', to: `/app/matters/${id}/received`, icon: ICONS.received,
          detail: 'Productions as served, and the splitting bench' },
        { label: 'Import statements', to: financials('import'), icon: ICONS.import,
          detail: 'Read statements, and work the exceptions queue' },
        { label: 'Accounts', to: financials('accounts'), icon: ICONS.accounts,
          detail: 'Every account, its statements and its characterization' },
        { label: 'Transactions', to: financials('transactions'), icon: ICONS.transactions,
          detail: 'Search, tag, categorize and export' },
        { label: 'Inventory', to: financials('inventory'), icon: ICONS.inventory,
          detail: 'Balances as assets and liabilities' },
        { label: 'Financial Information Statement', to: financials('fis'), icon: ICONS.fis,
          detail: 'Monthly income and expenses' },
      ],
    },
  ]
}

export default function MatterDashboardPage() {
  const { matterId: param } = useParams<{ matterId: string }>()
  const matterId = Number(param)
  const [matter, setMatter] = useState<Matter | null>(null)
  const [client, setClient] = useState<Client | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!matterId) return
    let current = true
    setMatter(null); setError(null)
    getMatter(matterId)
      .then(async m => {
        if (!current) return
        setMatter(m)
        const c = await getClient(m.client_id).catch(() => null)
        if (current) setClient(c)
      })
      .catch(e => { if (current) setError(e instanceof Error ? e.message : 'Could not load the matter') })
    // Recorded for the opening page's recent list. A failure costs only that.
    markMatterViewed(matterId).catch(() => undefined)
    return () => { current = false }
  }, [matterId])

  if (error) {
    return (
      <div className="px-6 py-16 text-center">
        <p className="text-red-600 text-sm mb-4">{error}</p>
        <Link to="/app/dashboard" className="text-navy underline text-sm">Back to the dashboard</Link>
      </div>
    )
  }
  if (!matter) {
    return <div className="px-6 py-16 text-center text-text-secondary text-sm">Loading matter…</div>
  }

  return (
    <div className="px-6 py-8 max-w-6xl mx-auto space-y-8">
      <MatterHeader matterId={matter.id} matter={matter} client={client} back="dashboard" />

      {groups(matter.id).map(group => (
        <section key={group.title}>
          <h2 className="font-display text-xl text-navy mb-3">{group.title}</h2>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {group.tiles.map(tile => (
              <Link key={tile.label} to={tile.to}
                className="card p-5 flex gap-4 items-start border border-border hover:border-navy hover:shadow-sm transition">
                <span className="shrink-0 rounded-lg bg-navy/5 p-2">{tile.icon}</span>
                <span>
                  <span className="block font-semibold text-navy">{tile.label}</span>
                  <span className="block text-sm text-text-secondary mt-1">{tile.detail}</span>
                </span>
              </Link>
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}
