# 1. How configuration is put together

[← Index](README.md) · Next: [Secrets from AWS Parameter Store →](02-secrets.md)

---

Every setting Cyclone reads is classified, in `app/util/config_spec.py`, into
exactly one of six buckets. That file is the authority, and the test suite fails
if a setting is added without a decision about which bucket it is in.

The classification is not bookkeeping. It is what makes the question *"do these
two servers agree?"* answerable at all — because **some settings are supposed to
differ**, and a check that compared everything would report drift forever and
therefore report it never.

## The six buckets

| Bucket | What it is | In the fleet hash? |
| ------ | ---------- | :----------------: |
| **FLEET** | Must be identical on every node in an environment: model sampling, poll intervals, timeouts, log level | ✅ |
| **SECRET** | The twelve credentials. Hashed as a digest of the value, never the value itself | ✅ |
| **PER-FIRM SETTING** | `FIRM_NAME`, intake dropdowns, billing increments — becomes a `firms` row when a second firm is real | ✅ |
| **INFRASTRUCTURE** | Legitimately differs per node: `ID`, `HOST_URL`, the Supabase and Redis URLs, the secret-loader switches, `AWS_REGION` | ❌ excluded, reported beside it |
| **COMPOSE-ONLY** | Read by `docker-compose.yml`, never by the application — `SUPABASE_ANON_KEY`, `API_BASE_URL` | ❌ |
| **DEPRECATED** | Accepted, warned about at startup, and due for deletion | ❌ |

**FLEET is where silent drift lives.** A node running `LLM_TEMPERATURE=0.3`
against a fleet running `0.1` produces different evidence and nothing anywhere
says so. That bucket is what the fingerprint exists for.

---

## ⚠ "Per-firm" means two unrelated things

They share **no** members, and confusing them means somebody provisioning a new
firm changes the wrong set of values.

| | **Per-firm setting** | **Per-firm credential** |
| - | -------------------- | ----------------------- |
| Applies to | A `Settings` field | An SSM parameter |
| Lives in | The node's `.env` | AWS Parameter Store |
| Members | `FIRM_NAME`, `REFERRAL_TYPES`, `TIME_INCREMENT_OPTIONS`, `DEFAULT_REFRESH_TRIGGER_PCT`, `STRIPE_PUBLISHABLE_KEY` | A subset of the twelve secrets, tagged `PER_FIRM=Y` in AWS |
| Means | "This becomes a row in a `firms` table when there is a second firm" | "This credential **must** be swapped for a new firm" |

Only secrets are ever written to SSM, and no per-firm *setting* is a secret — so
the two lists cannot overlap. [Chapter 6](06-new-firm.md) needs both of them,
and says which is which every time.

> **An untagged SSM parameter is a decision, not an oversight.** The
> `PER_FIRM=Y` tag was applied only to credentials that *must* change for a new
> firm. A parameter without it — a shared LLM key, say — is one a second firm
> may reasonably inherit.

---

## Which layer wins

**Highest wins, and this is the single most important thing to hold in your
head:**

```text
1. The process environment     ← .env, injected by compose and mounted at /app/.env
2. SSM parameters              ← fill only names NOT already set
3. Settings defaults           ← compiled into the image
```

**SSM never overwrites something already set.** The loader skips any name
already in the environment and reports the count:

```text
secret_source: loaded 4 parameter(s) from /cyclone/production/ (8 already set, left alone)
```

Three consequences follow, and they are the reason the design is shaped this
way:

- **Turning the loader on changes nothing** until a line is deleted from `.env`.
- **Rollback is uncommenting a line in `.env` and recreating the containers.**
  No AWS change and no deploy. That is the emergency procedure.
- **A stale `.env` line silently beats a rotated SSM value.** Rotate a key in
  Parameter Store while the old value is still in `.env`, and the node keeps
  using the old one — reporting it only as a number in that "already set" count.
  **This is the most likely operator mistake with this design.**

### In development

A checkout has the whole repo, so `docker-compose.override.yml` layers the
committed files underneath. **Later wins:**

```text
config/fleet.env  →  config/environments/dev.env  →  .env
```

### In production

The servers have **no checkout**. They carry `.env`, `docker-compose.yml`, and
whatever scripts were copied across by hand; images come from ghcr and nothing
is built there.

`env_file` is read by compose *on the host*, so listing `config/fleet.env` in
the production compose file would stop `docker compose up` outright on a server
that does not have it — and copying a third file to each box would add a drift
surface rather than remove one.

So production layering is **image defaults → SSM → `.env`**, and the fleet layer
is solved twice over without a file anybody hand-copies:

- Non-secret defaults live in `Settings` and ship **inside the image**, so two
  nodes on the same tag cannot disagree about `LLM_TEMPERATURE`.
