# Cost tracking — design and handoff

Handoff for whoever writes the operator, admin and partner-facing documentation
for this work. It records **what is built, what is designed but not built, the
reasoning behind each decision, and the traps** — the things the code alone will
not tell you. The code is authoritative for *what*; this is the *why*.

**Status (2026-09-17):** Steps 1 and 2 of 4 are built — every model call is
captured, normalized across vendors, attributed, and **priced** from dated rates
on the **Admin > Costs** page. Steps 3–4 (storage and hosting costs, budgets) are
designed below and not started.

| Source of truth | |
| - | - |
| `app/services/llm_usage.py` | Vendor normalization and the recorder. The module docstring carries the per-vendor mapping. |
| `app/util/usage_scope.py` | Attribution: who a call was for. |
| `app/services/llm_service.py` | Where every attempt is recorded (`_run`). |
| `db/migrations/039_cost_tracking.sql` | `firms` and `llm_calls`. |
| `tests/test_llm_usage.py` | The capture guarantees, pinned. |
| `db/migrations/041_service_rates.sql` | Prices, the pricing view, the report and set-price functions. |
| `app/services/cost_service.py` | Report window, exact money, audited price changes. |
| `frontend/src/pages/app/CostsPage.tsx` | Admin > Costs. |
| `tests/test_cost_sql.py` | The pricing SQL against real Postgres 16 (needs Docker). |

---

## 1. The goal, and the question it answers

Two things: **know** what running Cyclone costs, and be able to **show** that the
question is under control — to the firm, and to a partner or acquirer asking how
a per-seat subscription survives unbounded model spend.

That breaks into four questions, each a build step:

1. **What did we use?** — tokens, per call, per vendor. *Built.*
2. **What did it cost?** — tokens × a dated price. *Built.*
3. **What else costs money?** — storage per matter; hosting and subscriptions. *Designed.*
4. **Can a firm be stopped from running up an unbounded bill?** — budgets. *Designed.*

Step 1 is the one that cannot be deferred. Everything else can be added later
and applied to history. **Usage that was never recorded cannot be recovered**, and
attribution that was never recorded — which firm, which matter — can never be
reconstructed.

---

## 2. What was wrong before, in two facts

These are worth stating in any document about this, because the natural
assumptions are both false.

- **Nothing captured token usage.** Every vendor call function returned a bare
  string and discarded the response object carrying the counts. Cost was
  unknowable from inside the application.
- **Model spend was not limited to background jobs and billing entry.** Ten
  services call a model. Among them: the CRM agent in the worker, which works on
  a *lead* — before anyone is a client, with no staff member and no matter — and
  pleading and discovery uploads, which run inside a web request.

---

## 3. Step 1 — built: capture

### One row per vendor call, in `llm_calls`

**Every attempt a failover chain makes is a row, not just the one that
answered.** A call that times out after the vendor has started generating, or
returns a reply that is then rejected, is billed all the same. A ledger of
successes would understate spend precisely when the chain is misbehaving —
which is when anybody would go looking.

**The table is append-only.** A trigger refuses `UPDATE`, like `audit_log`. A
cost ledger that can be edited is not evidence of cost. Price corrections belong
in the rate table (step 2), where a price actually changes; token counts never
do. `DELETE` is left possible for a future retention policy.

**It stores tokens, not dollars.** See step 2 for why.

### Normalization — the part most likely to be done wrong

Vendors disagree about what "input tokens" means. Summed naively, **the same
prompt costs different amounts depending on which vendor answered it**:

| Normalized column | Anthropic | OpenAI / Groq | Gemini | DeepSeek |
| - | - | - | - | - |
| `input_uncached_tokens` | `input_tokens` — **already excludes** cache | `prompt_tokens` **minus** cached | `prompt_token_count` **minus** cached | `prompt_cache_miss_tokens` |
| `input_cache_read_tokens` | `cache_read_input_tokens` | `prompt_tokens_details.cached_tokens` | `cached_content_token_count` | `prompt_cache_hit_tokens` |
| `input_cache_write_tokens` | `cache_creation_input_tokens` | — | — | — |
| `output_tokens` | `output_tokens` — **includes** thinking | `completion_tokens` — **includes** reasoning | `candidates_token_count` **plus** `thoughts_token_count` | `completion_tokens` |
| `reasoning_tokens` | not reported | `completion_tokens_details.reasoning_tokens` | `thoughts_token_count` | `completion_tokens_details.reasoning_tokens` |

The three traps, stated plainly for the docs:

- **OpenAI and Gemini count cached tokens inside their input figure; Anthropic
  does not.** Without subtracting, every OpenAI cache hit is billed twice.
