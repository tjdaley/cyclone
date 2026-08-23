import type { FullName } from './common'
import type { MatterType } from './matter'
import type {
  ChildPreview, ClaimPreview, ChildCommitEntry, ClaimCommitEntry, DiscoveryLevel,
} from './pleading'

/**
 * Matter intake from a filed pleading.
 *
 * Mirrors app/schemas/intake.py. Extraction here is deliberately neutral —
 * no party is "opposing" until the attorney says who we represent.
 */

export interface IntakeClientMatch {
  client_id: number
  full_name: string
  /** 'strong' — every word of the shorter name matches; 'partial' — surname only. */
  confidence: 'strong' | 'partial'
}

/**
 * An unconverted lead who may be this party.
 *
 * Preferred over creating a client from the caption — the lead already cleared
 * the conflict check and carries contact details a pleading never has.
 */
export interface IntakeLeadMatch {
  session_uuid: string
  full_name: string
  email: string | null
  telephone: string | null
  status: string
  confidence: 'strong' | 'partial'
}

export interface IntakeParty {
  full_name: string
  /** Role in the caption: petitioner, respondent, counterpetitioner, … */
  designation: string | null
  client_matches: IntakeClientMatch[]
  lead_matches: IntakeLeadMatch[]
}

export interface IntakeAttorney {
  /** Party name this attorney appears for; how we tell ours from theirs. */
  represents: string | null
  name: FullName
  firm_name: string | null
  street_address: string | null
  street_address_2: string | null
  city: string | null
  state: string | null
  postal_code: string | null
  email: string | null
  cell_phone: string | null
  telephone: string | null
  fax: string | null
  bar_state: string | null
  bar_number: string | null
}

export interface IntakeCase {
  title: string
  filed_date: string | null
  served_date: string | null
  state: string | null
  county: string | null
  court_name: string | null
  matter_number: string | null
  matter_type: MatterType | null
  discovery_level: DiscoveryLevel | null
  suggested_matter_name: string | null
}

export interface MatterIntakePreview {
  raw_text: string
  case: IntakeCase
  parties: IntakeParty[]
  attorneys: IntakeAttorney[]
  children: ChildPreview[]
  claims: ClaimPreview[]
  warnings: string[]
}

export interface IntakeNewClient {
  name: FullName
  auth_email: string
  email: string
  telephone: string
  referral_type: string
  referral_source: string
  notes?: string | null
}

export interface IntakeMatterFields {
  matter_name: string
  short_name?: string | null
  matter_type: MatterType
  state: string
  county: string
  court_name?: string | null
  matter_number?: string | null
  discovery_level?: DiscoveryLevel | null
  opened_date?: string | null
  notes?: string | null
}

export interface IntakePartyCommitEntry {
  full_name: string
  designation: string | null
}

export interface MatterIntakeCommitRequest {
  raw_text: string
  /**
   * The party we represent; everything else on the pleading becomes adverse.
   * Null when opening a matter with no pleading (promoting a lead off a call).
   */
  our_party_name: string | null
  existing_client_id?: number | null
  new_client?: IntakeNewClient | null
  matter: IntakeMatterFields
  /** Null opens the client and matter with no pleading attached. */
  case: IntakeCase | null
  parties: IntakePartyCommitEntry[]
  attorneys: IntakeAttorney[]
  children: ChildCommitEntry[]
  claims: ClaimCommitEntry[]
}

export interface MatterIntakeCommitResponse {
  client_id: number
  client_created: boolean
  matter_id: number
  /** Null when no pleading was supplied. */
  pleading_id: number | null
  opposing_parties_created: number
  children_created: number
  opposing_counsel_linked: number
  claims_created: number
}
