# 4. Deploying a release

[← Proving the nodes agree](03-fleet-checks.md) · [Index](README.md) · Next: [Reading the startup log →](05-startup-log.md)

---

Images are built on a workstation and pushed to ghcr. **The production servers
never build anything** — they pull a tagged image.

## Build and push

```bat
deploy-version.bat
```

One variable inside it drives **both** the image tag and the build stamped
inside the image:

```bat
set CYCLONE_BUILD=1.0.4
```

Two literals would eventually disagree, and the failure mode is an image that
reports a version it is not — worse than reporting nothing, because the fleet
check would then confirm an agreement that does not exist.

Edit that one line per release.

**It refuses to build** when `SUPABASE_URL` or `SUPABASE_ANON_KEY` is missing
from **this machine's** `.env`. That is not a formality: those two are compiled
into the JavaScript bundle, and the build machine is the only place they are
read. Without them compose substitutes an empty string, the build succeeds, and
the deployed app is a blank white page on every route
([Chapter 1](01-configuration.md#where-each-value-has-to-live)).

## Roll out

On each node: update the `image:` tags in `docker-compose.yml`, then

```sh
docker compose -f docker-compose.yml up -d
```

`-f docker-compose.yml` is not optional. Compose silently applies
`docker-compose.override.yml` when it is present, and the override is the
**development** configuration.

**Rebuild and redeploy api, worker and frontend together** unless you are
certain a change touches only one. The usage ledger is written by the api *and*
the worker, and every new screen is frontend code.

Then prove it landed:

```sh
scripts/check-fleet.sh https://cyclone.jdbot.us
```

Exit 3 (build skew) is normal *during* the rollout and a problem after it.

**Then open the app in a browser.** A frontend built without its build args
passes every check a server can make — healthy container, 200 from nginx, clean
fleet hash — and is a blank page to every user. Nothing but a browser will tell
you.

---

## The three things that go wrong

### 1. The worker is the same image, and it is a separate service

```yaml
api:     ghcr.io/tjdaley/jdbot-cyclone-api:1.0.4
worker:  ghcr.io/tjdaley/jdbot-cyclone-api:1.0.4   # same image, different entrypoint
```

**Backend changes need both recreated.** Shipping only the API leaves jobs
queued with nothing running them — statement imports sit at "queued" forever and
nothing in the API log says why. This is the most repeated deployment mistake in
this project.

### 2. Migrations are run by hand, in numeric order

`db/migrations/run_all.sql` covers `001`–`005` only. Everything after it is run
by hand, and **release notes must name the migration files** so they can be
applied before the containers come up.

**This release: `038` through `045`, in order.** All eight are safe to run
before the deploy, and that is the right order — a migration run late costs data
that was never recorded.

| File | What it does | Run it late and… |
| ---- | ------------ | ---------------- |
| `038_insurance_accounts.sql` | Adds `life_insurance` and `annuity` as account types | Choosing either is refused with a sentence naming the constraint (§[5](05-startup-log.md)) |
| `039_cost_tracking.sql` | The `firms` row and the `llm_calls` usage ledger | **Spend before it is gone.** Recording also switches itself off for the life of each process that started without the table, so run it, then restart api and worker |
| `040_enable_rls.sql` | Row-Level Security on every table lacking it; views closed to the public key | See [below](#row-level-security-on-every-new-table-and-view) |
| `041_service_rates.sql` | The price table, pricing views and report function | The Costs page has no prices to show |
| `042_served_model.sql` | Records which model actually answered, and prices on that | Rows written meanwhile are priced on the *requested* model, permanently |
| `043_cost_report_by_firm.sql` | Adds **Firm** to the Costs page's groupings | That grouping is missing |
| `044_client_portal_tagging.sql` | Client tags, cleared tags, client-visible tags, and the per-matter tagging lock. Seeds seven client-facing waste tags | The client portal's tagging cannot be switched on. Applied and re-applied against real Postgres 16 |
| `045_no_marital_interest.sql` | A fifth account characterization: *no marital property interest* | Choosing it is refused with a constraint sentence ([Chapter 5](05-startup-log.md#a-write-the-database-refused)) |

Two things about this set that are not in the files:

- **⚠ Re-running `041` after `042` fails, deliberately.** It runs in one
  transaction, so nothing changes. The failure exists to stop `041` quietly
  switching pricing back to the model that was *requested* rather than the one
  that answered. If `041` ever has to be re-applied, run `042` again right after.
- **Rename the firm once, by hand.** `039` seeds `'Default firm'` because a
  migration cannot read `FIRM_NAME`:

  ```sql
  update firms set name = '<firm name>' where id = 1;
  ```

### 3. Anything compiled into the frontend needs a rebuild, not a restart

`SUPABASE_URL`, `SUPABASE_ANON_KEY` and `API_BASE_URL` are docker **build args**
baked into the JavaScript bundle. Changing one means rebuilding and redeploying
the frontend image — restarting a container does nothing at all.

It is also the failure that hides best: compose substitutes an empty string for
a missing variable, the build succeeds with a warning nobody reads, and **every
route serves a blank white page** — there is no login screen to fail at. See
[Chapter 1](01-configuration.md#where-each-value-has-to-live).

---

## The client portal changes who can reach the application

This release lets a **client** sign in. Two things an operator should know:

- **A client sees the whole matter**, including the other side's produced
  accounts. That is intended, and it is the first question anybody asks.
- **It is off per matter until somebody turns it on**, and the client needs the
  `client` role and a client record before that. Deploying the code alone
  exposes nothing.

Nothing about hosting, secrets or the fleet changes: the portal is served by the
same image, on the same routes, behind the same authentication.

---

## Row-Level Security on every new table and view

**Supabase grants the public `anon` key access to a new table by default, and
that key is compiled into the frontend bundle.** Anything it can read is
readable by anyone who opens the app. A **view** is worse: it runs with its
owner's privileges, so it reads *through* RLS even on tables that are protected.
On this deployment the tables turned out to be covered — switched on in the
dashboard, never recorded in a migration — and **two views were the real
exposure**.

`040` closes both, finding tables and views from the database catalog so it also
covers anything created by hand. It adds no policies, which is safe: the backend
uses the service role and bypasses RLS, and the frontend never queries tables.

**The standing rule, for every migration from here on:**

- enable RLS on every new **table**;
- create every new **view** with `security_invoker = on`;
- `revoke execute … from public, anon, authenticated` on every new **function**.

`tests/test_migration_rls.py` enforces it for anything numbered after 040.

**Check it**, in the Supabase SQL editor:

```sql
select c.relname, c.relkind, c.relrowsecurity,
       has_table_privilege('anon', c.oid, 'SELECT') as anon_can_select
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
 where n.nspname = 'public' and c.relkind in ('r', 'v')
 order by anon_can_select desc, c.relname;
```

Every **view** should report `anon_can_select = false`. A view always reports
`relrowsecurity = false` — that column means nothing for a view, so read the
privilege column instead. **Fix by running `040`**, which is idempotent.

---

## Build identity

`CYCLONE_BUILD` is baked into the image by `app/Dockerfile` and reported at
`GET /api/config` beside the fleet hash. An image built without it reports
`unstamped`, which the fleet check treats as a fact rather than as agreement.

**It comes from the build, never from configuration.** A value in `.env` could
be set wrong, and a node claiming a build it is not running is worse than one
saying nothing.

**It is reported beside the fleet hash and never inside it**, for two reasons:

- Folding it in would make every release read as configuration drift.
- It would destroy the control-group technique
  ([Chapter 2](02-secrets.md#the-control-group-technique)), which works only
  while the hash stays comparable across builds.

> **Worth doing: bump `settings.version` per release.** It *is* hashed, so a node
> running an older image reports a different fleet hash and the drift check
> doubles as a deploy check. Today it reads `2026.04.05` while images are tagged
> `1.0.4`, so it currently contributes nothing.

---

## After a deploy, read the log once

Every node prints its fingerprint, its dead settings, and any credential sitting
in the wrong slot at startup. It takes ten seconds and it is the only moment
those lines are guaranteed to be read: [Chapter 5](05-startup-log.md).

---

[← Proving the nodes agree](03-fleet-checks.md) · [Index](README.md) · Next: [Reading the startup log →](05-startup-log.md)
