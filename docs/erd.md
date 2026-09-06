# Cyclone — Entity Relationship Diagrams

Nine subject-area diagrams. Split deliberately: one ERD covering every table is
unreadable, and crossings grow much faster than node count. `MATTERS` and
`STAFF` repeat across frames so each frame can stay small.

Vetted line by line against the deployed schema after migration 032. Every
cardinality below traces to a column's nullability and its foreign key — if you
change a column between `NULL` and `NOT NULL`, the diagram needs updating too.

Migrations 029 and 030 added columns only. **031 and 032 add tables**, and with
them the ninth frame: `fis_category_settings` records how often a category is
paid, and `transaction_category_rules` files a transaction by keyword. 032 also
puts categorization provenance on the transaction itself.

## Notation

| Marker | Meaning |
| ------ | ------- |
| `\|o` | zero or one |
| `\|\|` | exactly one |
| `}o` | zero or more |
| `}\|` | one or more |
| `--` | enforced by a foreign key |
| `..` | reference with **no** foreign key — a cross-database join, or a link deliberately left unenforced |

Two rules that account for nearly every error worth catching:

1. **A nullable foreign key is `|o` / `o|` on the parent side, never `||`.**
2. **Markers are sided.** Zero-or-one is `|o` on the left of the line and `o|`
   on the right. `STAFF o|--o{ JOBS` puts a right-hand marker in a left-hand
   slot and renders wrong.

Labels read as a sentence from the left entity outward — *OFFICES houses
STAFF*. Mixed direction is what makes an ERD hard to read aloud.

---

## 1. People and access

```mermaid
erDiagram
    OFFICES  ||--o{ STAFF : houses
    STAFF    ||--o{ STAFF_SLUG_ACCESS : "granted lead access"
    STAFF    |o--o| AUTH_USERS : "authenticates as"
    STAFF    ||--o{ USER_ROLES : holds
    CLIENTS  ||--o{ USER_ROLES : holds
    STAFF    ||--o{ MATTER_STAFF : "staffed on"
    MATTERS  ||--o{ MATTER_STAFF : "staffed by"
    STAFF    |o--o{ MATTERS : "billing reviewer for"
    STAFF    ||--o{ MATTER_RATE_OVERRIDES : "rate overridden on"
    MATTERS  ||--o{ MATTER_RATE_OVERRIDES : "overrides rates for"
```

- `staff.supabase_uid` is nullable — a staff row exists before that person ever
  logs in — so `AUTH_USERS` is `|o--o|`. It lives in the `auth` schema, outside
  the one we control; named without a dot because a dot breaks the parser.
- One person holds several roles as separate `user_roles` rows. **Clients hold
  roles too**, which migration 021 unblocked.
- `matters.billing_review_staff_id` is nullable: zero or one reviewer per
  matter, many matters per reviewer.

## 2. Split billing

```mermaid
erDiagram
    MATTERS ||--o{ BILLING_SPLITS : "divided by"
    CLIENTS ||--o{ BILLING_SPLITS : "owes a share of"
```

`billing_splits` divides the **bill** between multiple clients on one matter.
Attorney revenue split is `matter_staff.split_pct` where `role = 'originating'`
— a different table. Both foreign keys here are `NOT NULL`, so this is a plain
junction, not a many-to-many on both sides.

## 3. Leads and the agent

```mermaid
erDiagram
    LEADS_WORKFLOW           }o--o| STAFF : "assigned to"
    LEADS_WORKFLOW           |o--o| CLIENTS : "converted to"
    LEADS_WORKFLOW           |o--o| MATTERS : "opened as"
    LEADS_WORKFLOW           ||..o{ LEAD_ACTIONS : "timeline of"
    LEADS_WORKFLOW           ||..o{ LEAD_AGENT_RUNS : "agent turns on"
    LEADS_WORKFLOW           ||..o| LANDING_PAGES_LEADS : "mirrors"
    LEAD_ACTIONS             }o--o| STAFF : "logged by"
    STAFF                    ||--o{ ATTORNEY_LEAD_RESPONDERS : "is the attorney"
    STAFF                    ||--o{ ATTORNEY_LEAD_RESPONDERS : "is the responder"
    STAFF                    ||--o{ JOBS : requested
    MATTERS                  |o--o{ JOBS : "scoped to"
```

