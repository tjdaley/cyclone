# Co-tenancy: how a second firm would run

**Status: open question, nothing decided, nothing built.** Written 2026-09-17 so
the decision has a record and so the option that is chosen is chosen against the
alternatives rather than by default.

**The question.** Cyclone today is one deployment for one firm. The commercial
case for a second is a solo practitioner who cannot cost-justify their own EC2
instances and Supabase project. Where should the boundary between firms be —
in a `where` clause, in a process, or in a database?

**The one fact that decides most of this.** `SupabaseManager` connects with the
**service-role key, which bypasses Row-Level Security by design** (CLAUDE.md
§7). Every access-control decision Cyclone makes today happens in FastAPI —
`require_role()`, and service-layer checks like
`transaction_search_service._verify_on_matter()`. So *"add `firm_id` everywhere
and let RLS make co-tenancy safe"* does not work as stated: RLS would not be
consulted on the path the application actually uses. Making it protect anything
means changing how the backend connects to the database, which is where the
risk lives. §1 covers that in full.

---

## The four options

| | 1. Shared schema + RLS | 2. Stack per firm, Supabase per firm | 3. Schema per firm | 4. Database per firm |
| - | - | - | - | - |
| Isolation is | a `where` clause | a separate process, database and storage | a `search_path` | a connection string |
| Fails how | **silently**, wrong firm's data on screen | loudly, or not at all | **silently** | loudly |
| Marginal cost per firm | ~$0 | $10–25/mo (Supabase project) | ~$0 | n/a — see §4 |
| Host RAM per firm | none | one api + worker + nginx | none | none |
| Migrations per release | once | **once per firm** | once per firm | once per firm |
| Cross-firm reporting | one query | aggregate across projects | one query | aggregate |
| Onboarding a firm | insert a row | provision a project + directory + haproxy rule | create a schema, run every migration | n/a |
| Works with today's code | **no** | **yes**, with §5 | mostly | n/a |
| Blast radius of one bug | every firm | one firm | every firm | one firm |

---

## 1. Shared schema, `firm_id` on every table, RLS

Every firm-scoped table gains `firm_id`; policies restrict each row to the
caller's firm.

### Why it is not as safe as it sounds

**RLS is not consulted today.** To make it load-bearing, one of these has to
happen:

- **Per-request credentials.** The backend stops using the service-role key and
  connects as a role subject to RLS, carrying a JWT that names the firm. That is
  a rewrite of `db_handler`'s connection model, and it removes the service role
  that `AuditLogger`, the schema check and the background worker rely on.
- **A session variable.** Policies read `current_setting('app.firm_id')`, set per
  connection with `set local`. The trap: connections are reused, and Cyclone runs
  jobs on a **thread pool** with a manager per job (§11a). A connection that
  keeps a previous request's firm, or a job that never set one, does not raise —
  it returns another firm's rows. This is the same class of failure as the
  contextvar traps already documented in §11: *a value set in a sync dependency
  never reaches the endpoint*, and *a scope set around `pool.submit` does not
  cross into the pool thread*. Both were found by running them, not by reading.

### What it would take

- `firm_id` on ~40 tables, backfilled, indexed, and added to **every unique
  constraint** — `(institution, last4)` becomes `(firm_id, institution, last4)`,
  and the same for the statement period index and the account-number keys.
- Policies on every table, plus the connection strategy above.
- The FastAPI-layer checks kept anyway, as the second layer.
- **Everything firm-wide becomes per-firm:** the seeded chart of accounts (024),
  keyword rules (032), payee and custodian rulings (033, 036), firm-wide tags,
  standard privileges and objections. Each needs a per-firm copy and a seeding
  path for a new firm.
- Storage paths (`matters/{matter_id}/…`) gain a firm segment, or matter ids
  alone remain the namespace across firms.

### Verdict

**Cheapest per firm and the wrong default here.** Its failure mode is a silent
cross-firm disclosure in a system that holds financial discovery, CPS records
and protective-order affidavits, and it would rest on the one layer the backend
currently bypasses. The whole codebase is built to make confident-wrong failures
impossible; this adds one at the centre.

The argument *for* it gets stronger with scale — see §6.

---

## 2. One Docker stack per firm, one Supabase project per firm

The proposal on the table. Same hardware, same images, one compose project per
firm, haproxy routing by hostname, a Supabase project per firm.

### Pros

