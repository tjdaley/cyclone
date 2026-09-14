# Supabase key migration — legacy JWT to `sb_secret_` / `sb_publishable_`

**Deadline: end of 2026.** Supabase retires the legacy `anon` and `service_role`
keys then. Cyclone uses both, in the API, the worker and the frontend bundle.

The reason to do it early is not the deadline. **Secret keys are created and
revoked individually.** Rotating `service_role` today means rotating the
project's JWT secret, which also changes the anon key and invalidates every
active user session — which is why the September rotation logged everybody out.
After this migration, revoking one key is one click and affects nothing else.

---

## Where the keys are

**Settings → API Keys.** There used to be a separate *Settings → API* page and
there is not any more; every key, legacy and new, is on this one page.

```
https://supabase.com/dashboard/project/<project-ref>/settings/api-keys
```

## ⚠ There are TWO projects

Easy to do the first and forget the second, and the second fails quietly — the
CRM poller stops seeing leads, and nothing else breaks.

| Project | Variables it supplies |
| ------- | --------------------- |
| **Cyclone** | `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_ANON_KEY` |
| **Landing pages** | `SUPABASE_LANDING_PAGES_SERVICE_ROLE_KEY` |

To find out which keys are still legacy before you start, ask a running node —
the count is on `/api/config`, and the names are on the authenticated endpoint:

```sh
curl -s https://cyclone.jdbot.us/api/config | \
  sed -n 's/.*"legacy_supabase_keys":\([0-9]*\).*/legacy keys: \1/p'

# Which ones, by name (needs a bearer token):
curl -s -H "Authorization: Bearer $TOKEN" \
  https://cyclone.jdbot.us/api/v1/config/fingerprint | \
  python -m json.tool | grep -A6 legacy_supabase_keys
```

---

## The three things that go wrong

### 1. The variable names do NOT change

This is a change of **value**, not a rename. `db_handler`'s `SupabaseManager`
reads `os.environ["SUPABASE_SERVICE_ROLE_KEY"]` itself and is a third-party
package, so the new `sb_secret_...` string goes into the variable that is
already there. Nothing in this codebase parses an API key as a JWT — the only
`jwt.decode` verifies *user* tokens against JWKS — so no code changes at all.

| Variable | Old value | New value |
| -------- | --------- | --------- |
| `SUPABASE_SERVICE_ROLE_KEY` | `eyJ...` | `sb_secret_...` |
| `SUPABASE_ANON_KEY` | `eyJ...` | `sb_publishable_...` |
| `SUPABASE_LANDING_PAGES_SERVICE_ROLE_KEY` | `eyJ...` | `sb_secret_...` |

### 2. The frontend must be REBUILT, not restarted

`SUPABASE_ANON_KEY` is a **docker build arg**, compiled into the JavaScript
bundle:

```yaml
frontend:
  build:
    args:
      VITE_SUPABASE_ANON_KEY: ${SUPABASE_ANON_KEY}
```

A `docker compose up -d` that reuses the existing image ships the old key, and
the failure looks like "login is broken" rather than like a key problem.

### 3. Disabling the legacy keys is a separate, later step

Creating the new keys does not revoke the old ones. **Both systems run in
parallel**, which is exactly what makes this reversible — so create, swap,
verify, and only then disable. Legacy JWT keys can be *deactivated*
reversibly; a secret key can be permanently deleted and that cannot be undone.

---

## The swap

**1. Create the keys.** Settings → API Keys on each project. One
`sb_secret_...` per project, and one `sb_publishable_...` on Cyclone. Nothing
breaks at this point: the legacy keys keep working.

**2. Update `.env` on every node.** Same variable names, new values. Both boxes,
or the fleet hash will tell you about it in step 4 — which is the intended
outcome, but it is quicker to just do both now.

**3. Rebuild and redeploy all three images.**

```sh
docker compose -f docker-compose.yml build --no-cache api worker frontend
docker compose -f docker-compose.yml up -d
```

The frontend is the one that must be rebuilt; the API and worker only need the
new environment, but rebuilding all three keeps the image tags coherent.

**4. Verify.**

```sh
sh scripts/check-fleet.sh https://cyclone.jdbot.us
```

Two things to look for:

- The **NOTES column should be empty** — no `legacy Supabase key(s)`. Detection
  is on the key format, so a node that was missed reports itself.
- Both nodes report the **same hash**, as before.

Then exercise the parts that use each key: log in (anon/publishable), open a
matter (service role), and confirm the CRM poller still sees leads (landing
pages). The last one is the one that fails silently.

**5. Disable the legacy keys.** Settings → API Keys, on both projects.
Deactivation is reversible for legacy JWT keys, so this is safe to do once step
4 is clean.

---

## Rollback

Put the legacy value back in `.env` and rebuild. Both key systems run in
parallel until you disable the old ones in step 5, so until that point rollback
is a value change and a deploy — no Supabase-side action at all.

---

## After

`legacy_supabase_keys` on `/api/config` goes to `0` and stays there. Startup
stops logging the countdown. And rotating any Supabase credential becomes a
per-key operation that logs nobody out — which is the actual point.
