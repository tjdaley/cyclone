import { Navigate, Outlet } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

/**
 * Wraps routes that require an authenticated session with a staff/admin role.
 * Clients are directed to a separate portal (future work).
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

  // Clients have a separate portal (not yet implemented); deny for now
  if (profile.role === 'client') {
    return <Navigate to="/access-denied" replace />
  }

  return <Outlet />
}
