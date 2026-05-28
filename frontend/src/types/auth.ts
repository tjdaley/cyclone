/** Returned by GET /api/v1/auth/me.
 *
 * A user may hold multiple roles (attorney + admin, etc.):
 * - `roles` carries the full set, sorted alphabetically.
 * - `role`  is the highest-privilege role (admin > attorney > paralegal > client),
 *   preserved for legacy single-role consumers (nav filter, role display,
 *   density selection).
 */
export interface UserProfile {
  role: string
  roles: string[]
  staff_id: number | null
  client_id: number | null
  [key: string]: unknown
}
