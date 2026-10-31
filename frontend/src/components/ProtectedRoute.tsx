import { Navigate, Outlet } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

/**
 * The roles the staff app is for. Nothing else gets in.
 *
 * This used to admit any role that was not `client`, which was true only while
 * every other role was staff. A financial professional (052) is not, and staff
 * routes are not matter-scoped: letting one in here would show an expert
 * retained on one case every matter in the firm.
 */
const STAFF_ROLES = ['attorney', 'paralegal', 'admin']

/**
 * Wraps routes that require an authenticated session with a staff role.
 *
 * A client has their own portal and a financial professional their own shell,
 * each with its own API prefix, so this sends them there rather than denying
 * them. `/access-denied` means what it says: a signed-in account with no place
 * to go.
 */
export default function ProtectedRoute() {
  const { session, profile, loading } = useAuth()

  if (loading) {
    return (
      <div className="min-h-screen bg-off-white flex items-center justify-center">
        <div className="animate-spin w-8 h-8 border-4 border-navy/20 border-t-navy rounded-full" />
      </div>
    )
  }

  if (!session) {
    return <Navigate to="/login" replace />
  }

  // Authenticated but no role yet — needs correlation
  if (!profile) {
    return <Navigate to="/onboarding" replace />
  }

  // Checked on `roles` rather than the primary role: somebody who is both a
  // client and staff of the firm is staff here.
  const roles = profile.roles ?? [profile.role]
  if (roles.some(r => STAFF_ROLES.includes(r))) return <Outlet />
  if (roles.includes('client')) return <Navigate to="/portal" replace />
  if (roles.includes('financial_professional')) return <Navigate to="/guest" replace />
  return <Navigate to="/access-denied" replace />
}