- Lead rows themselves live in the **landing-pages** project; this database
  holds `leads_workflow`, joined in Python. `LANDING_PAGES_LEADS` is drawn on a
  dashed edge to mark the boundary.
- Every `foreign_session_uuid` edge is dashed: there is no foreign key behind
  it, so nothing prevents an orphan.
- `lead_actions.staff_id` is nullable — the agent logs actions with no human
  behind them.
- `attorney_lead_responders` gets two labelled edges because both of its columns
  point at `staff`; one edge would hide that it pairs two people.
- `jobs.matter_id` is nullable because the queue predates it: a matter-intake
  job runs *before* the matter exists, so only jobs raised from inside a matter
  — statement ingestion, so far — carry one.
- `jobs.params` (migration 025) holds the options a job was *started* with, the
  counterpart to `result`. First use is the Bates prefix override: typed at the
  upload, needed by the worker minutes later in another process.

## 4. Money and the calendar

```mermaid
erDiagram
    CLIENTS        ||--o{ MATTERS : "engages us on"
    MATTERS        ||--o{ BILLING_CYCLES : "billed in"
    MATTERS        ||--o{ BILLING_ENTRIES : accrues
    STAFF          ||--o{ BILLING_ENTRIES : records
    BILLING_CYCLES |o--o{ BILLING_ENTRIES : invoices
    MATTERS        ||--o{ TRUST_LEDGER : "holds funds in"
    STAFF          ||--o{ TRUST_LEDGER : posts
    MATTERS        ||--o{ FEE_AGREEMENTS : "governed by"
    MATTERS        ||--o{ MATTER_EVENTS : calendars
```

- A client can exist with no matter — that is what a prospect is, and what
  intake creates before the matter row.
- `billing_entries.billing_cycle_id` is nullable: an unbilled entry has no cycle
  yet, which is the point of the `v_unbilled_entries` view.
- Nothing constrains a matter to one fee agreement, and the status enum includes
  `voided` — which only makes sense if a replacement can follow.

> `fee_agreements.template_id` is not drawn: it references a `templates` table
> that does not exist anywhere in the schema. Dead column or missing table.

## 5. The case file

```mermaid
erDiagram
    CLIENTS          ||--o{ MATTERS : "engages us on"
    MATTERS          ||--o{ OPPOSING_PARTIES : "adverse to"
    MATTERS          ||--o{ MATTER_CHILDREN : concerns
    MATTERS          ||--o{ MATTER_PLEADINGS : "filed in"
    MATTERS          ||--o{ MATTER_CLAIMS : asserts
    MATTERS          ||--o{ MATTER_OPPOSING_COUNSEL : "opposed in"
    MATTER_PLEADINGS ||--o{ MATTER_CLAIMS : pleads
    MATTER_PLEADINGS |o--o{ MATTER_PLEADINGS : amends
    OPPOSING_PARTIES |o--o{ MATTER_PLEADINGS : "filed by"
    OPPOSING_PARTIES |o--o{ MATTER_CLAIMS : "asserted by"
    OPPOSING_PARTIES |o--o{ MATTER_OPPOSING_COUNSEL : "represented by"
    OPPOSING_COUNSEL ||--o{ MATTER_OPPOSING_COUNSEL : "appears through"
```

- The three `OPPOSING_PARTIES` edges carry the case's meaning: which party filed
  a pleading, whose claim a claim is, and which party an attorney appears for.
  All three are nullable.
- `amends_pleading_id` is the amendment chain that drives
  `status = 'superseded'`, so the self-relation is worth drawing.
- `opposing_counsel` is the shared attorney record, deduplicated on
  `(bar_state, bar_number)`; a junction row points at exactly one.
- A matter opened from a phone call has no pleading at all.

## 6. Discovery