- Anything that must genuinely vary can go in SSM — the loader copies **every**
  parameter under the path into the environment, not only the ones it counts as
  secrets. (The seeding script writes only secrets, deliberately.)

> **`config/environments/production.env` is not wired into the production
> compose file**, so editing it changes nothing on a running node today. `config/`
> is for local development and as the written record of which setting belongs in
> which layer. Anything a production node must actually read goes in its `.env`
> or in SSM.

---

## Where each value has to live

### Must stay in `.env` — SSM cannot supply it

**The division is build time against runtime, not one node against another**,
and saying only "keep it in `.env`" is what invited the outage below.

| | In `.env` on | Why |
| - | ------------ | --- |
| `ID` | **every node** | Genuinely per-node. It is how a report traces back to a machine |
| `CYCLONE_SECRETS_SOURCE` | **every node** | Chicken and egg — it is the switch that turns the loader on |
| `AWS_REGION` | **every node**, optional where instance metadata answers | Bootstrap, before any SSM call can be made |
| `SUPABASE_URL` | **the machine that runs `docker compose build`** | compose build arg |
| `SUPABASE_ANON_KEY` | **the machine that runs `docker compose build`** | compose build arg |
| `API_BASE_URL` | **nowhere** | compose gives it a `:-` default |

So a production node's `.env` legitimately holds `ID`,
`CYCLONE_SECRETS_SOURCE`, `AWS_REGION` and optionally `CYCLONE_ENV` — nothing
else. `SUPABASE_URL`, `REDIS_URL` and `HOST_URL` can all come from SSM, because
the API reads them at runtime inside the container. Keeping the two build args
in a node's `.env` anyway is cheap insurance against somebody one day building
on a server.

**The build args are the trap.** `docker-compose.yml` substitutes them on the
host, before any container exists, to build the frontend bundle:

```yaml
VITE_SUPABASE_URL: ${SUPABASE_URL}
VITE_SUPABASE_ANON_KEY: ${SUPABASE_ANON_KEY}
VITE_API_BASE_URL: ${API_BASE_URL:-}
```

The secret loader runs *inside* the container and cannot possibly supply them.
Compose substitutes an **empty string** for a name it cannot find, prints one
"variable is not set" line, and exits 0.

#### What that actually looks like, because it shipped

`SUPABASE_URL` was removed from the **build machine's** `.env` during the SSM
migration — correct as far as the API was concerned, which reads it at runtime
and stayed healthy throughout.

- The next build compiled an empty string into the bundle.
- **Every route served a blank white page.** `createClient("")` throws
  `supabaseUrl is required.` at module scope, before React mounts — so there is
  no login screen to fail at, which is what earlier versions of this chapter
  said.
- The build exited 0. The container was healthy. nginx answered `GET / → 304`.
  The fleet hash was clean.
- **No server log anywhere carried a line about it**, because the app died
  before making its first request.
- The only evidence was four words in the **browser console**.

> **Troubleshooting: a blank white page after a deploy.** Open the browser
> console. `supabaseUrl is required` means the build machine's `.env` was
> missing a build arg. Fix it there, **rebuild**, and redeploy the frontend
> image. Restarting a container does nothing — the empty string is compiled in.

**Three guards now exist, and they are backstops for the rule, not the rule:**

1. **`deploy-version.bat` refuses to build** without both names. This is the
   only moment the problem can be caught, because it is created at build time
   and every later check passes.
2. **The runtime guard keys on the names compose interpolates**, not on a
   bucket. It used to key on `COMPOSE_ONLY`, and `SUPABASE_URL` is
   `INFRASTRUCTURE` — that is exactly the size of the hole this fell through.
3. **A test fails** if that list stops matching `docker-compose.yml`.

> `SUPABASE_SERVICE_ROLE_KEY` is perfectly fine in SSM. Only its `ANON` sibling
> is a build arg. This is easy to get backwards.

### Should stay in `.env`, though not strictly required

