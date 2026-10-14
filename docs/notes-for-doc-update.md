# Notes for the documentation update — cost tracking, insurance statements, deploy hardening

Handoff notes for the thread that maintains `docs/operator-guide/` and
`docs/user-guide/`. They cover the change set **not yet committed** as of
2026-09-17. The earlier version of this file (config, secrets, fleet checks) has
already been worked into `docs/operator-guide/` chapters 1–6, and it is replaced
here. Where this change set makes one of those chapters wrong, §1 says so.

**Everything here is something the diff will not tell you:** why a choice was
made, a procedure that spans the database, AWS and the app, a recovery step, or
a trap that has already bitten. For *what* the code does, the code wins. For
*why* and *how to operate it*, use this.

| Authoritative source | For |
| - | - |
| `docs/cost-tracking-design.md` | Full cost-tracking design, deploy steps, SQL queries, and the partner-facing wording (§7). **Read it before writing about Costs.** |
| `db/migrations/038`–`042` | Each header explains what that migration is for and what it is safe to do. |
| `app/services/llm_usage.py` (docstring) | How each vendor's token counts are mapped. |
| `app/services/statement_service.py` | Flag conditions and wording. |
| `CLAUDE.md` §11 (LLM Service, Statement Service), §15 (RLS, constraint handler) | Condensed rationale. |

---

## 0. This release, as an operator will run it

### Migrations, in order

| File | What it does | Known status | Safe to run before the deploy? |
| - | - | - | - |
| `038_insurance_accounts.sql` | Adds `life_insurance` and `annuity` to the account-type CHECK | **Tom ran it** on the database he was testing against | Yes. Run it first. |
| `039_cost_tracking.sql` | `firms` table (one row) plus the `llm_calls` ledger | **Tom ran it** and saw a row | Yes. Run it **before** the deploy: calls made earlier are never recorded. |
| `040_enable_rls.sql` | RLS on every table lacking it; views closed to the public key | **Not confirmed run on production** | Yes |
| `041_service_rates.sql` | Price table, pricing views, report function | Not confirmed | Yes |
| `042_served_model.sql` | Records which model actually answered, and prices on that | Not confirmed | Yes. It can also run after the deploy (see §2.7). |
| `043_cost_report_by_firm.sql` | Adds **Firm** to the Costs page's groupings | Not confirmed | Yes |
| `044_client_portal_tagging.sql` | Client tags, cleared tags, client-visible tags, the per-matter tagging lock. Seeds seven client-facing waste tags | Not run | Yes. Applied and re-applied against real Postgres 16 |

`docs/operator-guide/04-deploying.md` still says *"This release: 036 and 037"*.
Update it.

### Deploy

- Rebuild and redeploy **api, worker and frontend**. The Costs page is new
  frontend code, and the usage ledger is written by both the api and the worker.
  Shipping only the api leaves every job's spend unrecorded.
- `deploy-version.bat` now **refuses to build** when `SUPABASE_URL` or
  `SUPABASE_ANON_KEY` is missing from the build machine's `.env` (§1.1).
- After deploy, **open the app in a browser**. The script now says so, and §1.1
  explains why nothing else will tell you.
- **Rename the firm:** `update firms set name = '<firm name>' where id = 1;`.
  039 seeds `'Default firm'` because a migration cannot read `FIRM_NAME`.

---

## 1. Corrections to the existing operator guide

### 1.1 The build args belong on the BUILD machine, not on every node — `01-configuration.md`, `04-deploying.md`

`01-configuration.md` § *"Must stay in `.env` — SSM cannot supply it"* lists
`SUPABASE_URL`, `SUPABASE_ANON_KEY` and `API_BASE_URL` without saying *where*.
That reads as "on every server", which is wrong, and the mistake it invites has
already happened.

**The distinction is build time vs runtime, not one node vs another.**

