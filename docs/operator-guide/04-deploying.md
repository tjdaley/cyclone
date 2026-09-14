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

## Roll out

On each node: update the `image:` tags in `docker-compose.yml`, then

```sh
docker compose -f docker-compose.yml up -d
```

`-f docker-compose.yml` is not optional. Compose silently applies
`docker-compose.override.yml` when it is present, and the override is the
**development** configuration.

Then prove it landed:

```sh
scripts/check-fleet.sh https://cyclone.jdbot.us
```

Exit 3 (build skew) is normal *during* the rollout and a problem after it.

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

This release: **`036_value_platforms.sql`** and **`037_brokerage_cash_flow.sql`**.

### 3. Anything compiled into the frontend needs a rebuild, not a restart

`SUPABASE_URL`, `SUPABASE_ANON_KEY` and `API_BASE_URL` are docker **build args**
baked into the JavaScript bundle. Changing one means rebuilding and redeploying
the frontend image — restarting a container does nothing at all.

It is also the failure that hides best: compose substitutes an empty string for
a missing variable, the build succeeds with a warning nobody reads, and the
first symptom is a user who cannot log in. See
[Chapter 1](01-configuration.md#where-each-value-has-to-live).

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
