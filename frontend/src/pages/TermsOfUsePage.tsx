import { Link } from 'react-router-dom'

export default function TermsOfUsePage() {
  return (
    <div className="min-h-screen bg-white font-sans">
      <header className="border-b border-border">
        <div className="max-w-3xl mx-auto px-6 h-16 flex items-center">
          <Link to="/" className="font-display text-2xl text-navy tracking-tight">Cyclone</Link>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-6 py-12">
        <h1 className="font-display text-4xl text-navy mb-2">Terms of Use</h1>
        <p className="text-text-secondary text-sm mb-8">Last updated: April 8, 2026</p>

        <div className="prose prose-navy max-w-none space-y-6 text-sm leading-relaxed text-text-primary">
          <section>
            <h2 className="font-semibold text-navy text-lg mt-8 mb-2">1. Agreement to Terms</h2>
            <p>
              By accessing or using the Cyclone legal practice management platform (the "Service"),
              you agree to be bound by these Terms of Use ("Terms"). If you do not agree, do not
              use the Service. These Terms apply to all users including firm administrators,
              attorneys, paralegals, and clients accessing the client portal.
            </p>
          </section>

          <section>
            <h2 className="font-semibold text-navy text-lg mt-8 mb-2">2. Description of Service</h2>
            <p>
              Cyclone provides a cloud-based platform for legal practice management, including
              client and matter management, time and billing entry, discovery collaboration,
              document management, and a client-facing portal. The Service is provided on a
              subscription basis as described in Section 5.
            </p>
          </section>

          <section>
            <h2 className="font-semibold text-navy text-lg mt-8 mb-2">3. User Accounts</h2>
            <p>
              Access to the Service requires authentication through Google OAuth as configured by
              your firm administrator. You are responsible for maintaining the security of your
              Google account credentials. You must notify your firm administrator immediately of
              any unauthorized access to or use of your account.
            </p>
            <p>
              Firm administrators are responsible for creating staff and client records, assigning
              roles, and managing access within the Service. Access is role-based: attorneys,
              paralegals, and administrators have access to the staff portal; clients have access
              to the client portal only.
            </p>
          </section>

          <section>
            <h2 className="font-semibold text-navy text-lg mt-8 mb-2">4. Acceptable Use</h2>
            <p>You agree not to:</p>
            <ul className="list-disc pl-5 space-y-1">
              <li>Use the Service for any unlawful purpose or in violation of any applicable regulation</li>
              <li>Attempt to gain unauthorized access to other firms' data or accounts</li>
              <li>Reverse engineer, decompile, or disassemble any part of the Service</li>
              <li>Interfere with or disrupt the integrity or performance of the Service</li>
              <li>Use the Service to store or transmit malicious code</li>
              <li>Resell, sublicense, or redistribute access to the Service without written consent</li>
            </ul>
          </section>

          <section>
            <h2 className="font-semibold text-navy text-lg mt-8 mb-2">5. Subscription and Payment</h2>
            <p>
              The Service is billed monthly via credit or debit card through Stripe. Charges are
              calculated on a <strong>per-active-matter basis</strong>. An "active matter" is defined
              as any matter that has one or more billing entries recorded during the billing month.
              Matters with no billing activity in a given month are not counted toward that month's
              charge.
            </p>
            <p>
              The per-matter rate and any applicable base fees are established in your firm's
              subscription agreement. Payment is due on the first of each month for the prior
              month's usage. Failed payments will be retried according to Stripe's retry schedule.
            </p>
            <p>
              We reserve the right to change pricing with 30 days' written notice. Price changes
              take effect at the start of the next billing cycle following the notice period.
            </p>
          </section>

          <section>
            <h2 className="font-semibold text-navy text-lg mt-8 mb-2">6. Data Ownership</h2>
            <p>
              Your firm retains full ownership of all practice data entered into the Service,
              including client records, matter details, billing entries, discovery materials,
              documents, and related content. We claim no ownership interest in your data.
            </p>
            <p>
              You grant us a limited, non-exclusive license to host, store, process, and display
              your data solely for the purpose of providing and improving the Service.
            </p>
          </section>

          <section>
            <h2 className="font-semibold text-navy text-lg mt-8 mb-2">7. Confidentiality</h2>
            <p>
              We recognize that the Service will contain information protected by the
              attorney-client privilege, work product doctrine, and other legal protections. We
              will treat all practice data as confidential and will not access, use, or disclose
              it except as necessary to provide the Service, respond to support requests authorized
              by the firm administrator, or comply with legal process.
            </p>
          </section>

          <section>
            <h2 className="font-semibold text-navy text-lg mt-8 mb-2">8. AI-Powered Features</h2>
            <p>
              Certain features of the Service, including natural language billing entry and
              discovery request ingestion, use third-party large language model (LLM) APIs to
              process text. By using these features, you acknowledge that:
            </p>
            <ul className="list-disc pl-5 space-y-1">
              <li>Text submitted to AI features is transmitted to the configured LLM provider</li>
              <li>AI-generated outputs (parsed billing entries, classified discovery requests) are provided for attorney review and must be confirmed before they take effect</li>
              <li>AI outputs may contain errors and should not be relied upon without professional review</li>
              <li>You should avoid including personally identifiable information in AI prompts when possible</li>
            </ul>
          </section>

          <section>
            <h2 className="font-semibold text-navy text-lg mt-8 mb-2">9. Service Availability</h2>
            <p>
              We strive to maintain high availability but do not guarantee uninterrupted access to
              the Service. We may perform scheduled maintenance with reasonable advance notice.
              We are not liable for downtime caused by factors outside our control, including
              internet outages, third-party service disruptions, or force majeure events.
            </p>
          </section>

          <section>
            <h2 className="font-semibold text-navy text-lg mt-8 mb-2">10. Limitation of Liability</h2>
            <p>
              TO THE MAXIMUM EXTENT PERMITTED BY LAW, CYCLONE AND ITS OFFICERS, DIRECTORS,
              EMPLOYEES, AND AGENTS SHALL NOT BE LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL,
              CONSEQUENTIAL, OR PUNITIVE DAMAGES, OR ANY LOSS OF PROFITS OR REVENUES, WHETHER
              INCURRED DIRECTLY OR INDIRECTLY, OR ANY LOSS OF DATA, USE, GOODWILL, OR OTHER
              INTANGIBLE LOSSES RESULTING FROM YOUR USE OF THE SERVICE.
            </p>
            <p>
              OUR TOTAL AGGREGATE LIABILITY FOR ALL CLAIMS ARISING OUT OF OR RELATING TO THESE
              TERMS OR THE SERVICE SHALL NOT EXCEED THE AMOUNT YOU PAID US IN THE TWELVE (12)
              MONTHS PRECEDING THE CLAIM.
            </p>
          </section>

          <section>
            <h2 className="font-semibold text-navy text-lg mt-8 mb-2">11. Termination</h2>
            <p>
              Either party may terminate the subscription with 30 days' written notice. Upon
              termination, your firm may request an export of all practice data within 60 days.
              After that period, we will delete all firm data in accordance with our data retention
              policy.
            </p>
            <p>
              We may suspend or terminate access immediately if you materially breach these Terms,
              fail to pay fees after reasonable notice, or if continued service would expose us to
              legal liability.
            </p>
          </section>

          <section>
            <h2 className="font-semibold text-navy text-lg mt-8 mb-2">12. Governing Law</h2>
            <p>
              These Terms are governed by and construed in accordance with the laws of the State
              of Texas, without regard to conflict of law principles. Any disputes arising under
              these Terms shall be resolved in the state or federal courts located in Dallas
              County, Texas.
            </p>
          </section>

          <section>
            <h2 className="font-semibold text-navy text-lg mt-8 mb-2">13. Changes to Terms</h2>
            <p>
              We may revise these Terms from time to time. We will notify registered users of
              material changes via email or an in-app notice at least 30 days before the changes
              take effect. Continued use of the Service after changes constitutes acceptance.
            </p>
          </section>

          <section>
            <h2 className="font-semibold text-navy text-lg mt-8 mb-2">14. Contact</h2>
            <p>
              For questions about these Terms, please contact us at:<br />
              <strong>legal@cyclone.law</strong>
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
