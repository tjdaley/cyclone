import { Link } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

/**
 * The public landing page.
 *
 * **Every sentence here is a claim about the product, so it claims only what is
 * built.** The previous version promised Stripe payments, PDF bills, built-in
 * conflict checking and client-drafted discovery responses — none of which
 * existed. A prospective firm that signs in and cannot find what the front page
 * promised has learned that the front page is not to be believed, which is the
 * worst thing a product whose whole argument is "every figure is defensible"
 * can teach anybody. When a feature ships, add it here; not before.
 *
 * The logo marks in /brand are cut from the link-share image with exact
 * colour-to-alpha edges. They are drawn for LIGHT backgrounds: the swirl's
 * dark half and the "jdbot" wordmark disappear on navy, so the one dark band
 * on this page carries no logo.
 *
 * The illustration in the hero is sample data, not a client's. Keep it that
 * way — this page is public.
 */

// ── Icons (inline SVG, 24px grid, stroke) ───────────────────────────────────

function Icon({ d }: { d: string }) {
  return (
    <svg className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth={1.6} viewBox="0 0 24 24"
      aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d={d} />
    </svg>
  )
}

const ICON = {
  // a stack of statements
  statements: 'M6 3.75h9l3 3V20.25H6zM15 3.75v3h3M9 11.25h6M9 14.25h6M9 17.25h3',
  // a grid with one empty cell
  grid: 'M3.75 4.5h16.5v15H3.75zM3.75 9.5h16.5M3.75 14.5h16.5M9.25 4.5v15M14.75 4.5v15',
  // a gavel-free exhibit: a document with a stamp
  exhibit: 'M5.25 3.75h10.5l3 3v13.5H5.25zM8.25 9h7.5M8.25 12h7.5M8.25 15h4.5M14.25 15.75l1.5 1.5 3-3',
  // a person with a key: outside professional
  expert: 'M9 11.25a3.375 3.375 0 100-6.75 3.375 3.375 0 000 6.75zM3 19.5a6 6 0 0110.6-3.86M16.5 14.25a1.875 1.875 0 113.75 0 1.875 1.875 0 01-3.75 0zM18.375 16.125V20.25M18.375 18.375h1.5',
  // two speech bubbles: client involvement
  client: 'M7.5 9.75h6M7.5 12.75h3.75M3 6.75A2.25 2.25 0 015.25 4.5h9A2.25 2.25 0 0116.5 6.75v6A2.25 2.25 0 0114.25 15H9l-3.75 3v-3A2.25 2.25 0 013 12.75zM16.5 9h2.25A2.25 2.25 0 0121 11.25v5.25a2.25 2.25 0 01-2.25 2.25v2.25l-3-2.25h-3',
  // a briefcase: the practice basics
  practice: 'M3.75 8.25h16.5v11.25H3.75zM9 8.25V6a1.5 1.5 0 011.5-1.5h3A1.5 1.5 0 0115 6v2.25M3.75 13.5h16.5',
}

// ── The hero illustration: a compliance grid, sample data ──────────────────

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
// Apr and Aug: the production skipped them. A statement is filed under the
// month it CLOSES in (as on the real compliance grid), so the April cell is the
// period 4 March - 3 April; the findings text below must agree with that.
const MISSING = new Set([3, 7])