| Name | Must be in `.env` on | Why |
| - | - | - |
| `ID`, `CYCLONE_SECRETS_SOURCE` | every node | Identity, and the switch that turns the SSM loader on |
| `AWS_REGION` | every node (optional where IMDS answers) | Bootstrap |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY` | **the machine that runs `docker compose build`** | Compiled into the JavaScript bundle |
| `API_BASE_URL` | nowhere | Compose gives it a `:-` default |

A production node's `.env` legitimately holds only `ID`,
`CYCLONE_SECRETS_SOURCE` and `AWS_REGION`, plus optionally `CYCLONE_ENV`.
`SUPABASE_URL`, `REDIS_URL` and `HOST_URL` can all come from SSM, because the
API reads them at runtime inside the container. Keeping the two build args in a
node's `.env` anyway is cheap insurance in case anybody ever builds on a server.

**Fix the symptom description, too.** The chapter says a missing anon key
"first appears in a browser at login". What actually shipped was worse:

- `SUPABASE_URL` was removed from the build machine's `.env` during the SSM
  migration.
- The next build compiled an empty string into the bundle.
- **Every route served a blank white page.** `createClient("")` throws
  `supabaseUrl is required.` before React mounts, so there is no login screen to
  fail at.
- The build exited 0, the container was healthy, nginx answered `GET / → 304`,
  and the fleet hash was clean.
- **No server log anywhere had a line about it**, because the app died before
  making its first request.
- The only evidence was four words in the **browser console**.

Worth a troubleshooting entry: *blank white page after a deploy → open the
browser console → `supabaseUrl is required` → the build machine's `.env` is
missing the build args → fix it, rebuild, redeploy the frontend.* A restart does
nothing.

**Three guards now exist. Document the rule, and name the guards as backstops:**

1. `deploy-version.bat` refuses to build without the two names. This is the only
   point where the problem can be caught, because it is created at build time.
2. The runtime guard used to be keyed on the `COMPOSE_ONLY` bucket.
   `SUPABASE_URL` is in the `INFRASTRUCTURE` bucket, so the guard never saw it,
   and that is how the gap happened. It now keys on a separate list of *names
   compose interpolates*.
3. A test fails if that list stops matching `docker-compose.yml`.

### 1.2 New startup lines — `05-startup-log.md`

Add these to the "Lines from the rest of the boot sequence" table:

| Line (abridged) | Level | Means | Do |
| - | - | - | - |
| `REDIS_URL is not set on this node and fell back to redis://localhost:6379/0, which names THIS machine…` (also `HOST_URL`, `ID`) | WARNING | A setting that differs per node was never set, and its default points at the node itself. **Suppressed in development**, where those defaults are correct. | Set it in `.env` or SSM. On Redis this is serious: **every node thinks it holds the fleet-wide mail-poller lock**, so all of them poll the mailbox. |
| `llm_usage: the llm_calls table does not exist. Run db/migrations/039_cost_tracking.sql…` | ERROR | Usage recording is **off for the life of that process**. | Run 039, then **restart api and worker**. Running the migration alone does not bring recording back. |
| `llm_usage: llm_calls has no served_model column. Run db/migrations/042_served_model.sql, then restart api and worker…` | ERROR, once | Usage **is** still recorded, without the served model. | Run 042, then **restart api and worker**. That process keeps writing without the new columns until it restarts. |
| `Database schema: … llm_calls … served_model / vendor_response_id` | ERROR | The same missing 042, reported by the schema check | As above |
| `llm_usage: could not write a cost record (…); N consecutive failure(s)` | WARNING at failures 1, 10, 100, then every 1000 | The writer could not insert. **Each failing row is lost** once the database client's own three retries are used up; that spend is not recorded. | Check database connectivity |
| `llm_usage: ledger queue full; N cost record(s) dropped` | WARNING | The database has been unreachable long enough that 10,000 rows are waiting. Spend is now being **lost**. | Urgent if it persists |
| `Database rejected a write: check constraint, sqlstate=23514, path=… The offending row is deliberately not logged` | ERROR, at request time rather than startup | A user picked a value the database does not allow yet. **Almost always a migration that has not been run.** | Run the missing migration. The user saw a sentence saying so (§3.2). |

### 1.3 `REDIS_TIMEOUT_SECONDS` — `01-configuration.md` / `.env.example`

- New, **FLEET** bucket (fingerprinted), default `3.0`. It covers connecting and
  reading alike.
- **It bounds a hang, not a refusal.** A refused connection already fails in
  about two seconds and never reaches the cap. What it fixes is a host that
  accepts a connection and then goes silent. Before, that stalled the mail-poller
  tick indefinitely while it held the fleet-wide lock, so **one wedged node
  stopped every node from polling**.
- **Q&A worth including:** *"The test suite (or the dev worker) is suddenly very
  slow."* `REDIS_URL` moved into SSM. A developer machine has no SSM, so it falls
  back to `redis://localhost:6379/0`, and every Redis call waits before failing.
  Measured: 74.7 s for one suite, 8.9 s once bounded. The fix is to put
  `REDIS_URL` back in the **dev** `.env`.
- `localhost` resolves to both `::1` and `127.0.0.1`, which are tried in turn, so
  an unreachable `localhost` URL costs exactly twice what `127.0.0.1` costs.
  Measured 4.07 s vs 2.04 s.

---

## 2. Admin › Costs — new feature

Menu item **Costs**, **admin only**. `docs/user-guide/12-user-roles.md`'s menu
table needs a row for it. It probably also needs a short chapter, in the
operator guide or an admin section; it is not paralegal work.

The page has three sections: **Spend** (by month, grouped by **firm**,
vendor/model, task, matter, staff member, or where it started), **Missing
prices**, and **Prices**.

**Grouping by firm reads as a single row today**, and that is expected: there is
one firm. It exists because `llm_calls` has carried a firm on every row since
039 -- attribution cannot be added to past rows -- and because it is the
grouping that says what the others mean once there is a second firm: *spend by
matter* across two firms is a list nobody can act on without knowing whose
matters they are. Renaming the firm changes the label on past months too; the
report keys on the firm's id, not its name.

