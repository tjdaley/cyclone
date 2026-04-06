import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { correlateStaff } from '../lib/api'

type State = 'pending' | 'success' | 'not_found' | 'error'

/**
 * First-login page for staff members.
 *
 * Calls POST /api/v1/auth/correlate-staff which matches the authenticated
 * user's email against the `auth_email` column in the staff table and creates
 * a user_roles record if a match is found.
 */
export default function OnboardingPage() {
  const { refreshProfile, signOut } = useAuth()
  const navigate = useNavigate()
  const ran = useRef(false)
  const [state, setState] = useState<State>('pending')
  const [detail, setDetail] = useState<string>('')

  useEffect(() => {
    if (ran.current) return
    ran.current = true

    async function correlate() {
      try {
        await correlateStaff()
        await refreshProfile()
        setState('success')
        setTimeout(() => navigate('/app/dashboard', { replace: true }), 1500)
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        if (msg.toLowerCase().includes('no staff record') || msg.includes('404')) {
          setState('not_found')
        } else {
          setDetail(msg)
          setState('error')
        }
      }
    }

    correlate()
  }, [navigate, refreshProfile])

  async function handleSignOut() {
    await signOut()
    navigate('/login', { replace: true })
  }

  return (
    <div className="min-h-screen bg-off-white flex flex-col items-center justify-center px-6">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <span className="font-display text-4xl text-navy tracking-tight">Cyclone</span>
        </div>

        <div className="card p-8 text-center">
          {state === 'pending' && (
            <>
              <div className="mx-auto mb-4 animate-spin w-10 h-10 border-4 border-navy/20 border-t-navy rounded-full" />
              <h1 className="font-display text-xl text-navy mb-2">Setting up your account…</h1>
              <p className="text-text-secondary text-sm">Linking your Google account to your firm profile.</p>
            </>
          )}

          {state === 'success' && (
            <>
              <div className="mx-auto mb-4 w-12 h-12 rounded-full bg-green-100 flex items-center justify-center">
                <svg className="w-6 h-6 text-green-600" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <h1 className="font-display text-xl text-navy mb-2">You're all set!</h1>
              <p className="text-text-secondary text-sm">Redirecting to your dashboard…</p>
            </>
          )}

          {state === 'not_found' && (
            <>
              <div className="mx-auto mb-4 w-12 h-12 rounded-full bg-amber-100 flex items-center justify-center">
                <svg className="w-6 h-6 text-amber-600" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round"
                    d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
                </svg>
              </div>
              <h1 className="font-display text-xl text-navy mb-2">No firm account found</h1>
              <p className="text-text-secondary text-sm mb-6">
                We couldn't find a staff record for your email address. Please contact your firm
                administrator to ensure your work email has been added to the system.
              </p>
              <button onClick={handleSignOut} className="btn-primary text-sm">
                Sign out and try again
              </button>
            </>
          )}

          {state === 'error' && (
            <>
              <div className="mx-auto mb-4 w-12 h-12 rounded-full bg-red-100 flex items-center justify-center">
                <svg className="w-6 h-6 text-red-600" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round"
                    d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
                </svg>
              </div>
              <h1 className="font-display text-xl text-navy mb-2">Something went wrong</h1>
              {detail && (
                <p className="text-text-secondary text-xs mb-4 font-mono bg-gray-50 rounded p-2 break-all">
                  {detail}
                </p>
              )}
              <p className="text-text-secondary text-sm mb-6">
                An unexpected error occurred during account setup. Please try again or contact support.
              </p>
              <div className="flex flex-col gap-3">
                <button onClick={() => { ran.current = false; setState('pending') }} className="btn-primary text-sm">
                  Try again
                </button>
                <button onClick={handleSignOut} className="btn-secondary text-sm">
                  Sign out
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
