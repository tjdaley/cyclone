# 5. Reading the startup log

[← Deploying a release](04-deploying.md) · [Index](README.md) · Next: [Onboarding a new firm →](06-new-firm.md)

---

A node says everything it knows about its own configuration at boot, in a few
lines. This chapter is what each one means.

```sh
docker compose -f docker-compose.yml logs --since 5m api
```

> ⚠ **`LOG_LEVEL` is `WARNING` in production, so the `INFO` lines below do not
> appear there.** The warnings and errors do. To see the whole summary, raise
> the level temporarily — or read the same facts from `GET /api/config` and the
> fingerprint endpoint, which do not depend on log level at all.
>
> The secret-loader line is the exception: it is written to stderr before
> logging is configured at all, so it normally appears whatever the level.

---

## The lines you want to see

```text
secret_source: loaded 4 parameter(s) from /cyclone/production/ (8 already set, left alone)
Config fingerprint | id=ec2-54-84-177-70 fleet_hash=7f3a91c2 fields=44
Cyclone API started | env=production log_level=WARNING llm_profiles: ...
```

**"8 already set, left alone" is the number to read.** Those are names that were
already in the environment — from `.env` — so the SSM value was skipped. That is
correct behaviour during a cutover and a **bug in your process** after one: a
rotated credential in Parameter Store is doing nothing while the old value sits
in a file.