- **The isolation needs no code to enforce it.** A bug in a `where` clause cannot
  reach another firm, because there is no connection to reach it through.
  Database, Storage and Auth separate in one move.
- **It works with the architecture as it stands** — the changes in §5 are
  configuration and key naming, not a data-model rewrite.
- Per-firm restart, rollback, and image version: a firm can hold back a release.
- A diligence question about tenant isolation has a one-sentence answer.
- No marginal hardware cost, which was the point of the exercise.

### Cons

- **N migration runs, by hand.** This is already the most repeated operational
  risk in the project (CLAUDE.md §15). With N firms it multiplies, and a firm
  that misses one is broken in a way `schema_check` reports only at ERROR in its
  own log.
- N sets of secrets, N mailbox pollers, N healthchecks to watch.
- **Cross-firm cost reporting needs aggregation.** Every firm's database has its
  own `firms` table with `id = 1`, so the Firm grouping added in 043 is correct
  *within* a firm and says nothing across them (see §5.4).
- RAM per firm bounds how many fit on a box. **Unmeasured** — take `docker stats`
  on the current stack before promising a number.
- Per-firm drift in the chart of accounts and rules. Arguably a feature: it is
  that firm's chart.

### Cost

The Supabase project is the line item; $10–25/month per firm depending on how
compute credits fall out in your organisation. No additional EC2 cost until RAM
or vendor rate limits run out.

---

## 3. One schema per firm, one database

`firm_daley.matters`, `firm_smith.matters`, one Postgres database.

- Cheaper than 2, better isolated than 1, and it keeps one migration *file* even
  though it is applied N times.
- **But** PostgREST must be told about each schema explicitly; Supabase **Auth
  and Storage remain single** — one user pool, one bucket namespace — so the
  isolation is partial; and the boundary is a `search_path`, which is a setting
  and can be silently wrong.
- Ends up with most of option 1's fragility and most of option 2's migration
  cost.

## 4. One database per firm inside one Supabase project

Not really supported: a Supabase project is one database, and PostgREST, Auth
and Storage are bound to it. In practice this collapses into option 2. Listed so
nobody re-derives it later.

---

## 5. What options 2–4 require before a second stack can run

Each of these is silent if missed. Verified against the files named.

### 5.1 The compose file cannot start twice as written

`docker-compose.yml` publishes `ports: "8094:80"`. The second stack's frontend
fails to start. Make it `${FRONTEND_PORT:-8094}:80`, which makes `FRONTEND_PORT`
a new setting in the `COMPOSE_ONLY` bucket, on the compose-interpolated list, in
`.env.example`, and in the test that keeps those in agreement (CLAUDE.md §5a).

**What does *not* need changing:** `frontend/nginx.conf` proxies to
`http://api:8000`, a name resolved on the compose project's own network, so two
stacks do not collide there.

### 5.2 Container names come free; set them anyway

No `container_name:` is set, so Docker names containers
`<project>-<service>-<n>` and the project defaults to the **directory name**:

```
/opt/cyclone/daley/     → daley-api-1     daley-worker-1     daley-frontend-1
/opt/cyclone/smithlaw/  → smithlaw-api-1  smithlaw-worker-1  smithlaw-frontend-1
```

`docker compose logs -f api` from a firm's directory is already scoped to that
firm. Set `COMPOSE_PROJECT_NAME=<firm>` in each `.env` regardless: inheriting it
from a directory means renaming the directory renames every container, which is
not a thing to discover during an incident.

`ID` must also become firm-specific (`daley-node1`), since the fleet check
groups nodes by it.

### 5.3 ⚠ Redis keys collide across firms

The sharpest landmine, because it produces a hung-looking import rather than an
error.

- **`job_service.run_pending` claims work with `claim_once("job:%s" % job.id)`**
  and job ids come from a per-database sequence — **every firm has a job 1.**
  The first firm to claim it blocks the other firm's job for the claim TTL
  (`_CLAIM_TTL_SECONDS = 1800`, 30 minutes), repeatedly. The job sits at
  `queued`, nothing fails, nothing is logged.
- **`crm_worker` holds `crm:poller`**, a single lock intended to be fleet-wide.
  Across firms it serialises every firm's mail polling behind whichever tick
  holds it.

Two fixes, not mutually exclusive:

1. **A Redis database index per firm** — `redis://host:6379/3`. The URL already
   supports it, costs nothing, and is the cheapest correct answer today.