### 2.1 What the numbers are, and are not

- **Model-API spend only.** Supabase, AWS, storage and hosting are *designed,
  not built* (design doc §5). Budgets are not built. **Do not let the docs imply
  a total cost of running Cyclone.**
- Figures are **vendor-counted tokens × the price entered for that day**.
  Nothing is estimated from text length.
- **Every attempt counts, including failures.** A call that failed over to the
  next model, or whose reply was rejected as unparseable, was still billed by the
  vendor. So a statement import can show several calls to different vendors for
  one piece of work. That is correct, not duplication.
- **"Priced so far" instead of "Total"** means some calls have no price entered.
  The figure is then a **floor**. Unpriced calls count as *unknown*, never as
  $0.
- **`<$0.01`** means more than zero and less than a cent. It is shown because
  rounding to cents would print `$0.00`, which reads as free.
- **Months and dates are UTC**, because vendor invoices are. A call made at 8 pm
  Texas time on 30 September counts in **October**.
- **`unattributed`** under *Where it started* is spend from a path that set no
  owner. It is recorded so the gap shows up; it is not an error.
- **Intake preview has no matter, by design.** Its purpose is to *create* the
  matter. Its spend is attributed to the user and the request.

### 2.2 Entering a price — the procedure

**Where to start:** use **Missing prices** → **Set price**. It fills in the
vendor, the exact model name, the meter, and a start date equal to the first day
that model was used, so every affected call is covered. Typing the model name by
hand is how a price ends up never matching (a trailing space, `flash-lite` vs
`flash_lite`). The form trims spaces, but it cannot fix a wrong name.

**Prices are per 1,000,000 tokens**, which is how every vendor quotes them.
Enter the **Standard** (paid, on-demand) tier. Cyclone does not use Batch, Flex
or Priority.

**Which line on a vendor's pricing page goes into which meter.** This is the
question that needs documenting, because the vendors word it differently:

| Meter in Cyclone | Anthropic page | OpenAI page | Gemini page |
| - | - | - | - |
| **Input** | Base input | Input | Input price |
| **Cached input** | Cache hits & refreshes | Cached input | Context caching price |
| **Cache write** | 5-minute cache writes | — (not billed) | — (not billed per token) |
| **Output** | Output | Output | Output price ("including thinking tokens") |

- **Thinking and reasoning tokens are Output** on every vendor, and the ledger
  already counts them there.
- **A meter no call has used needs no price.** Missing prices lists only meters
  that actually carried tokens.
- **Not modelled, and why the report can differ from an invoice:** Gemini's
  cache *storage* charge (per million tokens **per hour**), grounding with
  Search or Maps, and any price tier that applies only above a prompt length
  (some models charge more past 200k tokens; the ledger holds one price per
  meter). Cyclone does not currently use grounding or explicit caching.

**Worked example** (from 2026-09-16, Gemini 3.5 Flash-Lite, Standard):
Input $0.30 → **Input**. Output $2.50 → **Output**. Context caching $0.03 →
**Cached input**. The $1.00/1M-per-hour storage line is skipped.

**Always fill in the source URL**, plus a note on the tier. The price table is
the evidence behind every cost figure. A price with no source is an assertion.

### 2.3 Changing a price vs correcting one — two different buttons

| | **New price** (or Set price) | **Correct** |
| - | - | - |
| Use when | The vendor changed its price from a date | You typed it wrong |
| Dates | Ends the price in force the moment the new one starts, in a single step | **Cannot move dates** |
| Effect on past calls | None before the new start date | Re-prices every call the row covered |
| Audit log | `service_rate.set` | `service_rate.corrected`, with before and after |

- **A start date earlier than a price that already starts later is refused**
  (HTTP 409, with a sentence explaining it). This stops somebody rewriting a
  schedule without meaning to. The remedy is to correct the later row, or choose
  a date after it.
- **There is no delete.** A wrong price is corrected, not removed.
- **Past spend changes when a price is corrected.** Prices are applied when the
  report runs, never stored on the call. That is deliberate: prices get entered
  late and get fixed. Say this plainly, or a total that moved after an edit will
  look like a bug.

### 2.4 ⚠ Price the model that ANSWERED, not the one requested (042)

This is the least obvious rule on the page, and Tom asked for it for **billing
disputes on large document jobs.**

- Cyclone asks a vendor for a model by the name in its configuration. The vendor
  may **serve that name from a specific version** and says which in its
  response. OpenAI answers `gpt-5` with a dated snapshot. A "preview" or alias
  name can be repointed to a newer version with a different price.
- Every call now records both names, and **prices match the served model**.
  Calls from before 042 have no served name and are priced on the requested one;
  that fallback is recorded per call.