**No news about the environment is good news.** When the instance tag resolves,
there is no line saying so. A warning about falling back to `CYCLONE_ENV` means
the tag did not answer — see
[Chapter 2](02-secrets.md#which-environment-a-node-is-in).

**The fingerprint line is how you correlate behaviour with configuration after
the fact.** The hash sits in the log beside the deploy that produced it, so
*"which configuration was this node running on Tuesday"* has an answer that does
not depend on the file still being there.

---

## ⛔ A secret in a slot that reaches a browser

```text
ERROR  SUPABASE_ANON_KEY holds a value beginning sb_secret_ — that is a SECRET key
       in a slot that is compiled into the frontend bundle by the docker build.
       Replace it with the publishable credential and redeploy; treat the secret
       as disclosed and revoke it.
```

**Act on this today.** Two slots are checked, and both are one paste away from
holding their secret twin — the names differ by a word and the values differ by
a prefix:

| Variable | Should hold | Where its value goes |
| -------- | ----------- | -------------------- |
| `SUPABASE_ANON_KEY` | `sb_publishable_...` | Compiled into the JavaScript bundle |
| `STRIPE_PUBLISHABLE_KEY` | `pk_live_...` / `pk_test_...` | Served from the unauthenticated `GET /api/config` |

This is not hypothetical — a secret key was found sitting in a Supabase
landing-pages anon variable, where it happened to be harmless because nothing
read it. In these two, the same mistake ships the credential to every visitor.

The process does **not** refuse to boot over it: taking the site down would not
undo a disclosure that already happened on the previous deploy, and an error in
the log plus a flag on the fingerprint endpoint gets it fixed within the hour
without a second outage.

Rotate the key. Then fix the variable and redeploy — a rebuild, for the anon key
([Chapter 4](04-deploying.md)).

---

## Legacy Supabase keys, with a countdown

```text
WARNING  SUPABASE_SERVICE_ROLE_KEY is a legacy Supabase JWT key, retired by
         Supabase on 2026-12-31 (109 days). Replace the VALUE with a sb_secret_
         key — same variable, no code change — and gain per-key revocation.
```

Detected by the **format of the key itself** rather than by a flag somebody has
to remember to set, so a node that was missed reports itself. It becomes an
`ERROR` the day it is overdue.

What matters about it:

- **The migration is a change of value, not a rename.** The new key goes into
  the same variable. No code changes.
- **There are two Supabase projects** to do — Cyclone's own and the
  landing-pages one.
- **The frontend must be rebuilt**, because the anon key is a build arg.
- New-format keys are `sb_publishable_...` and `sb_secret_...`, and both systems
  run in parallel, so the swap is reversible.
- The real prize is not beating the deadline: **secret keys are revocable
  individually**, where rotating `service_role` today means rotating the project
  JWT secret and logging every user out.
- The keys are under **Settings → API Keys** in the Supabase dashboard. There is
  no longer a *Settings → API* page, and this is genuinely hard to find.

Full procedure: [`docs/supabase-key-migration.md`](../supabase-key-migration.md).

---

## Deprecated settings still in use

```text
WARNING  Deprecated setting in use: SUPABASE_KEY (superseded by
         supabase_service_role_key). Remove the line from .env on every node;
         the field is deleted in a later release and startup will then fail
         while it is still present.
```

**"In use" means somebody set it**, not that it holds a value — two of these
carry non-empty defaults, and testing the value would have reported them on
every node forever. A warning that is always on is a warning nobody reads.

Strike the line from `.env` on every node. When no node reports any, the fields
can be deleted from the code — which is why the warning exists at all
([Chapter 1](01-configuration.md#removing-a-setting-takes-two-releases)).

---

## A setting whose default names this machine

```text
WARNING  REDIS_URL is not set on this node and fell back to
         redis://localhost:6379/0, which names THIS machine. Nothing here is
         development, so that is almost certainly not what was wanted: set it in
         .env or in SSM under this environment's path. A Redis at localhost
         means every node believes it holds the fleet-wide poller lock.
```

Raised for any per-node setting that was never set and whose default points at
the node itself — `REDIS_URL`, `HOST_URL`, `ID`. **Suppressed in development**,
where those defaults are exactly right, and asked of *whether somebody set it*
rather than of the value, so a deliberate choice is never reported.

**On Redis it is the serious one.** The mail-poller lock is what makes polling
fleet-wide single-runner; at a localhost default every node holds its own lock,
so **every node polls the mailbox**. Set it in `.env` or in SSM under this
environment's path.

> **Related, and the reason a dev box can go slow:** `REDIS_TIMEOUT_SECONDS`
> (new, fleet-wide, default `3.0`) bounds connecting *and* reading. It bounds a
> **hang**, not a refusal — a refused connection already fails in about two
> seconds and never reaches the cap. What it fixes is a host that accepts a
> connection and then goes quiet, which used to stall the poller tick
> indefinitely **while holding the fleet-wide lock**.
>
> If a developer's test suite suddenly crawls, this is usually why: `REDIS_URL`
> now lives in SSM, a laptop has no SSM, and the fallback waits on every call.
> Measured 74.7s for one suite against 8.9s once bounded. Put `REDIS_URL` back
> in the **dev** `.env`. Note also that `localhost` resolves to both `::1` and
> `127.0.0.1` and both are tried, so an unreachable `localhost` costs exactly
> twice what `127.0.0.1` costs — 4.07s against 2.04s, measured.

---

## The usage ledger has something to say

These three come from cost recording (migrations 039 and 042). **Running the
migration is never enough on its own — restart api and worker**, because each
process decides once, at the point of failure, and keeps that decision for its
lifetime.

```text
ERROR  llm_usage: the llm_calls table does not exist. Run
       db/migrations/039_cost_tracking.sql. LLM usage is NOT being recorded by
       this process until it restarts.
```

**Recording is off for the life of that process.** Spend between now and the
restart is gone — it is not backfilled from anywhere, because nothing else holds
the token counts. Run `039`, restart api **and** worker.

```text
ERROR  llm_usage: llm_calls has no served_model column. Run
       db/migrations/042_served_model.sql, then restart api and worker. Until
       this process restarts, usage is recorded WITHOUT the model the vendor
       actually served, and is priced on the model requested.
```

**`046_stop_reason.sql` reports itself the same way**, naming its own columns:
usage keeps being recorded without the stop reason until the process restarts.

Said once. **Usage is still recorded** — this is the smaller loss of the two.
The rows written meanwhile are priced on the model that was *requested* rather
than the one that answered, permanently, because nothing recorded which
answered. The same missing migration also shows up as
`ERROR Database schema: … llm_calls … served_model / vendor_response_id`.

```text
WARNING  llm_usage: could not write a cost record (APIError); 10 consecutive
         failure(s)
WARNING  llm_usage: ledger queue full; 250 cost record(s) dropped
```

The first is reported at failures 1, 10, 100 and then every 1000 — **each
failing row is lost** once the database client's own retries are spent. The
second means the database has been unreachable long enough for ten thousand rows
to pile up, and spend is now being dropped. Both point at database
connectivity; the second is urgent if it persists.

> Recovery reports itself too: `llm_usage: ledger writes recovered after N
> failure(s)`, at INFO.

---

## A write the database refused

Not a startup line — this one arrives during ordinary work, and it is here
because the response is the same: run a migration.

```text
ERROR  Database rejected a write: check constraint, sqlstate=23514,
       path=/api/v1/financial-accounts/41. The offending row is deliberately
       not logged (it is client data).
```

**Almost always a migration that has not been run.** Somebody picked an option
the screen offers and the database does not accept yet — the first instance was
choosing **Life insurance** before `038`. The user saw a sentence saying exactly
that, so they may quote it to you.

**The row is deliberately absent from the log**, and that is the second reason
this handler exists: unhandled, the database client prints the offending row
into the traceback, and for a financial account that is a client name and a full
account number. Constraint names are safe to log; rows are not.

---

## Lines from the rest of the boot sequence

| Line | Means |
| ---- | ----- |
| `WARNING ... llm_profiles ...` | A model profile names an unknown vendor, a vendor with no API key, or a non-vision model in a vision profile. The chain will fail over past it silently, so fix it |
| `ERROR Database schema: ...` | A model field has no matching column. Those requests **cannot succeed** — a migration was missed. See [Chapter 4](04-deploying.md#2-migrations-are-run-by-hand-in-numeric-order) |
| `SecretSourceError` and the process exits | SSM was configured and could not be read. The node has **no** credentials rather than the wrong ones, which is why it refuses to start. Check the region, the instance role, and the metadata options ([Chapter 2](02-secrets.md)) |

---

[← Deploying a release](04-deploying.md) · [Index](README.md) · Next: [Onboarding a new firm →](06-new-firm.md)