- **`CYCLONE_ENV`** — the fallback that names the environment. See
  [Chapter 2](02-secrets.md#which-environment-a-node-is-in). It costs nothing and
  prevents an outage.

### Belongs in SSM

All twelve credentials. `scripts/seed_ssm.py` writes only those names, and
reports "orphans" — parameters in the path that are not credentials — so
anything created by hand is visible.

### Deliberately **not** in SSM

Fleet and per-firm settings. Their defaults already ship inside the image, so
two nodes on one tag structurally cannot disagree. Putting them in SSM would add
a place to look without removing one, and turn a value readable in a `diff` into
one that needs AWS access.

*"Centralise everything"* is the obvious instinct, and it is the wrong one here.

---

## ⚠ The upload limit is three numbers, and only two are ours

`MAX_UPLOAD_MB` is a `Settings` field but it is **not a knob**, and the
difference is invisible from inside the application.

| Where | What it does | Who can change it |
| ----- | ------------ | ----------------- |
| `client_max_body_size` in `frontend/nginx.conf` | **Enforces** the limit for anything POSTed through the API | Edit the file, rebuild the frontend image |
| `settings.max_upload_mb` | What the API **tells the browser**, so a file can be refused before it is sent | Edit the default, rebuild the API |
| Supabase Storage's own cap | **Enforces** the limit for the direct upload, which the browser PUTs straight to storage | Storage project settings. Defaults to **50 MB** |

The first two are pinned to each other by `tests/test_upload_limit.py`, because
a mismatch is invisible from either side: too high and the browser accepts a
file nginx then refuses with a raw error page; too low and uploads fail for no
stated reason.

**The third cannot be tested** — it is a third party's setting behind a network
call — so raise it to at least `max_upload_mb`, or lower `max_upload_mb` to
match it. Until they agree, a file between the two passes the browser's check
and is refused by storage. The error says which of them refused it, in those
words, because a refusal at storage never appears in any Cyclone log.

**Setting `MAX_UPLOAD_MB` in an env file does not work**, and the application
says so at startup:

```
MAX_UPLOAD_MB is set to 512, but the limit is enforced by nginx in the
frontend image and no environment variable changes it...
```

Nginx cannot read an environment variable here, so raising the setting alone
makes the browser stop pre-checking files that storage and nginx still reject.
To change the limit for real, edit **both files** and rebuild the frontend.

---

## ⚠ The worker is silent at the default log level

`LOG_LEVEL` defaults to **WARNING**, and every line describing what an ingest is
doing — which file, how many pages, how many needed OCR, which statements were
committed — is **INFO**.

So `docker compose logs worker` on a job that is running correctly shows
nothing at all, and a job that failed shows one line with no context. When
diagnosing an import, set `LOG_LEVEL=INFO` on the worker and restart it.

Leave it at WARNING the rest of the time: §6 of the conventions forbids client
names, amounts and case facts in logs, and INFO is where the volume is.

---

## Cache headers, and a deploy that looks like it did not take

`frontend/nginx.conf` sets `Cache-Control` explicitly, and it has to.

Without it a browser applies **heuristic caching** — roughly a tenth of the age
since the file was last modified — and decides for itself how long to keep
`index.html`. The bundles are content-hashed, so a new build gives them new
names; `index.html` is not, and it is the file that **names** the hashed
bundle. A stale copy of it asks for the previous bundle, which the new image no
longer contains: the deploy then serves either the old application or a blank
page, while every server-side check passes.

| Path | Header | Why |
| ---- | ------ | --- |
| `/assets/` | `immutable, max-age=1y` | The name changes when the content does |
| `index.html` and SPA routes | `no-cache` | It names the hashed bundle |
| `/user-guide/`, `/client-guide/` | `no-cache` | Markdown fetched at runtime; the names never change, so a corrected chapter would never appear |

`no-cache` does not mean "do not cache" — it means revalidate before use, which
with nginx's ETag is a 304 carrying no body. One conditional request per page
load, in exchange for a deploy that is actually deployed.

**Bumping the image tag does not fix this.** The server had the new files; the
browser never asked for them. After deploying, check:

```
curl -I https://<host>/ | grep -i cache-control
```

---

## Image transformation on the storage project

Supabase enables it automatically. It cannot affect anything today, for two
independent reasons, **and both expire the moment images are ever stored**:

1. PDF is not a transformable source, and every upload route refuses anything
   else.
2. A transformation happens only when a caller asks for one. Nothing does.

The risk is ahead: converting photographs and TIFFs to PDF is planned, and the
original of such a file — a JPEG, a HEIC, a TIFF — **is** a transformable
source. Serving one through a transform returns a re-encoded image with its
metadata stripped, and it looks like the photograph. Capture time and GPS are
routinely the evidence.

Turning the feature off in the storage project settings costs nothing while no
image is ever transformed, and is stronger than a convention about who calls
what.

---

## Removing a setting takes two releases

`Settings` uses `extra = "forbid"`: a variable in `.env` that the application
does not declare is **refused**, and the node does not start. That is a drift
guard worth keeping — a typo is caught rather than silently ignored — but it
makes deleting a setting a coordinated change.

So a dead setting is first **declared deprecated**, which makes startup warn and
name the line to strike ([Chapter 5](05-startup-log.md)). The field is deleted in
a later release, once the fleet reports zero deprecated settings in use.

Delete it early and any node whose `.env` still carries the line stops booting.

---

[← Index](README.md) · Next: [Secrets from AWS Parameter Store →](02-secrets.md)