- **So a Missing prices row can name a model that appears nowhere in Cyclone's
  configuration.** That is the vendor's served version, and it is the name the
  price must be entered under. The row shows **"requested as …"** underneath, so
  you know which line on the pricing page to look for.
- **When a vendor repoints an alias, the new version shows up in Missing
  prices** instead of being charged at the old price. That is the feature
  working, not a regression.
- **Q&A — "the vendor no longer lists the model we used."** It happens with
  retired preview models (e.g. `gemini-3.1-flash-lite-preview`). Enter the price
  from the invoice or your own records, and say so in the note. The vendor's
  usage export generally does not say which version served a call. Cyclone's
  ledger now does.

### 2.5 Procedure: a billing dispute or an invoice that does not match

1. Compare the **same UTC month** and the **same vendor**.
2. Check whether the Spend total says **Priced so far**. If it does, clear
   Missing prices first.
3. Know why the figures can legitimately differ:
   - taxes and credits
   - a key on a free tier (the invoice is $0 while Cyclone shows a cost)
   - the unmodelled charges in §2.2
   - another application sharing the same API key
   - rows lost when a process stopped (§2.6)
4. For a specific call, the vendor's own reference is stored as
   `vendor_response_id`, which is what vendor billing support can look up.
   SQL (run in the Supabase SQL editor):

   ```sql
   select occurred_at, vendor, model as requested, served_model, vendor_response_id,
          outcome, input_uncached_tokens, input_cache_read_tokens, output_tokens
     from llm_calls
    where source_type = 'job' and source_id = '<job id>'
    order by occurred_at;
   ```

5. **The first thing to do after this ships** (design doc §6): compare one day's
   per-vendor token totals against each vendor's own usage console. The
   field-by-field mappings were written against the SDK documentation, and SDKs
   drift. If a mapping is wrong, the raw vendor object is stored on every row
   (`usage_raw`), so history can be recomputed.

### 2.6 Known limits (say them; do not let a reader find them)

- **Rows can be lost** when a process stops. Queued rows get five seconds to
  flush. This is accepted so that a shutdown cannot hang on a database that is
  down.
- **Each node records independently.** A node whose recorder switched itself off
  says so only in its own log. Nothing yet checks that both nodes are writing.
- The ledger is **append-only**. Rows cannot be edited; corrections go in the
  price table.
- Spend is not yet limited by any budget.

### 2.7 Recovery

| Situation | What happens | Do |
| - | - | - |
| Code deployed before **039** | Recording turns itself off per process, with one ERROR | Run 039, **restart api and worker**. Spend in between is gone. |
| Code deployed before **042** | Recording continues without the served model, with one ERROR | Run 042, **restart api and worker**. Rows written in between are priced on the requested model, permanently. |
| **041 re-run after 042** | **Fails, on purpose.** Nothing changes, because it runs in one transaction. | Nothing. If 041 must be re-applied, run 042 again right after. The failure prevents 041 from quietly switching pricing back to the requested model. |
| Price entered under the wrong model name | Calls stay under Missing prices | Enter it again under the name Missing prices shows. The wrong row is harmless; correct its note to say so. |

---

## 3. Security and error-handling changes

### 3.1 RLS — 040 (`operator-guide`, new section)

- **What was checked on production (2026-09-17):** every *table* already had
  RLS, switched on in the Supabase dashboard and never recorded in a migration.
  **The real exposure was two views, `v_matter_summary` and
  `v_unbilled_entries`.** A view runs with its owner's privileges, so the public
  key could read through it even though the tables were protected.
- **The public (anon) key is compiled into the frontend**, so anything it can
  read is readable by anyone who opens the app.
- 040 finds tables and views **from the database catalog**, so it also covers
  anything created by hand in the dashboard. It adds **no policies**, and that is
  safe: the backend uses the service role, which bypasses RLS, and the frontend
  never queries tables directly.
- **Rule for anyone adding a migration**, enforced by a test for everything after
  040: enable RLS on every new table, make every new view `security_invoker`,
  and revoke EXECUTE from `anon` on every new function.
- **What could break:** any external tool reading tables with the *anon* key.
  None is known. The dashboard and the backend are unaffected.
- **Verification query** (a view always reports `relrowsecurity = false`, so for
  views the anon-privilege column is the one to read):

  ```sql
  select c.relname, c.relkind, c.relrowsecurity,
         has_table_privilege('anon', c.oid, 'SELECT') as anon_can_select
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'public' and c.relkind in ('r', 'v')
   order by anon_can_select desc, c.relname;
  ```

  After 040, `anon_can_select` should be false for every view.

### 3.2 A refused value now gets a sentence, not a 500 (user guide troubleshooting)

- Before this change, picking a value the database did not yet allow gave
  *"Internal Server Error"*. The first time was choosing **Life insurance**
  before 038 was run.
- **That error also printed the whole offending row into the server log:** a
  client name and a full account number. Worth one sentence in the operator
  guide, because it is why this change is a privacy fix and not tidying.