function ComplianceIllustration() {
  let stamp = 412
  return (
    <figure role="img"
      aria-label="Illustration: a year of statements for one checking account, with April and August missing from the production, and a savings account the statements mention that was never produced."
      className="relative">
      <div aria-hidden="true"
        className="rounded-2xl border border-border bg-white shadow-xl shadow-brand-ink/10 p-5 sm:p-6">
        <div className="flex items-baseline justify-between gap-3">
          <div>
            <p className="text-[11px] font-semibold tracking-widest uppercase text-brand-teal-dark">
              Statements produced · 2025
            </p>
            <p className="font-semibold text-brand-ink mt-0.5">Example Bank · Checking ····4070</p>
          </div>
          <span className="shrink-0 text-xs rounded-full px-2 py-0.5 bg-green-100 text-green-800">
            10 of 12 reconciled
          </span>
        </div>

        <div className="grid grid-cols-4 sm:grid-cols-6 gap-1.5 mt-4">
          {MONTHS.map((m, i) => {
            const missing = MISSING.has(i)
            const bates = missing ? null : `KF ${String(stamp).padStart(6, '0')}`
            if (!missing) stamp += 3
            return (
              <div key={m}
                className={`rounded-md px-1.5 py-2 text-center ${missing
                  ? 'border border-dashed border-red-400 bg-red-50'
                  : 'border border-brand-teal/20 bg-brand-teal/[0.06]'}`}>
                <p className={`text-[11px] font-semibold ${missing ? 'text-red-700' : 'text-brand-ink'}`}>{m}</p>
                <p className={`font-mono text-[9px] mt-0.5 truncate ${missing ? 'text-red-600' : 'text-text-secondary'}`}>
                  {missing ? 'missing' : bates}
                </p>
              </div>
            )
          })}
        </div>

        <div className="mt-4 space-y-2 text-[13px]">
          <div className="flex gap-2 rounded-lg bg-off-white px-3 py-2">
            <span className="mt-1 w-1.5 h-1.5 shrink-0 rounded-full bg-red-500" />
            <p className="text-text-primary">
              <span className="font-semibold">Not produced:</span> 4 March through 3 April, and
              2 July through 1 August — 62 days.
            </p>
          </div>
          <div className="flex gap-2 rounded-lg bg-off-white px-3 py-2">
            <span className="mt-1 w-1.5 h-1.5 shrink-0 rounded-full bg-amber-500" />
            <p className="text-text-primary">
              <span className="font-semibold">Referenced, never disclosed:</span> Savings ····9260 —
              six transfers out.
            </p>
          </div>
        </div>
      </div>
    </figure>
  )
}

// ── Building blocks ─────────────────────────────────────────────────────────

function Pillar({ icon, kicker, title, children }: {
  icon: string; kicker: string; title: string; children: React.ReactNode
}) {
  return (
    <div className="group rounded-2xl border border-border bg-white p-6 sm:p-7 transition-shadow
                    hover:shadow-lg hover:shadow-brand-ink/5">
      <div className="flex items-center gap-3 mb-4">
        <span className="w-11 h-11 rounded-xl flex items-center justify-center
                         bg-brand-teal/10 text-brand-teal-dark ring-1 ring-brand-teal/20">
          <Icon d={icon} />
        </span>
        <span className="text-[11px] font-semibold tracking-widest uppercase text-brand-teal-dark">
          {kicker}
        </span>
      </div>
      <h3 className="font-display text-2xl text-brand-ink leading-snug mb-3">{title}</h3>
      <div className="text-text-secondary leading-relaxed space-y-3">{children}</div>
    </div>
  )
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <li className="relative pl-14">
      <span className="absolute left-0 top-0 w-10 h-10 rounded-full flex items-center justify-center
                       bg-brand-ink text-white font-display text-lg">
        {n}
      </span>
      <h3 className="font-semibold text-brand-ink text-lg mb-1.5">{title}</h3>
      <p className="text-text-secondary leading-relaxed">{children}</p>
    </li>
  )
}

function Principle({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border-l-2 border-brand-cyan pl-5">
      <h3 className="font-semibold text-white text-lg mb-1.5">{title}</h3>
      <p className="text-white/70 leading-relaxed">{children}</p>
    </div>
  )
}

// ── Page ────────────────────────────────────────────────────────────────────