2. **Prefix every key with the tenant** (`smithlaw:job:1`). Robust against a
   shared index and the right answer if a central Redis ever serves everyone.

### 5.4 ⚠ The SSM path cannot tell two stacks on one host apart

`secret_source.environment_name()` reads the EC2 instance tag
`cyclone:environment`, and **the tag deliberately beats `CYCLONE_ENV`** — that
rule exists so a typo in one node's `.env` cannot point it at another
environment's secrets. Two stacks on one instance therefore resolve the **same**
path and load the **same** secrets, which is precisely wrong when each firm has
its own Supabase project.

Recommended shape, which keeps the anti-typo property:

- Add a per-stack **`CYCLONE_TENANT`** that *extends* the path rather than
  replacing the environment: `/cyclone/production/smithlaw/`. The machine still
  says where it is; the stack says whose it is.
- **Raise when an SSM load returns zero parameters** while `CYCLONE_SECRETS_SOURCE=ssm`.
  Today a mistyped path loads nothing and the process continues until
  `SupabaseManager` raises on an empty URL — a confusing failure for a
  well-defined mistake. Same argument as the region resolution refusing to guess
  (§5a).

### 5.5 Cost tracking inverts

With a database per firm, `llm_calls.firm_id` is 1 everywhere and the **Firm**
grouping added in 043 reports one row per database. Consolidated spend across
firms then means querying N projects.

If consolidated billing matters commercially — and for reselling to solos it
probably does — the alternative is a **small central database that every stack
writes cost rows to**, which is a different shape from what 039 built. Worth
deciding before there are twelve databases of history to merge.

### 5.6 Per-firm secrets multiply

Each firm needs its own Supabase URL and keys, its own mail credentials, and a
decision on LLM API keys:

- **Shared keys** — one vendor bill, simpler provisioning, and the `llm_calls`
  ledger is what apportions cost between firms.
- **Per-firm keys** — clean attribution and per-firm rate limits, N vendor
  accounts to manage.

Note the existing operational rule holds either way: the `PER_FIRM=Y` tag on SSM
parameters already marks which credentials must change for a new firm
(`docs/operator-guide/06-new-firm.md`).

### 5.7 Everything else that is firm-wide

Not a blocker for option 2 — a separate database *is* the per-firm copy — but it
is the checklist for provisioning a firm: chart of accounts (024), keyword rules
(032), payee and custodian rulings (033, 036), standard privileges and
objections, firm-wide tags, the exhibit caption template, and the
`matter_preferences` table that does not exist yet.

---

## 6. Recommendation

**Option 2, with §5 done first.** The reason is not cost or effort — it is that
option 1's failure mode is a silent cross-firm disclosure enforced by the layer
the backend currently bypasses, and option 2's failure mode is a container that
will not start.

**The honest counterargument is scale.** At forty solo practitioners on one box,
forty hand-run migration sets and forty mail pollers become the dominant cost and
the dominant risk, and shared-schema starts to look worth its danger. The
deciding question is therefore commercial, not technical:

> **How many firms, realistically, and over what period?**

Two to five: option 2, comfortably. Fifty: revisit, and if shared-schema wins,
it should be built with per-request database credentials rather than a session
variable, and with the FastAPI-layer checks kept as a second layer.

**The cheap hedge, worth doing regardless of the decision:** keep putting
`firm_id` on new firm-scoped tables as they are created, the way 039 did for
`llm_calls`. Attribution cannot be backfilled — a row written today without a
firm can never be told which firm it belonged to — and the column costs nothing
while there is one firm.

---

## 7. If option 2 is chosen — the work, in order

1. **`FRONTEND_PORT`** as a compose-interpolated setting (§5.1).
2. **Redis key namespacing** — a database index per firm now, tenant-prefixed
   keys if a shared index is ever needed (§5.3).
3. **`CYCLONE_TENANT`** in the SSM path, plus the zero-parameters guard (§5.4).
4. **`COMPOSE_PROJECT_NAME` and `ID`** per stack, and an haproxy backend per
   firm keyed on hostname (§5.2).
5. **A provisioning runbook**: Supabase project, migrations 001→latest in order,
   storage bucket, SSM parameters under the tenant path, the firm's `firms` row
   renamed, chart of accounts seeded, first admin correlated. This is
   `docs/operator-guide/06-new-firm.md` rewritten for a world where a new firm is
   a new stack.
6. **A decision on consolidated cost reporting** (§5.5) before there is history
   to merge.
