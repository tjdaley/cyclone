/** Returned by GET /api/v1/auth/me */
export interface UserProfile {
  role: string
  staff_id: number | null
  client_id: number | null
  [key: string]: unknown
}
