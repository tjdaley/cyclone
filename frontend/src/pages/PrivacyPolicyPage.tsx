import { Link } from 'react-router-dom'

export default function PrivacyPolicyPage() {
  return (
    <div className="min-h-screen bg-white font-sans">
      <header className="border-b border-border">
        <div className="max-w-3xl mx-auto px-6 h-16 flex items-center">
          <Link to="/" className="font-display text-2xl text-navy tracking-tight">Cyclone</Link>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-12">
        <h1 className="font-display text-4xl text-navy mb-2">Privacy Policy</h1>
        <p className="text-text-secondary text-sm mb-8">Last updated: April 8, 2026</p>

        <div className="prose prose-navy max-w-none space-y-6 text-sm leading-relaxed text-text-primary">
          <section>
            <h2 className="font-semibold text-navy text-lg mt-8 mb-2">1. Introduction</h2>
            <p>
              Cyclone ("we", "us", or "our") operates the Cyclone legal practice management platform
              (the "Service"). This Privacy Policy explains how we collect, use, disclose, and
              safeguard your information when you use the Service.
            </p>
          </section>

          <section>
            <h2 className="font-semibold text-navy text-lg mt-8 mb-2">2. Information We Collect</h2>
            <p><strong>Account information.</strong> When you sign in via Google OAuth, we receive your name, email address, and profile picture from Google. We use this solely to authenticate your identity and link you to your firm or client account.</p>
            <p><strong>Practice data.</strong> Attorneys and staff enter client information, matter details, billing entries, discovery materials, and related records into the Service. This data is stored in a Supabase (PostgreSQL) database provisioned for your firm.</p>
            <p><strong>Payment information.</strong> Payment processing is handled by Stripe. We do not store credit card numbers, CVVs, or full bank account details on our servers. Stripe's privacy policy governs the handling of payment credentials.</p>
            <p><strong>Usage data.</strong> We collect server-side logs that include request timestamps, IP addresses, HTTP methods, and response codes. These logs do not contain case facts, client names, or financial amounts.</p>
          </section>

          <section>
            <h2 className="font-semibold text-navy text-lg mt-8 mb-2">3. How We Use Your Information</h2>
            <ul className="list-disc pl-5 space-y-1">
              <li>To provide, operate, and maintain the Service</li>
              <li>To authenticate users and enforce role-based access control</li>
              <li>To process billing and generate invoices</li>
              <li>To detect and prevent fraud, abuse, and security incidents</li>
              <li>To comply with legal obligations</li>
            </ul>
            <p>We do not sell, rent, or trade your personal information or practice data to third parties.</p>
          </section>

          <section>
            <h2 className="font-semibold text-navy text-lg mt-8 mb-2">4. Data Sharing</h2>
            <p>We share data only in the following circumstances:</p>
            <ul className="list-disc pl-5 space-y-1">
              <li><strong>Service providers.</strong> Supabase (database hosting), Stripe (payment processing), and Google (authentication). Each provider processes data under their own privacy policies and our data processing agreements.</li>
              <li><strong>AI providers.</strong> When you use AI-powered features (natural language billing, discovery ingestion), the text you submit is sent to the configured LLM provider (e.g., Google Gemini, Anthropic, OpenAI). These submissions do not include client names or identifying information unless you type them into the prompt.</li>
              <li><strong>Legal requirements.</strong> We may disclose information if required by law, court order, or governmental regulation.</li>
            </ul>
          </section>

          <section>
            <h2 className="font-semibold text-navy text-lg mt-8 mb-2">5. Data Security</h2>
            <p>
              All data is encrypted in transit (TLS 1.2+) and at rest (AES-256 via Supabase).
              Access to practice data is governed by row-level security policies and role-based
              access control enforced at the application layer. Audit logs record all sensitive
              actions including billing operations and role changes.
            </p>
          </section>

          <section>
            <h2 className="font-semibold text-navy text-lg mt-8 mb-2">6. Data Retention</h2>
            <p>
              Practice data is retained for the duration of your firm's subscription and for a
              reasonable period thereafter to allow for data export. Upon written request, we will
              delete all firm data within 30 days, subject to any legal retention obligations.
            </p>
          </section>

          <section>
            <h2 className="font-semibold text-navy text-lg mt-8 mb-2">7. Your Rights</h2>
            <p>
              You may request access to, correction of, or deletion of your personal data by
              contacting your firm administrator or by emailing us at the address below. Firm
              administrators may export all practice data at any time through the Service.
            </p>
          </section>

          <section>
            <h2 className="font-semibold text-navy text-lg mt-8 mb-2">8. Children's Privacy</h2>
            <p>
              The Service is not directed to individuals under the age of 18. We do not knowingly
              collect personal information from children.
            </p>
          </section>

          <section>
            <h2 className="font-semibold text-navy text-lg mt-8 mb-2">9. Changes to This Policy</h2>
            <p>
              We may update this Privacy Policy from time to time. We will notify registered users
              of material changes via email or an in-app notice. Continued use of the Service after
              changes constitutes acceptance of the revised policy.
            </p>
          </section>

          <section>
            <h2 className="font-semibold text-navy text-lg mt-8 mb-2">10. Contact Us</h2>
            <p>
              If you have questions about this Privacy Policy, please contact us at:<br />
              <strong>privacy@cyclone.law</strong>
            </p>
          </section>
        </div>

        <div className="mt-12 pt-6 border-t border-border">
          <Link to="/" className="text-sm text-navy hover:underline">&larr; Back to Cyclone</Link>
        </div>
      </main>
    </div>
  )
}
