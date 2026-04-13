export type DocumentRequestType = 'interrogatories' | 'production' | 'disclosures' | 'admissions'
export type DiscoveryRequestStatus = 'pending_client' | 'pending_review' | 'finalized' | 'objected'

/** Mirrors DiscoveryDocumentResponse in app/schemas/discovery.py */
export interface DiscoveryDocument {
  id: number
  matter_id: number
  ingested_by_staff_id: number
  propounded_date: string
  due_date: string
  request_type: DocumentRequestType
  look_back_date: string | null
  response_served_date: string | null
}

/** Mirrors DiscoveryRequestItemResponse in app/schemas/discovery.py */
export interface DiscoveryRequestItem {
  id: number
  discovery_request_id: number
  matter_id: number
  request_number: number
  source_text: string
  status: DiscoveryRequestStatus
  ingested_by_staff_id: number
  interpretations: string[]
  privileges: { privilege_name: string; text: string }[]
  objections: { objection_name: string; text: string }[]
  client_response_needed: boolean
  response: string | null
}

/** Mirrors DiscoveryUploadResponse in app/schemas/discovery.py */
export interface DiscoveryUploadResponse {
  document: DiscoveryDocument
  item_count: number
  items: DiscoveryRequestItem[]
  warnings: string[]
}

/** Fields that can be updated on a discovery request item */
export interface DiscoveryRequestItemUpdatePayload {
  source_text?: string
  client_response_needed?: boolean
  interpretations?: string[]
  privileges?: { privilege_name: string; text: string }[]
  objections?: { objection_name: string; text: string }[]
  response?: string | null
}

export interface StandardPrivilege {
  id: number
  slug: string
  text: string
}

export interface StandardObjection {
  id: number
  slug: string
  applies_to: string[]
  text: string
}