```mermaid
erDiagram
    MATTERS                 ||--o{ DISCOVERY_REQUESTS : "served with"
    DISCOVERY_REQUESTS      ||--o{ DISCOVERY_REQUEST_ITEMS : contains
    DISCOVERY_REQUEST_ITEMS ||--o| DISCOVERY_RESPONSES : "answered by"
    STAFF                   ||--o{ DISCOVERY_REQUESTS : ingested
    STAFF                   ||--o{ DISCOVERY_REQUEST_ITEMS : ingested
    MATTERS                 ||--o{ DISCOVERY_REQUEST_ITEMS : "shortcut, held true by composite FK"
```

Split out of diagram 5 — it was the densest corner, and it is now the cleanest
chain in the schema.

- `discovery_responses.discovery_request_id` is `UNIQUE`, so at most one
  response per item; an item awaiting the client has none yet.
- `discovery_request_items.matter_id` is a denormalized shortcut that keeps
  `get_by_matter` and `get_pending_client` single-table. Since migration 022 a
  composite foreign key on `(discovery_request_id, matter_id)` guarantees it
  equals the parent document's, so the shortcut cannot lie.

---

## 7. Account statements

```mermaid
erDiagram
    MATTERS                        ||--o{ FINANCIAL_ACCOUNTS : "inventories"
    FINANCIAL_ACCOUNTS             |o--o| FINANCIAL_ACCOUNTS : "succeeds"
    FINANCIAL_ACCOUNTS             ||--o{ FINANCIAL_ACCOUNT_STATEMENTS : "statements for"
    FINANCIAL_ACCOUNT_STATEMENTS   ||--o{ FINANCIAL_ACCOUNT_TRANSACTIONS : "lines on"
    OPPOSING_PARTIES               |o--o{ FINANCIAL_ACCOUNTS : "held by"
    STAFF                          ||--o{ FINANCIAL_ACCOUNT_STATEMENTS : ingested
    JOBS                           |o--o{ FINANCIAL_ACCOUNT_STATEMENTS : "extracted by"
    MATTERS                        ||--o{ FINANCIAL_ACCOUNT_STATEMENTS : "shortcut, held true by composite FK"
    FINANCIAL_ACCOUNTS             ||--o{ FINANCIAL_ACCOUNT_TRANSACTIONS : "shortcut, held true by composite FK"
```

The source of the inventory, the settlement spreadsheet, and the waste and
reimbursement exhibits. Added by migration 023.

- `financial_accounts.opposing_party_id` is nullable and means "held by the
  other side"; `NULL` is our own client's account, not missing data.
- `property_character` is nullable on purpose. Characterization is argued, not
  extracted, so a freshly ingested account has none until an attorney sets one.
- Both denormalized shortcuts follow the pattern established in diagram 6:
  `financial_account_statements.matter_id` and
  `financial_account_transactions.financial_account_id` each carry a composite
  foreign key back to the parent's `(id, <key>)` unique constraint, so neither
  can drift from the row above it.
- `source_job_id` is nullable and `ON DELETE SET NULL` — a statement outlives
  the job that produced it, and job rows are prunable.
- A statement is unique on `(financial_account_id, period_start, period_end)`
  only where `review_status <> 'rejected'`. Rejecting a bad extraction frees
  the period so the same PDF can be run again.
- `ownership` (migration 026) says who holds the account: `client_sole`,
  `opposing_sole`, `joint`, `third_party`, `unknown`. It exists because
  `opposing_party_id` alone could not express *joint*, and a jointly held
  account is the difference between an asset one side keeps and an asset the
  court divides. `opposing_party_id` now names *which* other party — sole owner
  or co-holder. It defaults to `unknown`, not `client_sole`: every existing row
  was created by extraction, which never determined this.
- The transaction→statement composite FK is `ON UPDATE CASCADE` since migration
  026, which is what makes an account merge possible. Moving a statement changes
  a key its transactions point at; under the default `NO ACTION` there is no
  order of updates that works, in either direction.