- **What a user now sees**, roughly: the constraint name, then *"the database
  refused this value. If the application was just updated to offer a new option
  here, the matching migration has not been run on this database yet."*
- **User-guide wording:** *if you see this, tell your administrator. Nothing you
  entered was saved, and nothing is wrong with the matter.* Duplicate and
  conflicting entries now answer with a 409 sentence in the same way.

---

## 4. Statement import — user guide changes

### 4.1 New account types: Life insurance and Annuity (and Crypto in the picker)

`04-importing-statements.md`, `07-viewing-statements.md`, `11-discovery-tracking.md`
(compliance), and wherever account types are listed.

- **Why they are separate types rather than "Other" or "Brokerage".** This is
  the explanation readers will want:
  - A cash-value policy divides by **where the premiums came from**, not by whose
    name it is in.
  - It has a **beneficiary designation**, which no brokerage account has.
  - It prints **two figures that must never be added together**: a cash value,
    which is divisible property, and a death benefit, which is not.
  - An **annuity in payout** behaves like an income stream, not an asset, which
    is why it is not lumped in with life insurance.
- **Crypto** existed since migration 036 but was missing from the account-type
  dropdown. It is now there.
- **They are read like brokerage accounts:** cash in and out plus investment
  results, never internal moves between sub-accounts. The balance is the
  **total policy value**, not the cash value alone.
- **A policy statement has no transaction register.** Its "value of your policy"
  block *is* the activity: premiums, deductions, investment results. Those are
  the lines imported, and they are **period totals with no dates** (see §4.2).
- **On the compliance grid and its exhibits** they print as **Life insurance**
  and **Annuities**. **Crypto accounts printed as "Other" on every compliance
  exhibit until this release.** Anyone holding one of those exhibits should
  re-export it.
- **Changing an existing account to one of these types fails until 038 is
  run**, with the sentence in §3.2.

### 4.2 Flag changes — `05-import-problems.md` flag reference

The rule behind all three: **a flag must be true of the statement it sits on.**
A policy statement that reconciled to the cent was showing two false flags and
one instruction nobody could follow.

| Flag | Before | Now | Doc change |
| - | - | - | - |
| **SECURITIES_NOT_EXTRACTED** | On every brokerage-type statement | **Only when at least one trade was actually skipped** | Add "only appears when something was skipped" |
| **MARKET_CHANGE_NOT_PRINTED** | Whenever no market-change line was found | **Only when the statement also did not reconcile** | The existing text ("the balances will not tie") is now always true where it appears. Also say that insurers call this line other things: *"Fixed and variable account results"*, *"Investment results"*, *"Change in value"*. |
| **UNDATED_TRANSACTIONS** | Always a warning, "date them from the source page" | **Two shapes.** *Some* lines undated: still a warning, same advice, because those dates are printed on the page. **Every** line undated on a brokerage, retirement, life-insurance or annuity account: **information only.** That is a policy-style statement whose figures are period totals and never had dates. | Split the row. Keep the consequence for both: **a date-range search or exhibit will not include undated lines.** For the info case, the instruction is *nothing to do*, not "date them". |
| **INCOMPLETE_EXTRACTION** | Fired falsely on policy statements: "premium payments" was read as money *out* | No longer fires on those | No wording change. If a reader asks why an old policy import shows it, see §4.3. |

### 4.3 Statements imported before this release keep their old flags

Flags are written at import and **not recomputed**. A policy or annuity
statement imported earlier may still show the false flags, or zero transactions,
or be typed as *Other*. The fix:

1. On the Accounts tab, set the account type to **Life insurance** or
   **Annuity**. This needs 038.
2. **Retry** the statement, so it is re-read with the new rules.
3. Check that the account type came back correct after the re-read.

Remember that Retry discards **every** statement from that upload, which the
guide already says.

### 4.4 An amount range on the Transactions tab — `08-categorizing-and-fis.md`

Two new boxes beside the date range, **Amount at least** and **Amount at most**,
in the filter table at the top of *"From the Transactions tab — for bulk work"*.
The behaviour that needs explaining, because it is not guessable from the
labels:

| Typed | Finds |
| - | - |
| `5000` | **Either direction** — a $5,000 debit *and* a $5,000 credit |
| `-5000` | Money out, $5,000 or more |
| `+5000` | Money in, $5,000 or more |
| `500` to `1000` | Either direction, between $500 and $1,000 |
| `-500` to `-1000` | Money out, between $500 and $1,000 |

- **Unsigned matching both directions is the feature, not a convenience.** It is
  how an offsetting pair is found: money leaving one produced account and the
  same figure arriving in another is one movement, and the exhibit needs both
  lines. Worth saying in the guide, with that example.
- **Both boxes are amounts, not positions.** The smaller figure goes first even
  when searching money out, so `-500` to `-1000` is right and `-1000` to `-500`
  is refused with a sentence saying so.
