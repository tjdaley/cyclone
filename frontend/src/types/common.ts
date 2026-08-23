/**
 * Public configuration served by GET /api/config (unauthenticated).
 * `id` identifies which server instance answered the request.
 */
export interface AppConfig {
  id: string
  firm_name: string
  version: string
  stripe_publishable_key: string
  time_increment_options: number[]
  referral_types: string[]
}

/**
 * Shared name structure used by clients and staff.
 * Mirrors backend FullName from db/models/staff.py.
 */
export interface FullName {
  courtesy_title: string | null
  first_name: string
  middle_name: string | null
  last_name: string
  suffix: string | null
}
