import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export default function AccessDeniedPage() {
  const { signOut } = useAuth()
  const navigate = useNavigate()

  async function handleSignOut() {
    await signOut()
    navigate('/login', { replace: true })
  }

  return (
    <div className="min-h-screen bg-off-white flex flex-col items-center justify-center px-6">
      <div className="w-full max-w-sm text-center">
        <span className="font-display text-4xl text-navy tracking-tight">Cyclone</span>

        <div className="card p-8 mt-8">
          <div className="mx-auto mb-4 w-14 h-14 rounded-full bg-red-50 flex items-center justify-center">
            <svg className="w-7 h-7 text-red-500" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round"
                d="M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z" />
            </svg>
          </div>

          <h1 className="font-display text-2xl text-navy mb-3">Access denied</h1>
          <p className="text-text-secondary text-sm leading-relaxed mb-8">
            Your account does not have access to this firm's Cyclone workspace. If you believe this
            is an error, please contact your firm administrator.
          </p>

          <button onClick={handleSignOut} className="btn-primary w-full text-sm">
            Sign out
          </button>
        </div>
      </div>
    </div>
  )
}
