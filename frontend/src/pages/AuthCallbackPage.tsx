import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { supabase } from '../lib/supabaseClient'
import { getMe } from '../lib/api'

/**
 * Handles the OAuth redirect from Supabase after Google sign-in.
 *
 * Flow:
 *  1. Supabase exchanges the auth code in the URL hash for a session.
 *  2. We call GET /api/v1/auth/me to check if this user already has a role.
 *  3a. Role exists → /app/dashboard
 *  3b. No role yet (new staff first login) → /onboarding
 *  3c. Any error → /access-denied
 */
export default function AuthCallbackPage() {
  const navigate = useNavigate()
  const ran = useRef(false)

  useEffect(() => {
    if (ran.current) return
    ran.current = true

    async function handleCallback() {
      // Exchange the auth code / hash fragment for a session
      const { error: sessionError } = await supabase.auth.getSession()
      if (sessionError) {
        navigate('/access-denied', { replace: true })
        return
      }

      // Supabase JS automatically handles the fragment; wait a tick for the
      // onAuthStateChange listener in AuthContext to pick up the new session
      // before we query the backend.
      await new Promise(r => setTimeout(r, 300))

      try {
        const profile = await getMe()
        if (profile) {
          navigate('/app/dashboard', { replace: true })
        } else {
          // Authenticated but no role record — needs staff correlation
          navigate('/onboarding', { replace: true })
        }
      } catch {
        navigate('/access-denied', { replace: true })
      }
    }

    handleCallback()
  }, [navigate])

  return (
    <div className="min-h-screen bg-off-white flex flex-col items-center justify-center gap-4">
      <div className="animate-spin w-10 h-10 border-4 border-navy/20 border-t-navy rounded-full" />
      <p className="text-text-secondary text-sm">Completing sign-in…</p>
    </div>
  )
}