- **Gemini reports thinking tokens beside its output count, not inside it**,
  though they are billed as output. Without adding them, a thinking model is
  billed for its visible answer only.
- **Unreported is `NULL`, never zero.** A call that died before any response has
  no counts at all. A report that summed `NULL` as `0` would understate spend
  while looking complete. The one deliberate exception: an OpenAI-style response
  with no cache breakdown is taken as *nothing cached*, so its whole prompt counts
  at the full rate — the direction that overstates cost rather than hiding it.

**The vendor's usage object is stored verbatim in `usage_raw`.** The mappings
above were written against the SDKs as documented, and vendor SDKs drift — this
codebase has been bitten by that twice (CLAUDE.md §11). If a mapping is ever found
to be wrong, the normalized columns can be recomputed from the raw object. See
§6, *First thing to do after deploying*.

### Attribution — who a call was for

A call records `firm_id`, `staff_id`, `matter_id`, `auth_uid`, `source_type` and
`source_id`. They are set **once, where the work starts**, and read by the
recorder — not threaded through every service signature, which would have
touched dozens of functions and broken the first time somebody added a call.

| Work | `source_type` | `source_id` | Also carries |
| - | - | - | - |
| A background job (statement ingest, matter intake) | `job` | job id | `matter_id`, `staff_id` (who started it) |
| A web request | `request` | e.g. `POST /api/v1/billing/parse` | `auth_uid`; `matter_id` on billing parse |
| A CRM tick step | `crm` | step name, e.g. `inbound mail poll` | — |
| The CRM welcome agent, per lead | `crm_lead` | lead session id | — |
| Anything with no scope set | `unattributed` | — | — |

**`unattributed` is not an error.** It is spend that reached a model from
somewhere nobody set a scope — a script, a future code path. It is recorded so
the gap *shows up in the totals* rather than vanishing from them.

**A web request records `auth_uid`, not `staff_id`.** Resolving the staff member
at call time would add a database round trip to every model call. Reports join
`staff.supabase_uid = llm_calls.auth_uid`.

**Only `firm_id` is a foreign key, deliberately.** `matter_id` and `staff_id`
must *outlive* what they point at: a matter deleted next year was still paid for
this year, and a cost row that vanished or went `NULL` with it would make the
books disagree with the vendor's invoice.

#### Two traps in the attribution mechanism — worth a paragraph in developer docs

Both were checked by running them, not assumed.

- **A value set around `pool.submit(...)` does not reach the pool thread.** The
  thread starts with an empty context. `job_service.run_claimed` therefore sets
  the job's scope *inside* the function the pool runs.
- **A value set inside a sync FastAPI dependency never reaches the endpoint.**
  FastAPI runs the dependency in its own threadpool call and discards that
  context. `require_role` — where the staff member is resolved, and the obvious
  place — would have looked right and recorded nothing. The request scope is set
  in `AuthMiddleware` instead.

### The minimal `firms` table

**One row, and `firm_id` on every ledger row defaults to it.** Attribution to a
firm cannot be added after the fact, so the column has to exist from the first
row. This is **not** multi-tenancy: nothing moves out of the `PER_FIRM` settings,
and matters, clients and staff carry no firm yet. True tenancy is a separate
project.

### Recording never fails the work

The same rule as the audit logger, with a harder edge:

- Rows go onto a **bounded queue drained by one background thread**. The
  database client retries a failed insert three times with backoff, so writing
  inline would add seconds to a model call whenever the database hiccuped.
- **A missing table** (migration 039 not yet run) is detected, logged **once** at
  ERROR naming the migration, and recording stops for the life of that process.
  The work carries on.
- A full queue drops rows and logs rarely, with a running count.

**Known loss, accepted:** rows still queued when a process exits are flushed for
up to five seconds and then abandoned. The alternative is a shutdown that can
hang on a database that is down.

### Known gaps in step 1

- ~~Pleading preview and discovery upload were not attributed to their matter.~~
  **Fixed 2026-09-17**: both are, and discovery upload records the staff member
  too. The scope wraps the route's OCR as well as the service's extraction,
  because both spend tokens. **Intake preview is attributed to the user and
  route only, and that is not a gap**: its purpose is to create the matter, so
  there is none yet.
- Recording happens per process. Nothing yet checks that both nodes are writing;
  a node whose writer disabled itself says so only in its own log.

---

## 4. Step 2 — built: price

**What was built matches the design below**, with these specifics:

- `service_rates` columns: `provider, model, meter, unit_count, price
  numeric(14,6), currency ('USD' only), effective_from, effective_to,
  source_url, note, created_by_staff_id`. The period is half-open, `[from, to)`.
