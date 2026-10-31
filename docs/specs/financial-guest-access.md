# Financial professional guest access

**Status:** built 2026-10-29 (migration 052). Decisions are Tom's unless noted.

A **financial professional** — a tracing expert, a CPA, a forensic accountant
retained on a case — is given access to one or more matters' **Accounts**,
**Transactions** and **Inventory**, with the client's consent, and nothing else.
They read, they export, and they tag transactions with tags of their own. That
is the whole of it.

---

## 1. Why this needs its own surface

Two facts about the existing code decide most of the design.

1. **Staff routes are not matter-scoped.** Any attorney, paralegal or admin can
   open any matter; `require_role(_STAFF_ROLES)` checks the role and nothing
   else. Adding a guest role to `_STAFF_ROLES` would give an expert retained on
   one case **every matter in the firm**, plus pleadings, discovery, billing and
   the CRM. It is the obvious one-line change and it is the catastrophic one.
2. **`SupabaseManager` bypasses RLS** (CLAUDE.md §7). The database will not
   catch a scoping mistake; the API is the only boundary.

So the guest gets what the client got in 044: **its own router
(`/api/v1/guest/...`), its own dependency (`require_guest`), its own service,
its own response schemas and its own shell.** Every guest endpoint resolves
scope through one function — `guest_access_service.require_grant` — the way
`client_portal_service.require_matter` is the one for clients.

`tests/test_guest_access.py` fails if `financial_professional` appears in any
`require_role([...])` list or `_*ROLES` constant outside the guest router.

The frontend had the same hole: `ProtectedRoute` admitted any role that was not
`client` into the staff app. It now admits **staff roles only**.

---

## 2. What a guest can and cannot do

| Area | Guest | Notes |
| ---- | ----- | ----- |
| Accounts list | ✅ read | institution, type, last four, name on account, **ownership and characterization** (Tom: yes), held-by party, closed, currency |
| Account notes, purpose | ❌ | attorney work product |
| Edit / merge / delete / add account | ❌ | |
| Statements | ✅ read | period, balances, Bates range, whether it ties, **the source PDF** |
| Statement flags, extraction provenance, correction history | ❌ | written for staff; the client portal makes the same call |
| Delete / retry / re-reconcile a statement | ❌ | |
| Transactions on a statement | ✅ read | removed (soft-deleted) lines are not shown |
| Add / edit / delete a transaction | ❌ | |
| Transactions search | ✅ | filters on account, date, category (read-only), text, amount, check, **their own tags** |
| Assign a category | ❌ | categories are shown, never set |
| Tags | ✅ **their own only** | create, rename, retire, apply, remove. They see no firm tag, no matter tag and no client tag (Tom). The "untagged" filter is not offered: "no tag at all" would reveal that staff had tagged a line |
| Inventory | ✅ read | including exclusions (grey) and currency conversion; who excluded an account is not shown |
| Exclude from inventory totals | ❌ | |
| Exports (CSV / MD / DOCX / PDF) | ✅ | transactions and inventory, identical to staff's, with a running footer naming who they were prepared for |
| Undisclosed accounts, creditor and custodian triage | ❌ | unreviewed candidates and strategy |
| FIS, compliance matrix, received documents, splitting bench | ❌ | not in the three tabs |
| Pleadings, claims, discovery, billing, leads, other matters | ❌ | |

### Guest tags

A guest's tags are rows in `transaction_tags` with `guest_id` set and the
matter set. Their links carry `source = 'guest'` and `tagged_by_guest_id`.

- **Visible to staff, read-only**, labelled with the guest's name, so the
  firm can see what the expert flagged and build on it.
- **Never visible to another guest** — not even another guest on the same
  matter. That keeps a consulting expert's work from being "reviewed by" a
  testifying expert, which would expose it.
- **Excluded from staff tag pickers and the tag manager.** Staff do not apply
  or edit an expert's tags; they are the expert's assertions.
- A guest's search results report **only their own tag ids**. The staff search
  decorates each line with every tag; the guest layer filters that list before
  it leaves the server.

---

## 3. Designation

Every grant records one of:

| Designation | Means |
| ----------- | ----- |
| `testifying_expert` | Designated to testify. What they were provided is disclosable |
| `consulting_expert` | Consulting only. Work product, unless reviewed by a testifying expert |
| `non_expert` | An accountant, financial advisor or similar helping the client, not retained as an expert |