- **Mixing `-` and `+` is refused.** A range that crosses zero is what you get
  by leaving the signs off.
- **The direction is the printed sign**, so on a credit card "money out" is a
  card *payment* and a purchase is money in. Chapter 7 already explains the sign
  convention; this is a place to cross-reference it rather than re-explain.
- **It reaches the exhibit.** The Selection block states the range *and* the
  direction — "Money out, $5,000.00 or more", or "between $500.00 and $1,000.00,
  in either direction" — so two exhibits drawn from the same matter cannot look
  contradictory. Mention it in `10-exports.md` where the Selection block is
  described.
- A figure that is not readable is refused with advice rather than ignored; the
  page waits until a box holds a whole figure before searching, so typing a
  minus sign does not flash an error.

### 4.5 Adding a line the import missed — `05-import-problems.md`, `07-viewing-statements.md`

New: **Add a line**, in the Transactions header of a statement's drill-down
(Accounts tab → account → statement). It is the fix for a statement that will
not reconcile because a line was never extracted at all — and it is what
replaces editing the database by hand, which is how it was being done.

What the guide should say:

- **It is for a line that is printed on the statement and the import missed.**
  Not for an adjustment, not for a figure from somewhere else.
- **It carries your name.** The line is recorded as added by hand, with the
  reason typed into the dialog, and that travels onto the exhibit's flag trail
  the same way a correction does. Worth stating plainly: the Rule 1006 notice
  promises the underlying records are available, so a line nobody can find on
  the production has to say where it came from.
- **The statement is re-checked on save**, which is the point. If the added line
  was the missing one, UNRECONCILED clears.
- **Sign it the way the statement moves the balance** — money out negative. The
  dialog says so; Chapter 7's sign convention is the cross-reference.
- **The date defaults to the period start** and is bounded by the period. Blank
  is allowed, for a policy-style statement whose figures never had dates, and
  the consequence is the one in §4.2: an undated line is excluded from every
  date-filtered search and exhibit.
- **Page and Bates are optional and belong to the page it is printed on.** Never
  a Bates number worked out from a neighbouring page.
- The panel now appears for a statement with **no** lines at all, which is
  exactly the statement somebody needs to add one to.

### 4.6 Reaching the source PDF from a transaction row — `05-import-problems.md`, `10-exports.md`

The document icon that was already on statement rows is now on every row of the
**Transactions** tab, inside the Bates/page cell rather than in a column of its
own. It opens that line's statement **at the page the line is printed on**,
where the statement-row button opens at the statement's first transaction page.
On a production of sixty pages that is the difference between checking a figure
and hunting for it. Mention it wherever the guide tells somebody to verify a
line against the source.

### 4.7 Why a multi-month PDF used to import eight months and then go wrong

Worth writing up plainly, because users have been working around it by hand and
the workaround is no longer necessary for this cause.

A long document was **indexed** in one pass before being read statement by
statement, and that index pass only ever saw the first 60,000 characters —
about eight monthly statements on a scanned production. Past that point the
import did not fail: it reported success for the statements it found, and the
pages belonging to later months were read as part of the last statement it knew
about. That is the "it ingests eight months and the rest has numerous errors"
experience.

The index now runs in windows across the whole document. What the guide should
say:

- **A multi-month PDF should now import whole.** If one still does not, the
  reason will be OCR quality or page count, not this.
- **New flag, `PAGES_NOT_INDEXED` (warn).** It names pages that begin a
  statement nothing read, or belong to none. Add it to the flag reference in
  `05-import-problems.md` under *"The extraction may be incomplete — the serious
  ones"*: the import is **short**, lines on those pages are missing, and where
  such a page falls inside another statement's range its lines may have been
  filed against that statement. The action is to check those pages and split the
  PDF into one statement per file if they hold months of their own.
- **Splitting is still faster**, and that advice stays: a stack of monthly files
  is read several at a time by the worker pool, where one combined PDF is read
  in sequence. The upload survey already says so.

### 4.8 A false INCOMPLETE_EXTRACTION on every credit-card statement

Fixed, and worth a line in `05-import-problems.md` if the flag reference says
anything about card statements.

A card statement's summary prints "Purchases $244.11". Cyclone signs an amount
by its effect on the printed balance, so a card purchase is **positive** — it
raises what is owed — while the word "purchase" means money *leaving* on a bank
statement. The completeness check read the printed purchases total as the
statement's **debit** total, compared it against lines that are all positive,
and reported the statement short by its own purchases. It reconciled to the
cent at the same time, which is the tell: **a statement that ties and claims to
be short is claiming something impossible.**

Nothing for a user to do differently. Older card imports still carry the flag —
flags are written at import and not recomputed — so a card statement showing
INCOMPLETE_EXTRACTION *and* reconciling can be accepted, or re-imported with
Retry to clear it.

**One thing that does matter to the user:** the fix keys on the account's
**type**. A card filed as "Checking" or "Other" still gets the bank reading and
will keep showing the false flag. Worth saying in Chapter 7 where account types
are set — the type also drives how the FIS treats the account's spending.

