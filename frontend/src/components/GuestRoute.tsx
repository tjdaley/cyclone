import { Navigate, Outlet } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

/**
 * Wraps the financial-professional surface (052).
 *
 * The mirror of `ClientRoute`. Only a financial professional gets in; anyone
 * else is sent to their own place. Hiding a control is not a protection — the
 * API under /api/v1/guest is what enforces the scope — but a page written for
 * one audience should not be reachable by another.
 */
export default function GuestRoute() {
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
  if (!roles.includes('financial_professional')) return <Navigate to="/app/dashboard" replace />

  return <Outlet />
}
