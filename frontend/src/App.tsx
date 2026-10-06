import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider } from './context/AuthContext'
import ProtectedRoute from './components/ProtectedRoute'
import AppShell from './components/AppShell'

import LandingPage      from './pages/LandingPage'
import LoginPage        from './pages/LoginPage'
import AuthCallbackPage from './pages/AuthCallbackPage'
import OnboardingPage   from './pages/OnboardingPage'
import AccessDeniedPage    from './pages/AccessDeniedPage'
import PrivacyPolicyPage  from './pages/PrivacyPolicyPage'
import TermsOfUsePage     from './pages/TermsOfUsePage'
import UserGuidePage      from './pages/UserGuidePage'

import DashboardPage  from './pages/app/DashboardPage'
import BillingPage    from './pages/app/BillingPage'
import MattersPage    from './pages/app/MattersPage'
import MatterDetailPage from './pages/app/MatterDetailPage'
import MatterFinancialsPage from './pages/app/MatterFinancialsPage'
import ClientsPage    from './pages/app/ClientsPage'
import DiscoveryPage  from './pages/app/DiscoveryPage'
import PleadingsPage    from './pages/app/PleadingsPage'
import LeadsListPage    from './pages/app/LeadsListPage'
import LeadDetailPage   from './pages/app/LeadDetailPage'
import ProfilePage      from './pages/app/ProfilePage'
import AdminPage        from './pages/app/AdminPage'
import CostsPage        from './pages/app/CostsPage'

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          {/* Public */}
          <Route path="/"              element={<LandingPage />} />
          <Route path="/login"         element={<LoginPage />} />
          <Route path="/auth/callback" element={<AuthCallbackPage />} />
          <Route path="/onboarding"    element={<OnboardingPage />} />
          <Route path="/access-denied"   element={<AccessDeniedPage />} />
          <Route path="/privacy"         element={<PrivacyPolicyPage />} />
          <Route path="/terms"           element={<TermsOfUsePage />} />
          {/* The user guide, rendered from docs/user-guide. Public, like the
              two pages above: the markdown ships as static assets, so gating
              the route would not gate the content. */}
          <Route path="/guide"           element={<UserGuidePage />} />
          <Route path="/guide/:slug"     element={<UserGuidePage />} />

          {/* Protected — staff portal */}
          <Route element={<ProtectedRoute />}>
            <Route element={<AppShell />}>
              <Route path="/app"           element={<Navigate to="/app/dashboard" replace />} />
              <Route path="/app/dashboard" element={<DashboardPage />} />
              <Route path="/app/billing"   element={<BillingPage />} />
              <Route path="/app/matters"   element={<MattersPage />} />
              <Route path="/app/matters/:matterId" element={<MatterDetailPage />} />
              <Route path="/app/matters/:matterId/financials" element={<MatterFinancialsPage />} />
              <Route path="/app/clients"   element={<ClientsPage />} />
              <Route path="/app/discovery" element={<DiscoveryPage />} />
              <Route path="/app/pleadings" element={<PleadingsPage />} />
              <Route path="/app/leads"                  element={<LeadsListPage />} />
              <Route path="/app/leads/:sessionUuid"     element={<LeadDetailPage />} />
              <Route path="/app/profile"   element={<ProfilePage />} />
              <Route path="/app/admin"     element={<AdminPage />} />
              <Route path="/app/costs"     element={<CostsPage />} />
            </Route>
          </Route>

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