### 4.9 The client portal — a new audience, and a new chapter

**This needs its own guide, not a section in the staff one.** The portal has its
own vocabulary and its own reader, and the existing fourteen chapters are
written for a paralegal. Two documents to produce:

**A short client guide** (a page or two, linkable by the firm):

- What you can see: your accounts, the statements your attorney's office holds,
  every transaction, and which months are still missing.
- **Marking transactions.** Pick anything you think your attorney should look
  at. It changes nothing in the record — it is a note, and the office reviews
  each one. Tags come from a list the firm publishes; clients cannot invent
  their own.
- **A tag with a line through it** was reviewed and removed by the office, and
  cannot be put back from here. Ask them if you disagree.
- **"Tagging is paused"** means the office is preparing for a hearing. Nothing
  you marked is lost.
- Searching by amount works the same way as it does for staff: no sign finds
  money in *and* out; `-5000` finds money out only. Worth an example — it is how
  a client finds the transaction they are arguing about.

**For the staff guide**, three additions:

- **Turning the portal on for a client** — the `client` role with a client
  record, and then **Client portal** on the matter detail page to allow tagging.
  Off by default: a matter does not acquire a tagging client by accident.
- **Publishing tags.** In the tag manager, "Offer to clients" per tag. Opt-in,
  because the firm's own list carries strategy. Seven client-facing waste tags
  ship with 044 and can be renamed or retired like any other.
- **Reviewing what the client marked** — the panel under the Transactions tab.
  Removing a tag is permanent as far as the client is concerned; staff can
  restore it, and a restored tag becomes the firm's rather than the client's.
  Worth saying plainly: **a client tag that reaches an exhibit is an exhibit
  resting on a guess**, which is why the panel exists and why the two are
  visibly different.

One thing to tell the firm before the first client is let in: **a client sees
the whole matter**, including the other side's produced accounts. That is the
intended behaviour, and it is the question somebody will ask first.

### 4.10 Large PDFs are now refused, not warned about

`04-importing-statements.md` and `05-import-problems.md`.

**An upload of more than 50 pages, or holding more than 8 statements, is
refused.** The response says why and what to do, and nothing is kept: no
document in storage, no job, no partial import. The same check runs on **Retry**,
before anything is deleted — so a large document imported before this change
cannot be re-read either, and the statements already held are untouched.

Why it changed, and worth saying plainly in the guide because it reverses
earlier advice: a 700-page PDF holding 122 statements was uploaded. The warning
fired and the import ran anyway, because the warning was advice. It was a total
loss. Below these limits the advisory warning still behaves exactly as Chapter 4
describes.

What the guide should tell people to do:

- **Split the PDF into one file per statement** and drop the whole stack at
  once. Worth repeating the reason, because it is counter-intuitive: the worker
  reads several files at a time, so a stack of monthly files finishes **sooner**
  than the same months combined, as well as more accurately.
- A combined statement — several accounts, one bank, one document — is
  unaffected, since it is well inside both limits.

### 4.11 A new tab: Inventory

Financials → **Inventory**. Needs a short section of its own, probably in
`07-viewing-statements.md` or beside the FIS chapter.

What it shows: every account on the matter with the ending balance of its most
recent statement, in two tables — assets and liabilities — each totalled, with a
net beneath. Columns are institution, account, held by, as of, balance and
Bates, and the Bates cell opens the statement the figure came from.

Four things the guide should make explicit, because each one is a question
somebody will ask:

- **Liabilities show as negative.** The card statement says you owe $1,045.45;
  the inventory shows −$1,045.45, because it reduces the estate. Both are right
  and they are answering different questions.
- **Every row has its own "as of" date.** There is no live balance here — each
  figure is the closing balance of a real statement, which is what makes it
  citable. Two accounts will usually speak as of different days.
- **"no statement" is not zero.** An account with nothing on file is listed with
  no balance and is left out of the total, and the count is shown beneath. Those
  rows are the ones to chase — the Compliance tab says which months are missing.
- **Held by** is P, R or J, taken from the account's ownership plus the matter's
  client alignment. If the matter has no alignment recorded the column reads C
  and O and the page says why: set the alignment on the matter detail page.

**This is not the Inventory & Appraisement.** The I&A is a sworn form and is
still unbuilt; this is a schedule of account balances. Worth saying plainly in
the guide so nobody files the wrong thing.

### 4.12 Fewer false alarms on "Referenced but not produced"

`11-discovery-tracking.md`.

The report compared institution names as raw text, so one bank written two ways
produced a finding that was already produced — "BofA" against "Bank of America,
N.A.". Names are now compared **as words**, with the furniture ("Bank",
"N.A.", "Credit Union", "Financial") set aside so what remains is what actually
identifies the institution.

What a reader will notice:

