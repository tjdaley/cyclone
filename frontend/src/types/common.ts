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