- `antecedent_account_id` is the one self-reference in the schema. A reissued
  card or a bank migration produces several accounts with different numbers;
  read separately they look like three half-produced accounts with alarming
  gaps, and read as a chain they are one continuous history. It points
  *backwards* so a new number can be linked the day it appears, and a partial
  unique index keeps a chain single-file — two accounts cannot claim the same
  predecessor, or "what came next" stops having one answer.

---

## 8. Classifying transactions

```mermaid
erDiagram
    TRANSACTION_CATEGORIES              |o--o{ TRANSACTION_CATEGORIES : "parent of"
    TRANSACTION_CATEGORIES              |o--o{ FINANCIAL_ACCOUNT_TRANSACTIONS : "files"
    FINANCIAL_ACCOUNT_TRANSACTIONS      ||--o{ FINANCIAL_ACCOUNT_TRANSACTION_TAGS : "tagged by"
    TRANSACTION_TAGS                    ||--o{ FINANCIAL_ACCOUNT_TRANSACTION_TAGS : "applied to"
    MATTERS                             |o--o{ TRANSACTION_TAGS : "scopes"
    STAFF                               ||--o{ FINANCIAL_ACCOUNT_TRANSACTION_TAGS : "tagged"
    TRANSACTION_CATEGORIES              ||--o{ TRANSACTION_CATEGORY_RULES : "files into"
    MATTERS                             |o--o{ TRANSACTION_CATEGORY_RULES : "scopes"
    TRANSACTION_CATEGORY_RULES          |o..o{ FINANCIAL_ACCOUNT_TRANSACTIONS : "filed (no FK)"
    STAFF                               |o--o{ FINANCIAL_ACCOUNT_TRANSACTIONS : "filed or reviewed"
```

Two axes over the same rows, deliberately built from different mechanisms
because they answer different questions. Added by migration 024.

- **Category is one per line**, a firm-wide hierarchy nesting to arbitrary
  depth (Housing > Utilities > Gas). It drives the Financial Information
  Statement — the personal income statement filed for a temporary orders
  hearing — where a line in two buckets double-counts. Firm-wide rather than
  per-matter so an FIS is comparable across cases.
- **Tags are many-to-many**, in two layers held in one table: `matter_id` NULL
  is a firm-wide tag offered everywhere ("Waste Claim"), a value scopes it to
  one case ("Waste: Sister's Wedding"). They drive the Rule 1006 summaries, and
  a single line is routinely evidence in several exhibits at once — which is
  exactly why this cannot be a column.
- `include_in_fis` is what keeps a stock split off the income statement: money
  that moves without being income or expense.
- `category_id` is `ON DELETE RESTRICT`, not `SET NULL`. Quietly un-filing
  evidence because someone tidied the chart is how an FIS loses a line with
  nobody noticing; retiring a category sets `is_active` instead.
- `financial_account_transactions.deleted_at` (migration 028) is the one soft
  delete in the schema. Statements and accounts are removed outright, because
  the PDF in Storage is the undo; a line is not, because dropping one asserts
  it is not printed on the document and changes whether the statement
  reconciles. Every read excludes them by default, and the matter-close
  workflow is meant to sweep them.
- The join table carries `tagged_by_staff_id`. Tagging is an attorney judgment
  that gets cross-examined, so the record says who made it.
- The join table also carries a surrogate `id` on top of its natural
  `(transaction_id, tag_id)` key, purely so `BaseRepository`'s update and
  delete by id work unchanged.

Migration 032 adds keyword rules and, first, the provenance that makes them
answerable.

- **`transaction_category_rules` uses the same two layers as tags**: `matter_id`
  NULL is a firm-wide rule, a value scopes it to the client whose EXXON lines are
  revenue rather than fuel. `applies_to` constrains a rule by sign, because
  PAYROLL arriving is income and PAYROLL leaving is an expense.
- **`category_rule_id` is drawn `..`, with no foreign key, on purpose.** A rule
  is editable and deletable; the record that a rule filed this line has to
  outlive it. The moment that record is most wanted is right after somebody
  deletes the rule that caused the problem they are investigating — a real FK
  would either block that delete or erase the evidence of it.
