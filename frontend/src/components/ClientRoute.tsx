import { Navigate, Outlet } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

/**
 * Wraps the client portal.
 *
 * The mirror of `ProtectedRoute`: staff are sent to the staff app rather than
 * shown a client's view of a case. Two surfaces, each written for one audience
 * — a page that has to be correct for both ends up written for neither.
 */
export default function ClientRoute() {
  const { session, profile, loading } = useAuth()

  if (loading) {
    return (
      <div className="min-h-screen bg-off-white flex items-center justify-center">
        <div className="animate-spin w-8 h-8 border-4 border-navy/20 border-t-navy rounded-full" />
      </div>
    )
  }
  if (!session) return <Navigate to="/login" replace />
  if (!profile) return <Navigate to="/onboarding" replace />

  const roles = profile.roles ?? [profile.role]
  if (!roles.includes('client')) return <Navigate to="/app/dashboard" replace />

  return <Outlet />
}