The designation is a fact about the engagement, set by staff and changeable;
each change is audited.

---

## 4. Disclosure: what the guest was provided

Tom: the obligation (Tex. Fam. Code 301.052(a)(6); Tex. R. Civ. P. 195) is to
identify **what was provided at the document level** — not every query run or
tag clicked. The transaction database is compiled from the statements, which
are themselves provided, so it is not a separate compilation to disclose.

So there is **no per-query read log** for disclosure. There is a **document
ledger** per grant: every statement (source document) that was available to the
guest while their grant was active, with its account, period, filename and Bates
range.

**Why a ledger and not a query over current statements:** a rejected statement
is *hard-deleted* (CLAUDE.md §11, "Rejecting a statement deletes it"). A
statement the expert had access to and staff later rejected would otherwise
vanish from "what the expert was provided". The ledger row survives — it is not
a foreign key — and records when the statement stopped being available.

**How it is kept:** synchronised **on every guest request** that touches a
matter, rather than hooked into every ingest and delete path.

- A statement on the matter that the ledger does not hold is added, stamped
  `first_available_at`.
- A ledger row whose statement no longer exists is stamped `removed_at`.

This records exactly what was available at the moments the guest was using the
system. A statement imported and rejected between two of their visits was never
in front of them, and correctly does not appear. Two queries per request,
deterministic, no hooks to forget.

Staff see the ledger on the grant and can export it.

Separately, **sign-ins** are recorded (who, when) — not for disclosure, but so
"was this access used after we revoked it?" has an answer.

---

## 5. Consent

**Staff attestation** is the path (Tom). A staff member records that the client
consented — by phone, in person, by email or letter — with the date and an
optional note. The grant activates on that record, which names the staff member
who made it.

- An **emailed consent link** is acceptable and is the planned second path. Not
  built in this pass.
- Tom accepted the residual risk (a spouse who can read the client's email): the
  worst case is that an expert the firm retained gets access the firm already
  provisionally granted. There is little attack surface.
- Consent is per grant — per guest, per matter. A second matter needs its own.

On activation the guest is emailed that the client has consented, with the
reminder that **the access exists to use the Cyclone transaction database on
behalf of, and solely for the benefit of, the client.** The same text is shown
on the guest's first screen.

---

## 6. Lifecycle

```
staff adds guest + grant ──► pending_consent ──(attest)──► active ──► revoked
                                    │                         │
                                    └────────(revoke)─────────┘
                                         active past expires_at = expired
```

- **Expiry is required**, default **180 days**, renewable. `expired` is derived
  from `expires_at` at request time rather than written by a job — a job that
  did not run would leave access open.
- **Revocation is immediate.** Every guest request re-reads the grant; nothing is
  cached in the JWT or the browser.
- **Revoke one matter, or all of a guest's matters**, from the matter page.
- Revoked and expired grants keep their ledger and consent record forever.
- Re-granting after revocation creates a new grant with new consent.

---

## 7. Data model (052)

| Table | Holds |
| ----- | ----- |
| `guest_professionals` | The person: name, email, organization, profession, `firm_id`, active |
| `matter_guest_grants` | Guest × matter: designation, purpose, status, expiry, who requested, activated, revoked and why |
| `guest_consents` | The attestation: method, date consent was given, who attested, note |
| `guest_document_ledger` | Per grant, every statement made available, with first-available and removed timestamps |
| `guest_sign_ins` | Guest, time |
| `user_roles` | `role = 'financial_professional'`, `guest_id` set, staff and client ids null; the subject check is widened |
| `transaction_tags` | `guest_id` — the owner of a guest tag |
| `financial_account_transaction_tags` | `source = 'guest'`, `tagged_by_guest_id`; the one-actor check is widened |

Every new table enables RLS in the migration (040). `firm_id` on
`guest_professionals` because attribution cannot be backfilled.

### Sign-in

The guest signs in exactly as staff and clients do (Google or a magic link to
their email). Staff adding a guest creates a `user_roles` row with
`auth_email` and no uid; the existing correlation (`/auth/correlate-staff`,
which is generic over `user_roles`) links it on first sign-in.

---

## 8. Not built in this pass

- The emailed consent link (section 5).
- An admin page listing every guest across matters. The matter page is the only
  place grants are managed.
- Reminders before a grant expires.
- Guest access to the received-documents pile. Statements' own PDFs are
  available; unsplit productions are not.
