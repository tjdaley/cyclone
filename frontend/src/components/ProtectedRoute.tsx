import { Navigate, Outlet } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

/**
 * Wraps routes that require an authenticated session with a staff/admin role.
 *
 * A client has their own portal with its own shell, its own wording and its own
 * API prefix, so this sends them there rather than denying them. `/access-denied`
 * now means what it says: a signed-in account with no place to go.
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

  // Authenticated but no role yet — needs staff correlation
  if (!profile) {
    return <Navigate to="/onboarding" replace />
  }

  // Clients belong in the portal. Checked on `roles` rather than the primary
  // role: somebody who is both a client and staff of the firm is staff here.
  const roles = profile.roles ?? [profile.role]
  if (roles.includes('client') && !roles.some(r => r !== 'client')) {
    return <Navigate to="/portal" replace />
  }

  return <Outlet />
}