- `category_source` says whether a person, a rule, or a similarity match filed
  the line. NULL means never categorized, which is **not** the same as a person
  deciding it belongs nowhere; that case is a human source with a null
  `category_id`.
- `category_reviewed_at` exists so reviewed-and-correct is distinguishable from
  never-looked-at. Without it the work queue never empties, because confirming
  an automatic assignment would leave no trace.
- Both staff references are nullable and point at the same table, which is why
  `STAFF |o--o{ FINANCIAL_ACCOUNT_TRANSACTIONS` appears once for two columns:
  who filed it, and who later confirmed it.

---

## 9. The Financial Information Statement

```mermaid
erDiagram
    TRANSACTION_CATEGORIES ||--o{ FIS_CATEGORY_SETTINGS : "scheduled by"
    CLIENTS                |o--o{ FIS_CATEGORY_SETTINGS : "pays on this schedule"
    OPPOSING_PARTIES       |o--o{ FIS_CATEGORY_SETTINGS : "pays on this schedule"
    MATTERS                ||--o{ OPPOSING_PARTIES : "has"
    CLIENTS                ||--o{ MATTERS : "brings"
```

How often a category is paid, which is arithmetic rather than decoration. The
FIS reports an average **monthly** figure, and for anything paid less often than
monthly, dividing the window total by the window's months is wrong in a way that
moves: $3,600 of property tax paid once in January reads as $1,800/month over
January–February and $1,200 over January–March. Same facts, a different sworn
figure every time the report is re-run. Added by migration 031.

- **Scoped to the person, not the matter.** A payment schedule is a fact about
  someone's finances, not about a lawsuit — the same client may have matters in
  several counties from successive marriages and pays property taxes on the same
  schedule in all of them. Both party columns are nullable and at most one is
  set; **neither set is the firm-wide default** ("property taxes are usually
  annual"), the same two-layer shape as tags and category rules.
- A `CHECK` enforces that `client_id` and `opposing_party_id` are never both
  set. Three partial unique indexes give one row per category at each of the
  three scopes — a plain unique index would not, because NULL is not distinct
  from NULL.
- **`opposing_parties` is itself matter-scoped**, so settings for the other side
  stay with that matter while a client's follow the client across cases. That
  falls out of the existing schema rather than being chosen here, and it is why
  the two edges look symmetrical but do not behave alike.
- `stated_annual_amount` is the attorney's own figure and overrides anything
  derived. It is **signed**, like every other amount in the schema, because the
  reason to state a figure at all is that the transactions do not show one — so
  there is no sign to borrow.
- No table records the statement itself. It is computed on demand from the
  transactions as they stand, which is what lets re-filing one line change it.

---

## Tables no diagram shows

Standalone by design — nothing references them and they reference nothing.
Adding them would cost clarity and return nothing.

| Table | Why it stands alone |
| ----- | ------------------- |
| `audit_log` | Deliberately polymorphic; `entity_id` is text so it can point at any table without a foreign key |
| `standard_privileges`, `standard_objections`, `kb_articles` | Lookup and content tables — copied from, never joined to |
| `telegram_users`, `processed_inbound_emails` | Integration bookkeeping |
| `v_matter_summary`, `v_unbilled_entries` | Views, not tables |

## Keeping the crossings down

Reordering statements will not fix crossings. Mermaid lays ER diagrams out with
dagre, which derives positions from the graph itself; there is no way to pin an
entity. What actually helps:

- **Fewer entities per frame.** Crossings grow far faster than node count.
  Splitting discovery out of diagram 5 removed most of the tangle in one move.
- **Declare each hub's edges together.** Keeping every `MATTERS` line adjacent
  gives dagre a cleaner ordering than interleaving hubs.
- **Repeat the hubs across diagrams.** That is what lets each frame stay small.
- **Try `direction LR`** as the first line inside `erDiagram`. Wide, shallow
  graphs like diagram 4 often untangle completely on a landscape screen.