- **A new group: "Probably already produced."** Rows whose institution closely
  resembles an account already on the matter are moved there, named with the
  account they are likely about, and **left out of the exhibit**. The exhibit's
  Selection block states how many were set aside, so the filed document never
  reads as the complete list when it is not.
- **Nothing is deleted on a near match.** The guide should be explicit about
  why: reporting an account that turns out to be produced is a visible nuisance;
  hiding one that was never produced is silent and far worse. So a close match
  demotes a row, it never removes it — and if one of them really is a separate
  account, it belongs back in the exhibit and the two account names need
  reconciling (which the merge feature already handles).
- **An account on the matter whose number could not be read** used to make every
  reference to it look undisclosed, since there was nothing to match on. Those
  now land in the same group.
- A quieter fix worth a line: a produced Chase card no longer answers for a
  creditor whose name merely contains the letters, such as "Purchase Power".

### 4.13 Three things that can now be done by hand

`04-importing-statements.md`, `11-discovery-tracking.md`, and wherever the
compliance grid is covered.

**Add an account** — from the Accounts tab, or from a row on the "Referenced but
not produced" list. The second is the one to lead with, because it changes a
workflow: that list is rebuilt from the transactions every time it runs, so
there is nothing to tick off, and a $5 repayment to a relative stays on it
forever. Adding the account is what resolves it, and the **character** recorded
says why:

- **No marital property interest** — a third party's account the production
  named in passing. It stays on the matter so references stop being reported,
  and it is left out of the compliance grid and the inventory, because there is
  nothing to compel and nothing to divide.
- **Anything else** — the account joins the compliance grid with *nothing
  produced against it*, which is exactly what a motion to compel is built from.

Worth saying plainly: this replaces keeping a list of "don't worry about it"
accounts in somebody's head or a spreadsheet, and it is also how an account from
an unprocessable scrap of paper — a receipt found in a vehicle — gets onto the
matter. A duplicate is refused, with the existing account named.

**Type in Bates numbers** — on a statement row where detection found none, an
"add Bates" link. Copy the stamps off the first and last pages exactly as
printed; the numbers between are filled across the pages held. Two things the
guide should state:

- **A partial production still runs consecutively.** Physical pages 1, 2 and 4
  carry three consecutive stamps, because the missing sheet took its number
  with it.
- **Both ends are required, and the span is checked.** If the range does not
  match the pages held, nothing is saved and the message says both counts —
  being off by one would mis-cite every line after the first.

Every line is marked as numbered by hand, and so is the statement. A detected
stamp was read off the document; a typed one is somebody's reading of it.

**Text for a motion to compel** — a button under the compliance grid. It
produces the paragraph and numbered list naming each account and the periods
nobody produced, in whole dates, ready to paste. Accounts with nothing missing
are left out. If nothing is missing at all it says so rather than producing an
empty heading.

### 4.14 What Retry looks like now — `05-import-problems.md` § Retry (was §4.4, then §4.5, §4.8–4.13, now §4.14)

Screenshots or wording to update:

- The row being re-read shows a spinner and **"Re-reading this document · 12s"**
  in place of its Accept / Retry / Reject links.
- **Every other row's links fade and lose their underline**, and hovering one
  explains why: a re-read discards and recreates every statement from that
  upload, so acting on a neighbouring row would act on a row about to be
  replaced. Before this change the links *looked* live and did nothing, and
  people clicked Retry down the list.
- The upload area shows **"Re-reading the document…"** with the timer, rather
  than "Reading the statements…", and **refuses new files until the re-read
  finishes.**
- **You can leave the page.** The re-read runs in the background.

---

## 5. Model configuration (for accuracy, not a user-facing change)

`app/config/llm_profiles.json` changed in this set. Tom moved Gemini to
`gemini-3.5-flash-lite` in the `fast` chain and `gemini-3.8-flash` as the
**last** entry in `default` and `vision`. For the docs:

- **Gemini stays last** in the chains that read evidence, for two independent
  reasons already in `docs/operator-guide/README.md`: measured accuracy, and its
  content filters refusing routine family-law material.
- **Every model change creates new Missing prices rows.** A release note that
  changes a model should say "enter prices for X". Otherwise the Costs page reads
  "Priced so far" from that day on.

---

## 6. Stale text elsewhere that the doc thread should fix or flag

- `docs/cost-tracking-design.md` § *"Querying spend before step 2 exists"*.
  Step 2 now exists. The queries still work, but they ignore prices and the
  served model. Point readers to the Costs page, or to `llm_call_costs` for SQL.
- `CLAUDE.md` §16a *"Cost tracking"* still says *"Step 1 of 4 (capture) is
  built; steps 2–4 … are designed"*. Steps 1 and 2 are built. §16's status table
  is already correct.
- `docs/operator-guide/04-deploying.md` names 036/037 as "this release" (§0).
- `docs/operator-guide/01-configuration.md` says the build args must be in `.env`
  without saying *on the build machine* (§1.1).
