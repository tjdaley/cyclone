import { Link } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

// ── Icon helpers (inline SVG to keep the bundle lean) ────────────────────────

function IconBolt() {
  return (
    <svg className="w-7 h-7" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
    </svg>
  )
}

function IconUsers() {
  return (
    <svg className="w-7 h-7" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M12 6.375a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zm8.25 2.25a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z" />
    </svg>
  )
}

function IconDocument() {
  return (
    <svg className="w-7 h-7" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
    </svg>
  )
}

function IconScale() {
  return (
    <svg className="w-7 h-7" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M12 3v17.25m0 0c-1.472 0-2.882.265-4.185.75M12 20.25c1.472 0 2.882.265 4.185.75M18.75 4.97A48.416 48.416 0 0012 4.5c-2.291 0-4.545.16-6.75.47m13.5 0c1.01.143 2.01.317 3 .52m-3-.52l2.62 10.726c.122.499-.106 1.028-.589 1.202a5.988 5.988 0 01-2.031.352 5.988 5.988 0 01-2.031-.352c-.483-.174-.711-.703-.59-1.202L18.75 4.97zm-16.5.52c.99-.203 1.99-.377 3-.52m0 0l2.62 10.726c.122.499-.106 1.028-.589 1.202a5.989 5.989 0 01-2.031.352 5.989 5.989 0 01-2.031-.352c-.483-.174-.711-.703-.59-1.202L5.25 5.49z" />
    </svg>
  )
}

function IconChat() {
  return (
    <svg className="w-7 h-7" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M7.5 8.25h9m-9 3H12m-9.75 1.51c0 1.6 1.123 2.994 2.707 3.227 1.129.166 2.27.293 3.423.379.35.026.67.21.865.501L12 21l2.755-4.133a1.14 1.14 0 01.865-.501 48.172 48.172 0 003.423-.379c1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0012 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018z" />
    </svg>
  )
}

// ── Feature card ──────────────────────────────────────────────────────────────

interface FeatureProps {
  icon: React.ReactNode
  title: string
  description: string
}

function Feature({ icon, title, description }: FeatureProps) {
  return (
    <div className="flex gap-4">
      <div className="flex-shrink-0 w-14 h-14 rounded-xl bg-navy/10 text-navy
                      flex items-center justify-center">
        {icon}
      </div>
      <div>
        <h3 className="font-semibold text-navy text-lg mb-1">{title}</h3>
        <p className="text-text-secondary text-sm leading-relaxed">{description}</p>
      </div>
    </div>
  )
}

// ── Step card ─────────────────────────────────────────────────────────────────

function Step({ number, title, description }: { number: string; title: string; description: string }) {
  return (
    <div className="text-center">
      <div className="mx-auto mb-4 w-12 h-12 rounded-full bg-gold flex items-center
                      justify-center text-navy font-display font-bold text-xl">
        {number}
      </div>
      <h3 className="font-semibold text-navy text-lg mb-2">{title}</h3>
      <p className="text-text-secondary text-sm leading-relaxed max-w-xs mx-auto">{description}</p>
    </div>
  )
}

// ── Main page component ───────────────────────────────────────────────────────

