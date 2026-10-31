import { useMemo } from 'react'
import { useParams } from 'react-router-dom'
import {
  exportGuestInventory, getGuestInventory, openGuestStatementPdf,
} from '../../lib/api'
import InventoryPanel, { type InventorySource } from '../app/InventoryPanel'

/**
 * The account inventory, read-only.
 *
 * The firm's own grid, through the guest's endpoints: the same figures, the
 * same exclusions in grey and conversions annotated, with the toggle and the
 * rate editor absent because a guest may change neither.
 */
export default function GuestInventoryPage() {
  const matterId = Number(useParams().matterId)
  const source = useMemo<InventorySource>(() => ({
    load: () => getGuestInventory(matterId),
    exportInventory: (format, name) => exportGuestInventory(matterId, format, name),
    openPdf: openGuestStatementPdf,
  }), [matterId])
  return <InventoryPanel matterId={matterId} source={source} />
}