export default function LandingPage() {
  const { session } = useAuth()
  const signIn = session
    ? <Link to="/app/dashboard" className="btn-primary bg-brand-ink hover:bg-navy">Go to Cyclone</Link>
    : <Link to="/login" className="btn-primary bg-brand-ink hover:bg-navy">Sign in</Link>

  return (
    <div className="min-h-screen bg-white font-sans text-text-primary">

      {/* ── Header ── */}
      <header className="fixed inset-x-0 top-0 z-50 bg-white/90 backdrop-blur border-b border-border/70">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between gap-4">
          <Link to="/" className="flex items-center gap-2.5 min-w-0" aria-label="Cyclone home">
            <img src="/brand/cyclone-mark.png" alt="" className="h-8 w-auto" />
            <span className="font-display text-2xl text-brand-ink tracking-tight">Cyclone</span>
            <span className="hidden sm:inline text-xs text-text-secondary mt-1.5">by jdbot.us</span>
          </Link>
          <nav className="flex items-center gap-5">
            <a href="#what-it-does"
              className="hidden md:inline text-sm text-text-secondary hover:text-brand-ink transition-colors">
              What it does
            </a>
            <a href="#how-it-works"
              className="hidden md:inline text-sm text-text-secondary hover:text-brand-ink transition-colors">
              How it works
            </a>
            <a href="#ai-costs"
              className="hidden md:inline text-sm text-text-secondary hover:text-brand-ink transition-colors">
              AI costs
            </a>
            <a href="#built-for-court"
              className="hidden md:inline text-sm text-text-secondary hover:text-brand-ink transition-colors">
              Built for court
            </a>
            {signIn}
          </nav>
        </div>
      </header>

      {/* ── Hero ── */}
      <section className="relative overflow-hidden pt-28 sm:pt-32 pb-20 sm:pb-24 px-4 sm:px-6
        bg-[radial-gradient(ellipse_at_top_right,rgba(0,188,204,0.14),transparent_55%),radial-gradient(ellipse_at_bottom_left,rgba(5,24,41,0.06),transparent_50%)]">
        <div className="max-w-6xl mx-auto grid lg:grid-cols-[1.05fr_1fr] gap-12 lg:gap-16 items-center">
          <div>
            <p className="inline-flex items-center gap-2 rounded-full border border-brand-teal/30
                          bg-white/70 px-3 py-1 text-xs font-semibold tracking-widest uppercase
                          text-brand-teal-dark">
              <span className="w-1.5 h-1.5 rounded-full bg-brand-cyan" />
              Financial discovery for family law
            </p>
            <h1 className="font-display text-4xl sm:text-5xl lg:text-6xl text-brand-ink leading-[1.08] mt-6">
              Every account.
              <br />Every month.
              <br /><span className="text-brand-teal">Every gap.</span>
            </h1>
            <p className="text-lg sm:text-xl text-text-secondary leading-relaxed mt-6 max-w-xl">
              Cyclone reads the bank, brokerage and card statements in a production, checks each one
              against its own printed balances, and shows you what is missing: the months nobody
              produced, and the accounts the statements mention that nobody disclosed.
            </p>
            <div className="flex flex-wrap gap-3 mt-9">
              <Link to={session ? '/app/dashboard' : '/login'}
                className="btn-primary bg-brand-ink hover:bg-navy px-7 py-3 text-base">
                {session ? 'Go to Cyclone' : 'Sign in to your firm'}
              </Link>
              <a href="#what-it-does" className="btn-secondary border-brand-ink text-brand-ink
                 hover:bg-brand-ink px-7 py-3 text-base">
                See what it does
              </a>
            </div>
          </div>
          <ComplianceIllustration />
        </div>
      </section>

      {/* ── Three facts ── */}
      <div className="border-y border-border bg-off-white">
        <ul className="max-w-6xl mx-auto px-4 sm:px-6 py-5 grid sm:grid-cols-3 gap-3 sm:gap-6 text-sm
                       text-text-secondary text-center">
          <li><span className="font-semibold text-brand-ink">Read to the transaction</span> — not summarized</li>
          <li><span className="font-semibold text-brand-ink">Every figure cites its page</span> — Bates and all</li>
          <li><span className="font-semibold text-brand-ink">Your firm's own instance</span> and database</li>
        </ul>
      </div>

      {/* ── What it does ── */}
      <section id="what-it-does" className="scroll-mt-16 py-20 sm:py-28 px-4 sm:px-6">
        <div className="max-w-6xl mx-auto">
          <div className="max-w-2xl mb-14">
            <h2 className="font-display text-3xl sm:text-4xl text-brand-ink leading-tight">
              From a box of statements to an argument you can file.
            </h2>
            <p className="text-lg text-text-secondary mt-4 leading-relaxed">
              The work that used to take a paralegal a week of spreadsheets — and still left you
              wondering what was missing — done in the time it takes to read the production once.
            </p>
          </div>

          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-5">
            <Pillar icon={ICON.statements} kicker="Discovery" title="Statements, read line by line">
              <p>
                Drop one statement or a stack of them. Cyclone files each against its account,
                extracts every transaction, and checks the arithmetic: opening balance plus every
                line must equal the printed close.
              </p>
              <p>
                A statement that does not tie is held for a person, with the exact difference.
              </p>
            </Pillar>

            <Pillar icon={ICON.grid} kicker="Gap detection" title="What they didn't produce">
              <p>
                A month-by-month grid of what each side has produced, Bates number in every cell.
                Gaps are measured in days and written out as the numbered paragraph of a motion
                to compel.
              </p>
              <p>
                And the accounts the production names but does not contain — transfers to
                unproduced accounts, incoming wires, payments to undisclosed creditors, and money
                moving through Venmo, PayPal and crypto platforms.
              </p>
            </Pillar>

            <Pillar icon={ICON.exhibit} kicker="Trial exhibits" title="Exhibits in minutes, not days">
              <p>
                Any search becomes a Rule 1006 summary: the case caption, the criteria that
                produced it, the documents and Bates ranges behind it, and the verification
                notice — in Word, PDF, Markdown or CSV.
              </p>
              <p>
                The Financial Information Statement and an account inventory come from the same
                records, so they cannot disagree.
              </p>
            </Pillar>

            <Pillar icon={ICON.expert} kicker="Expert access" title="Your CPA, in the same records">
              <p>
                Give a tracing expert or CPA the matter's accounts, transactions and inventory —
                with the client's consent, limited to one matter, expiring on the date you set,
                and revocable on the next click.
              </p>
              <p>
                They work in the same figures you do, and no spreadsheet travels by email.
              </p>
            </Pillar>

            <Pillar icon={ICON.client} kicker="Client portal" title="Clients who help, not just watch">
              <p>
                Clients see their accounts and statements, and they do the part only they can:
                marking the transactions they know about — the separate-property deposit, the
                payment to a lender, the trip that was not a business trip.
              </p>
              <p>
                You choose the tags they may use, review every mark, and lock tagging before a
                hearing.
              </p>
            </Pillar>

            <Pillar icon={ICON.practice} kicker="And the practice" title="The rest of the matter">
              <p>
                Matters opened straight from a filed pleading, with parties, children and counsel
                read off it. Time entered in a sentence. Discovery requests split into numbered
                items, answered, and exported to Word.
              </p>
            </Pillar>
          </div>
        </div>
      </section>

      {/* ── How it works ── */}
      <section id="how-it-works" className="scroll-mt-16 py-20 sm:py-28 px-4 sm:px-6 bg-off-white">
        <div className="max-w-6xl mx-auto grid lg:grid-cols-[1fr_1.2fr] gap-12 lg:gap-20">
          <div>
            <h2 className="font-display text-3xl sm:text-4xl text-brand-ink leading-tight">
              How it works
            </h2>
            <p className="text-lg text-text-secondary mt-4 leading-relaxed max-w-md">
              Three steps, and the middle one is Cyclone's. Nothing you rely on was guessed at
              along the way.
            </p>
          </div>
          <ol className="space-y-10">
            <Step n={1} title="Drop the production">
              PDFs exactly as they were served, however many pages. A production that bundles
              hundreds of pages is split into its documents on a bench built for the job, and the
              original is kept as received.
            </Step>
            <Step n={2} title="Cyclone reads and checks">
              Accounts, periods, balances and every line, each tied to its Bates-stamped page.
              Anything that does not reconcile, or that had to be inferred, is flagged for a
              person to look at.
            </Step>
            <Step n={3} title="Argue from it">
              Search and tag the transactions, export the exhibit, copy the motion-to-compel
              paragraph, and bring in your expert and your client.
            </Step>
          </ol>
        </div>
      </section>

      {/* ── AI costs ──
          The figures are Tom's own measured spend (2026-11), stated as
          experience and not as a quote. They are MODEL spend only — the Costs
          page does not count hosting or storage — so the copy says "AI costs"
          and never implies more. Revisit them if vendor prices move. */}
      <section id="ai-costs" className="scroll-mt-16 py-20 sm:py-28 px-4 sm:px-6">
        <div className="max-w-6xl mx-auto grid lg:grid-cols-[1.1fr_1fr] gap-12 lg:gap-16 items-center">
          <div>
            <p className="text-xs font-semibold tracking-widest uppercase text-brand-teal-dark">AI costs</p>
            <h2 className="font-display text-3xl sm:text-4xl text-brand-ink leading-tight mt-3">
              What is the AI costing? In dollars, by matter.
            </h2>
            <p className="text-lg text-text-secondary mt-4 leading-relaxed">
              AI is sold in tokens, which tells a lawyer nothing about what a case will cost.
              Cyclone prices every call in dollars and adds it up by matter — so you can set what
              the AI cost against what it delivered, and know before you start roughly what a
              case will run.
            </p>
            <ul className="mt-7 space-y-3 text-text-primary">
              {[
                ['In dollars, not tokens.', 'Priced against the vendor’s rate on the day of each call.'],
                ['By matter, task, person and month.', 'The question you are asked, answered the way it is asked.'],
                ['Every call counted.', 'Including attempts that failed or were refused — vendors charge for many of those too.'],
                ['Never guessed.', 'A call with no known price is flagged, and the total says it is a floor.'],
              ].map(([lead, rest]) => (
                <li key={lead} className="flex gap-3">
                  <svg className="w-5 h-5 shrink-0 mt-0.5 text-brand-teal" fill="none" stroke="currentColor"
                    strokeWidth={2.2} viewBox="0 0 24 24" aria-hidden="true">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                  </svg>
                  <span><span className="font-semibold text-brand-ink">{lead}</span>{' '}
                    <span className="text-text-secondary">{rest}</span></span>
                </li>
              ))}
            </ul>
          </div>

          <div className="rounded-2xl border border-border bg-off-white p-6 sm:p-8">
            <p className="text-sm font-semibold text-brand-ink">What we have seen, over the life of a matter</p>
            <div className="grid sm:grid-cols-2 gap-4 mt-5">
              <div className="rounded-xl bg-white border border-border p-5">
                <p className="text-sm text-text-secondary">A few hundred statements</p>
                {/* lining-nums: Playfair draws old-style figures by default,
                    which bob above and below the line — wrong for a price. */}
                <p className="mt-2 font-display text-4xl text-brand-ink lining-nums">
                  <span className="text-xl align-top text-text-secondary mr-1">under</span>$150
                </p>
                <p className="text-xs text-text-secondary mt-2">in AI costs, start to finish</p>
              </div>
              <div className="rounded-xl bg-white border border-border p-5">
                <p className="text-sm text-text-secondary">A much larger case</p>
                <p className="mt-2 font-display text-4xl text-brand-ink lining-nums">
                  <span className="text-xl align-top text-text-secondary mr-1">under</span>$700
                </p>
                <p className="text-xs text-text-secondary mt-2">in AI costs, start to finish</p>
              </div>
            </div>
            <p className="text-xs text-text-secondary mt-5 leading-relaxed">
              Measured in our own use. Not a quote: vendor prices change, and every case is its own.
              Your firm sees its actual figures, matter by matter, from the first upload.
            </p>
          </div>
        </div>
      </section>

      {/* ── Built for court ── */}
      <section id="built-for-court" className="scroll-mt-16 py-20 sm:py-28 px-4 sm:px-6 bg-brand-ink
        bg-[radial-gradient(ellipse_at_top_left,rgba(0,188,204,0.16),transparent_55%)]">
        <div className="max-w-6xl mx-auto">
          <div className="max-w-2xl mb-14">
            <p className="text-xs font-semibold tracking-widest uppercase text-brand-cyan">Built for court</p>
            <h2 className="font-display text-3xl sm:text-4xl text-white leading-tight mt-3">
              Built to be cross-examined.
            </h2>
            <p className="text-lg text-white/70 mt-4 leading-relaxed">
              A figure you cannot defend on the stand is worse than no figure at all. So Cyclone
              is built around four rules.
            </p>
          </div>
          <div className="grid sm:grid-cols-2 gap-x-12 gap-y-10">
            <Principle title="Nothing is invented to make it balance.">
              A statement that does not reconcile is recorded with its exact difference — never
              patched with a plug line nobody can explain.
            </Principle>
            <Principle title="Every figure traces to a page.">
              Bates numbers are read off the stamp, never filled in from the pages around it. An
              unstamped page says so.
            </Principle>
            <Principle title="Every correction has a name on it.">
              A changed amount, an added line or a corrected balance records who changed it, when,
              and why — and the original value stays on the record.
            </Principle>
            <Principle title="The software reads. A person decides.">
              No model files a transaction into a category on its own. Firm-written rules and
              people do, and every assignment says which.
            </Principle>
          </div>
        </div>
      </section>

      {/* ── Sign in ── */}
      <section className="py-20 sm:py-24 px-4 sm:px-6 text-center">
        <img src="/brand/cyclone-mark.png" alt="" className="h-16 w-auto mx-auto" />
        <h2 className="font-display text-3xl sm:text-4xl text-brand-ink mt-6">
          Already a Cyclone firm?
        </h2>
        <p className="text-lg text-text-secondary mt-3 max-w-xl mx-auto">
          Sign in with your firm's Google account. Your administrator has already set up your access.
        </p>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <Link to={session ? '/app/dashboard' : '/login'}
            className="btn-primary bg-brand-ink hover:bg-navy px-8 py-3 text-base">
            {session ? 'Go to Cyclone' : 'Sign in to Cyclone'}
          </Link>
          <Link to="/guide" className="btn-secondary border-brand-ink text-brand-ink hover:bg-brand-ink
             px-8 py-3 text-base">
            Read the user guide
          </Link>
        </div>
      </section>

      {/* ── Footer ── */}
      <footer className="border-t border-border bg-off-white py-10 px-4 sm:px-6">
        <div className="max-w-6xl mx-auto flex flex-col md:flex-row items-center justify-between gap-6">
          <div className="flex flex-col items-center md:items-start gap-2">
            <img src="/brand/jdbot-lockup.png" alt="jdbot.us" className="h-8 w-auto" />
            <p className="text-xs text-text-secondary">
              Cyclone is a jdbot.us product · &copy; {new Date().getFullYear()} by Thomas J. Daley, J.D.
            </p>
          </div>
          <nav className="flex flex-wrap justify-center gap-x-6 gap-y-2 text-sm text-text-secondary">
            <Link to="/guide" className="hover:text-brand-ink transition-colors">User guide</Link>
            <Link to="/privacy" className="hover:text-brand-ink transition-colors">Privacy policy</Link>
            <Link to="/terms" className="hover:text-brand-ink transition-colors">Terms of use</Link>
            <Link to="/status" className="hover:text-brand-ink transition-colors">Service status</Link>
          </nav>
        </div>
      </footer>
    </div>
  )
}