export default function LandingPage() {
  const { session } = useAuth()

  return (
    <div className="min-h-screen bg-white font-sans">

      {/* ── Navigation ── */}
      <header className="fixed inset-x-0 top-0 z-50 bg-white/95 backdrop-blur border-b border-border">
        <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between">
          <span className="font-display text-2xl text-navy tracking-tight">Cyclone</span>
          <nav className="flex items-center gap-6">
            <a href="#features" className="text-sm text-text-secondary hover:text-navy transition-colors">
              Features
            </a>
            <a href="#how-it-works" className="text-sm text-text-secondary hover:text-navy transition-colors">
              How it works
            </a>
            {session
              ? <Link to="/app/dashboard" className="btn-primary text-sm">Go to app</Link>
              : <Link to="/login"         className="btn-primary text-sm">Sign in</Link>
            }
          </nav>
        </div>
      </header>

      {/* ── Hero ── */}
      <section className="pt-32 pb-24 px-6 bg-gradient-to-br from-navy via-navy-light to-[#005a9e]">
        <div className="max-w-4xl mx-auto text-center">
          <div className="inline-block px-3 py-1 rounded-full bg-gold/20 text-gold text-xs
                          font-semibold tracking-widest uppercase mb-6">
            Legal Practice Management
          </div>
          <h1 className="font-display text-5xl md:text-6xl text-white leading-tight mb-6">
            The practice platform your clients will actually use.
          </h1>
          <p className="text-blue-100 text-xl leading-relaxed mb-10 max-w-2xl mx-auto">
            Cyclone brings together AI-powered billing, collaborative discovery, and a clean
            client portal — all in one secure, attorney-crafted platform.
          </p>
          <div className="flex flex-wrap gap-4 justify-center">
            <Link to="/login" className="btn-gold px-8 py-3 text-base">
              Sign in to your firm
            </Link>
            <a href="#features" className="btn-secondary border-white/40 text-white
                                           hover:bg-white/10 hover:text-white px-8 py-3 text-base">
              See what's inside
            </a>
          </div>
        </div>
      </section>

      {/* ── Social proof strip ── */}
      <div className="bg-off-white border-y border-border py-6 px-6">
        <p className="text-center text-sm text-text-secondary">
          Built for family law, probate, estate planning, and civil litigation
          &nbsp;·&nbsp; HIPAA-aware &nbsp;·&nbsp; Data stays in your Supabase tenant
        </p>
      </div>

      {/* ── Features ── */}
      <section id="features" className="py-24 px-6">
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-16">
            <h2 className="font-display text-4xl text-navy mb-4">
              Everything your firm needs, nothing it doesn't
            </h2>
            <p className="text-text-secondary text-lg max-w-2xl mx-auto">
              Cyclone is designed around how attorneys actually work — not around how software vendors
              think they should.
            </p>
          </div>

          <div className="grid md:grid-cols-2 gap-10">
            <Feature
              icon={<IconBolt />}
              title="Natural language billing"
              description={`Say "bill .25 to the Smith divorce for drafting the initial petition" and Cyclone does the rest. Our AI parses, classifies, and rates the entry for your review before committing a single record.`}
            />
            <Feature
              icon={<IconUsers />}
              title="Client portal"
              description="Clients see exactly what you want them to see: upcoming events, billing statements, and their discovery tasks. Pay directly through Stripe-hosted links — no separate payment system needed."
            />
            <Feature
              icon={<IconDocument />}
              title="Collaborative discovery"
              description="Upload opposing counsel's interrogatories, RFAs, and RFPs. Cyclone segments and classifies each request. Clients draft their own responses; you review, object, and finalize — all in one workflow."
            />
            <Feature
              icon={<IconScale />}
              title="Matter & billing management"
              description="Multi-timekeeper matters, per-matter rate cards with individual overrides, pro-bono flagging, and billing splits for appointed matters. Designed for the complexity of real family law practice."
            />
            <Feature
              icon={<IconChat />}
              title="Built-in conflict checking"
              description="Fuzzy trigram matching against all existing clients and opposing parties before a new matter is opened. Results surface to the responsible attorney for review — never disclosed to the prospective client."
            />
            <Feature
              icon={<IconBolt />}
              title="Multi-vendor AI"
              description="Swap between Anthropic, Gemini, OpenAI, Groq, or DeepSeek with a single environment variable. Use a fast model for billing parse and a powerful model for discovery ingestion — independently configurable."
            />
          </div>
        </div>
      </section>

      {/* ── How it works ── */}
      <section id="how-it-works" className="py-24 px-6 bg-off-white">
        <div className="max-w-4xl mx-auto">
          <div className="text-center mb-16">
            <h2 className="font-display text-4xl text-navy mb-4">How it works</h2>
            <p className="text-text-secondary text-lg">Three steps from firm setup to running matters.</p>
          </div>

          <div className="grid md:grid-cols-3 gap-12">
            <Step
              number="1"
              title="Firm setup"
              description="Add your offices and staff. Each attorney or paralegal receives an invitation link. Their first sign-in automatically links their account — no separate password system."
            />
            <Step
              number="2"
              title="Open matters"
              description="Run a conflict check, execute a fee agreement, and configure rate cards and retainer thresholds. Billing and discovery can start the same day."
            />
            <Step
              number="3"
              title="Work and bill"
              description="Enter time naturally or with the form. Close billing cycles to generate PDF statements. Clients pay through Stripe. Audit log captures everything."
            />
          </div>
        </div>
      </section>

      {/* ── CTA ── */}
      <section className="py-24 px-6 bg-navy text-center">
        <h2 className="font-display text-4xl text-white mb-4">
          Ready to streamline your practice?
        </h2>
        <p className="text-blue-100 text-lg mb-8 max-w-xl mx-auto">
          Sign in with your firm's Google account. Your administrator has already set up your access.
        </p>
        <Link to="/login" className="btn-gold px-10 py-3 text-base">
          Sign in to Cyclone
        </Link>
      </section>

      {/* ── Footer ── */}
      <footer className="bg-[#001e36] py-10 px-6">
        <div className="max-w-6xl mx-auto flex flex-col md:flex-row items-center justify-between gap-4">
          <span className="font-display text-xl text-white/80">Cyclone</span>
          <p className="text-xs text-white/40">
            &copy; {new Date().getFullYear()} &mdash; Legal Practice Management Platform
          </p>
          <div className="flex gap-6 text-xs text-white/40">
            <Link to="/privacy" className="hover:text-white/70 transition-colors">Privacy Policy</Link>
            <Link to="/terms" className="hover:text-white/70 transition-colors">Terms of Use</Link>
            <a href="/api/health" className="hover:text-white/70 transition-colors">API Status</a>
          </div>
        </div>
      </footer>

    </div>
  )
}