7. **Measure** the per-stack RAM and settle how many firms fit on the current
   instance size.

Centralised logging (backlog, CLAUDE.md §16a) becomes more valuable here than it
is today: with one stack per firm, *"which firm was this?"* is a field, and an
operator should not need SSH to answer it. CloudWatch's `awslogs` driver takes a
per-stack tag, which is the natural place for the firm name.

---

## 8. Bring your own API key (BYOK) — notes, nothing built

Tom, 2026-11-20, thinking aloud about charging for Cyclone. Recorded so the
reasoning is not re-derived from scratch.

### The idea

A firm may optionally supply its own LLM vendor keys, stored in SSM under its
tenant path. Per firm, a policy: **fall back to the house key** when the firm's
key fails (out of funds, revoked) **or fail outright**. Why a firm would want it:

- With an auto-replenishing key, the firm pays the vendor directly and we do not
  bill for AI usage at all.
- A firm that has negotiated vendor discounts gets them.

### The apparent complication, and why it mostly is not one

*"A firm may not hold keys for every vendor, or share our Anthropic → OpenAI →
Gemini preference, so it would need its own preference map."*

Under option 2 (a stack per firm, e.g. `koonsfuller.cyclone.jdbot.us`, haproxy
routing to it, its own Supabase project and SSM path), the existing
configuration already handles most of this:

- **Missing vendors need nothing.** `llm_service` already *skips* a candidate
  whose vendor has no key, without calling it (CLAUDE.md §11). A firm holding
  only an OpenAI key starts every chain at OpenAI automatically.
- **A different ORDER is the part that is not free,** and the reason is
  deliberate. Chains live in `app/config/llm_profiles.json` *inside the image*,
  so two nodes on one tag cannot disagree (§11: model identity must be fleet
  configuration, or it produces bugs nobody can reproduce). A per-firm order
  means a per-firm catalog file — `LLM_PROFILES_FILE` already names the path, so
  one mounted per stack would work, and the fleet-hash principle still holds
  *within* a firm. **But think before offering it at all:** chain order is
  *measured* extraction accuracy on evidence, not a taste. A firm that puts
  Gemini first will find its pleadings and affidavits refused (§11), and one
  that adds DeepSeek gets mis-stated amounts. Recommendation: honour missing
  keys (free), do **not** offer reordering; at most a short list of vetted
  alternative chains.

Tom's conclusion was that a stack per firm makes shared multi-firm instances
unnecessary, at least for firms of any size. Agreed — see §6. **The open
commercial question is the one or two lawyer shop:** its floor cost is a
Supabase project ($10–25/month, §2) plus its share of a box, so whether solos are
in the target market is a pricing decision, not an architecture one.

### What would actually have to be built

1. **Two keys per vendor, and a policy.** Firm key first; on a *key* failure
   (auth, quota, insufficient funds) either the house key or a hard failure, per
   the firm's setting. Distinguish key failures from transient ones: a vendor
   timeout on the firm's key should fail over across vendors as today, not spend
   the house key.
2. **The ledger must say whose key paid.** A `key_owner` (`firm` | `house`)
   column on `llm_calls`, written from the first BYOK call. **Attribution cannot
   be backfilled** (§6), and this column *is* the bill: house-key spend priced
   by 041 is what gets invoiced, and the Costs page becomes the billing basis.
3. **A hard failure must say why, in words.** An import that dies because the
   firm's key ran dry must report "your Anthropic key was refused: insufficient
   funds", not a generic extraction failure — the person reading it is the one
   who can top it up.
4. **Validate a key when it is entered**, with one cheap call, rather than
   discovering it is wrong halfway through a 200-statement production.
5. **Who writes the key into SSM.** Ops-run is simplest. Self-service from an
   admin screen means the instance role needs `ssm:PutParameter` on its own
   path, a wider permission than it holds today; scope it to that path only.

### Worth knowing either way

- **BYOK moves the data-processing relationship.** Client evidence sent on the
  firm's key is governed by the *firm's* agreement with the vendor — its
  retention, its zero-data-retention status, its training opt-outs — not ours.
  That is a selling point for a firm with its own enterprise terms, and a risk
  for one using a consumer-tier key. The engagement terms should say which
  applies.
- BYOK and the tenancy decision are independent: per-firm keys are useful under
  option 2 as well, and §5.6 already weighs shared against per-firm keys.