- **An exclusion constraint** (`service_rates_one_price_at_a_time`, btree_gist)
  makes two prices for the same meter at the same instant impossible. Without
  it, the pricing join would return both and silently price those calls twice.
- **`set_service_rate()`** is how a price is entered: it ends the price in force
  and starts the new one in a single transaction. It refuses a date earlier than
  a price that already starts later, with a sentence saying so (HTTP 409) —
  rewriting a schedule somebody entered deliberately should be a correction, not
  a side effect.
- **Corrections** (a typo in the price, source or note) are a separate, audited
  operation that cannot move dates. Every price change writes an `audit_log` row
  with the before and after.
- **`llm_call_costs`** prices every call and gives it a `pricing_status`:
  `priced`, `unpriced` (tokens with no rate in force — cost NULL), or `no_usage`
  (vendor reported nothing — cost NULL). **There is no fallback price.**
- **`llm_unpriced_meters`** is the to-do list: exactly which `(vendor, model,
  meter)` carried tokens with no price. The page offers "Set price" beside each,
  starting on the first day it was used so every affected call is covered.
- **`llm_cost_report()`** groups by **UTC month** and one of vendor/model, task
  profile, matter, staff member, or source, and returns how many calls are
  failed, unpriced or usage-less beside the money. A report with any unpriced
  call is marked incomplete, and the page labels its total **"Priced so far"**
  with a warning above the figure.
- **Priced on the model that answered (042).** Every row records the model the
  catalog *requested* (`model`) and the model the vendor's response says
  *served* it (`served_model`), plus the vendor's own response id
  (`vendor_response_id`). Pricing matches `service_rates.model` against the
  served model, falling back to the requested one only where the vendor named
  none (rows from before 042), and each priced row says which it used. Aliases
  and preview names get repointed to newer versions; when that happens the new
  version appears under **Missing prices** by its own name, with the requested
  name beside it, rather than being charged at the old version's price.
- Security: RLS on the table, both views `security_invoker`, both functions
  closed to `anon` and granted to `service_role` only. Admin role only in the API.
- **Nothing is seeded.** Every price is entered from the vendor's pricing page.

For partner material: *every cost figure traces to vendor-counted tokens, the
model the vendor says answered, the vendor's own response id, and a dated,
sourced, audited price; totals that are incomplete say so.*

### The original design, for reference

### A rate table, as columns — not a composed key

Proposed shape:

| Column | Example |
| - | - |
| `provider` | `anthropic` |
| `model` | `claude-opus-5` |
| `meter` | `input_uncached` · `input_cache_read` · `input_cache_write` · `output` · `storage_gb_month` · … |
| `unit_count` | `1000000` |
| `price` | `numeric(14,6)` — never a float |
| `currency` | `USD` |
| `effective_from` | `2026-09-16` |
| `effective_to` | `NULL` while current |
| `source_url` | the vendor pricing page it was copied from |

**Why not a key like `anthropic_fable_high_input_tokens`:** matching a ledger row
to its price would mean rebuilding that string from `vendor`, `model` and the
meter, and a string built in two places drifts. This project fixed exactly that
class of bug three times in the week this was designed. Columns join exactly:
`(vendor, model, meter)` and `occurred_at` within the effective range.

**Prices are copied from the vendors' pricing pages, with `source_url` recorded.**
None are seeded by guesswork.

### Price at read time, never at write time

**The ledger stores what the vendor counted; cost is computed when a report runs.**
Prices get entered late and corrected. A cost frozen onto a row when the call
happened would be wrong forever; tokens are a fact that never changes. This is
also what lets a price entered in October apply correctly to September's calls.

**Open question for step 2:** some models charge a higher rate above an input
length threshold, and batch requests are discounted. The rate table above cannot
express either. Decide whether to model tiers before seeding prices for any
model that has them.

---

## 5. Steps 3 and 4 — designed, not built

### Step 3 — storage and hosting

- **Storage is billed per GB per month, so it needs a time series**, not a total:
  a nightly snapshot of bytes stored per matter. Uploads already live under
  `matters/{id}/…`; intake uploads under `intake/{job_id}` are attributed through
  their job.
- **Hosting and subscriptions (EC2, load balancer, Supabase plan) are invoice
  lines entered monthly** by hand from the AWS billing console to start.
- **How shared costs are spread across firms is a policy, not data.** By share of
  model spend? By storage? Evenly? Store the invoice lines; allocate in the
  report. Decide the policy when a second firm exists.

### Step 4 — budgets

- **Dollars, not tokens.** Tokens are not comparable across models — an output
  token on one model can cost a hundred times an input token on another.
- **Checked when a job is queued, never between model calls.** Stopping mid-job
  would leave half a bank statement read, which is worse than the overspend it
  prevents.
