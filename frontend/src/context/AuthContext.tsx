import { createContext, useContext, useEffect, useState, ReactNode } from 'react'
import { Session, User } from '@supabase/supabase-js'
import { supabase } from '../lib/supabaseClient'
import { getMe, UserProfile } from '../lib/api'

interface AuthState {
  session:     Session | null
  user:        User    | null
  profile:     UserProfile | null
  /** True while the initial session and profile are loading */
  loading:     boolean
  /** Set to true after a successful correlate-staff call; triggers re-fetch of profile */
  refreshProfile: () => Promise<void>
  signOut:     () => Promise<void>
}

const AuthContext = createContext<AuthState | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [user,    setUser]    = useState<User | null>(null)
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [loading, setLoading] = useState(true)

  const fetchProfile = async () => {
    try {
      const p = await getMe()
      setProfile(p)
    } catch {
      // 404 (no role yet) is expected on first login — profile stays null
      setProfile(null)
    }
  }

  useEffect(() => {
    // Initial session load
    supabase.auth.getSession().then(({ data: { session } }) => {
      setSession(session)
      setUser(session?.user ?? null)
      if (session) {
        fetchProfile().finally(() => setLoading(false))
      } else {
        setLoading(false)
      }
    })

    // Listen for auth state changes (login, logout, token refresh)
    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      (_event, session) => {
        setSession(session)
        setUser(session?.user ?? null)
        if (session) {
          fetchProfile()
        } else {
          setProfile(null)
        }
      }
    )

    return () => subscription.unsubscribe()
  }, [])

  // Set body density attribute based on role
  useEffect(() => {
    if (profile) {
      document.body.dataset.density =
        profile.role === 'client' ? 'relaxed' : 'compact'
    } else {
      delete document.body.dataset.density
    }
  }, [profile])

  const refreshProfile = async () => {
    await fetchProfile()
  }

  const signOut = async () => {
    await supabase.auth.signOut()
    setProfile(null)
  }

  return (
    <AuthContext.Provider value={{ session, user, profile, loading, refreshProfile, signOut }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
