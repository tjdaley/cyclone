/**
 * The second line of a transaction: who it went to and where, for example
 * "Blue Bee Construction; McKinney, TX".
 *
 * A check prints nothing but its number, and the payee is what says where the
 * money went. A counterparty the description already spells out is left off, or
 * every card purchase would say its merchant twice.
 *
 * @returns The line, or '' when there is nothing to add.
 */
export function partyLine(
  description: string | null | undefined,
  counterparty: string | null | undefined,
  location: string | null | undefined,
): string {
  const party = (counterparty ?? '').trim()
  const place = (location ?? '').trim()
  const repeated = party && (description ?? '').toUpperCase().includes(party.toUpperCase())
  return [repeated ? '' : party, place].filter(Boolean).join('; ')
}