- **The gate is a Redis counter per firm per month — a cache of the ledger, not
  the source of truth.** It can always be rebuilt from `llm_calls`. No separate
  `firm_llm_usage` table: a stored running total drifts from the rows it
  summarises, and a `GROUP BY` over an indexed ledger is fast at this volume.
- **If Redis is unreachable, the gate lets the work through and logs it.** A
  budget check that fails closed turns a Redis blip into a paralegal who cannot
  import a statement.

---

## 6. Operating it

### Deploying step 2 (pricing)

1. Run **`040_enable_rls.sql`** and **`041_service_rates.sql`**, in that order.
2. Deploy **api, worker and frontend** — the Costs page is new frontend code.
3. Open **Admin > Costs** as an admin. "Missing prices" lists every model meter
   already used; enter each from the vendor's pricing page with its source URL.
4. When "Missing prices" is empty and the total no longer says "Priced so far",
   spend is fully priced.

### Deploying the served-model change (042)

1. Run **`042_served_model.sql`**. It can run before or after the deploy; code
   that finds the columns missing still records usage, without the served
   model, and logs one ERROR naming the file. Run 042 promptly — rows written
   before it are priced on the requested model forever.
2. Deploy **api, worker and frontend**.
3. After a few calls, check what each vendor actually reports:
   ```sql
   select vendor, model as requested, served_model, count(*)
     from llm_calls where served_model is not null
    group by 1, 2, 3 order by 1, 2;
   ```
   Wherever `served_model` differs from `requested`, prices are entered under
   the served name. **Missing prices** lists them.
4. **Do not re-run 041 after 042.** It will fail (harmlessly — it is one
   transaction), because it would otherwise put back pricing on the requested
   model.

### Deploying step 1

1. **Run `db/migrations/039_cost_tracking.sql` by hand, before deploying the
   code.** Calls made first are spend nobody can ever attribute.
2. **Rename the firm:** `update firms set name = '…' where id = 1;` — the
   migration seeds `'Default firm'` because it cannot read `FIRM_NAME`.
3. Deploy **api and worker** (they share an image).
4. Make one natural-language billing entry, then:
   ```sql
   select occurred_at, source_type, source_id, vendor, model, outcome,
          input_uncached_tokens, output_tokens
     from llm_calls order by id desc limit 5;
   ```
   A row means the whole path works. If there is none, look in the API log for
   `llm_usage:` — a missing table says so by name.

### First thing to do after deploying — verify the mappings

**Compare one day's totals per vendor against that vendor's own usage console.**
The mappings were written against documented SDK shapes; this is the check that
they match the versions actually installed. Disagreement means a mapping is
wrong, and `usage_raw` holds what is needed to fix history.

### Querying spend before step 2 exists

Usage by month, vendor and model, with the two counts that say how much to trust
the rest:

```sql
select date_trunc('month', occurred_at)                          as month,
       vendor, model,
       count(*)                                                  as calls,
       count(*) filter (where outcome = 'failed')                as failed_calls,
       count(*) filter (where output_tokens is null)             as calls_without_usage,
       sum(input_uncached_tokens)                                as input_full_rate,
       sum(input_cache_read_tokens)                              as input_cache_read,
       sum(output_tokens)                                        as output
  from llm_calls
 group by 1, 2, 3
 order by 1 desc, 2, 3;
```

By staff member — a job records `staff_id`, a web request records `auth_uid`, so
both are joined:

```sql
-- staff.name is a FullName JSON object; the work email is the plain label.
select s.id, s.email, count(*) as calls, sum(c.output_tokens) as output
  from llm_calls c
  join staff s on s.id = c.staff_id or s.supabase_uid = c.auth_uid
 group by s.id, s.email
 order by output desc nulls last;
```

By matter: `group by matter_id`. What is unattributed:
`where source_type = 'unattributed'`.

**`calls_without_usage` is not a bug by itself** — every call that failed before
the vendor responded has no counts. A *successful* call without usage is.

---

## 7. For partner- or acquirer-facing material

The story, in the order it is persuasive:

- Every model call is recorded — including retries and failures — with its
  vendor-reported token counts, reconciled across vendors that count differently.
- Every call is attributed to a firm, and where it applies to a matter, the staff
  member who started it, or the web request that made it. Spend with no owner is
  recorded and reported as such rather than lost.
- The ledger is append-only and priced at read time from dated vendor rates, so
  a cost figure can be traced to the counted tokens and the published price in
  force that day.
- Budgets are designed to gate new work before it starts rather than interrupt
  work in progress.

**Be precise about status.** Usage capture and pricing are built. Storage and
hosting costs, and budgets, are designed, not built. Say so.
