# Configuration & secrets hardening — working notes

**Status:** items 1-6 BUILT 2026-09-13. See "What shipped" at the foot.
Item 7 (`firms` table) deliberately not started.

The question started as "should we move `.env` to Supabase" and turned into a
configuration-management question when Tom found drift between two EC2 servers'
`.env` files. *"It was not consequential, but whether it's consequential is a
matter of luck, not design."* Three motivations, in the order they surfaced:

1. Harden the secret surface.
2. **Resist config drift across servers** — the real driver.
3. Be ready for multi-firm deployment, and tell a good story to potential
   partners/acquirers.

---

## Verified facts (checked against the repo, not assumed)

| Claim | Finding |
| --- | --- |
| `.env` is gitignored and untracked | ✅ `.gitignore:141`; `git ls-files` finds nothing |
| `SUPABASE_JWT_SECRET` is unused | ✅ appears **nowhere** in `app/` — matches CLAUDE.md §5 |
| `SUPABASE_ANON_JWT_SECRET` is unused | ✅ same — appears nowhere in `app/` |
| Anything parses an API key as a JWT? | ❌ No. The only `jwt.decode` is `auth_middleware.py:123`, decoding **user** tokens via JWKS |
| How the service-role key is used | Opaque string: `apikey` / `Bearer` headers in `schema_check.py:50-51`, passed through in `dependencies.py:50` |
| Frontend key use | `supabaseClient.ts` → `createClient(url, anonKey)` — accepts publishable keys |
| `.env` size | 48 keys; roughly 15 are actually secrets |
| How `.env` reaches containers | `docker-compose.yml`: **both** `env_file: .env` *and* mounted `./.env:/app/.env:ro` |
| `/api/config` today | Unauthenticated; already returns `id` (which server answered) and `version`, plus `firm_name`, `time_increment_options`, `referral_types`, `stripe_publishable_key` |

---

## Conclusions on the two Supabase options

**Edge Function secrets** (`/guides/functions/secrets`) — **does not apply.**
Secrets live on Supabase infrastructure and are readable only by
`Deno.env.get()` inside an Edge Function. The API and worker are Python
containers on our own host. The halfway version (Management API) is *worse*:
the token needed to read secrets is itself a secret in `.env`, it is
account-scoped enough to drop the database, and it puts a network round trip
between the worker and booting at all.

**Vault** (`/guides/database/vault`) — **applies, but rejected.** It is a
Postgres extension, so our backend could read `vault.decrypted_secrets`. The
objection is structural:

- It **couples two compromises that are independent today.** Now: a leaked
  `service_role` key costs the database; host filesystem access costs the API
  keys. With Vault, one credential opens both. The docs say it outright —
  *"anyone that has access to the view has access to decrypted secrets"* — and
  `SupabaseManager` already runs as service_role and bypasses RLS by design.
- `.env` does not go away. It shrinks to the URL + key needed to *reach* Vault,
  and those two are exactly the ones that now unlock everything else.
- Startup gains a network dependency. We degrade carefully when Supabase is
  unreachable (`_rulings` swallows, `schema_check` swallows); a worker that
  cannot boot without a round trip is a new class of outage.

Vault is built for secrets the *database* needs — a `pg_net` call from a
trigger, a webhook signing key. Using it as an application secret store
collapses the blast radius into one key.

---

## ⏰ Time-sensitive, independent of everything else

**Supabase is deprecating `anon` and `service_role` by the end of 2026** (per
`/guides/api/api-keys`). ~15 weeks from 2026-09-13. Cyclone uses both, in the
API, the worker, and the frontend.

The migration looks like a config swap, because nothing parses these as JWTs
(see the table above). Both key systems run in parallel, so it is reversible:
create `sb_secret_…` and `sb_publishable_…`, swap the values, rebuild **API +
worker + frontend**, confirm, then delete the legacy keys.

It also buys the thing we want anyway: **secret keys are created and revoked
individually**, where rotating `service_role` today means rotating the project
JWT secret.

---

## The drift argument

Centralizing storage **relocates** drift rather than killing it. Tom spotted
why unprompted: a per-server `CONFIG_SET=production-a` key is the same drift
surface, one line instead of forty-eight — and *harder to diagnose*, because
today you can diff two files, whereas a central store keyed on a hand-typed
identifier hides disagreement until behaviour differs.

**So the first question is not where secrets live. It is whether we can detect
disagreement, and today we cannot.** This drift was found by luck.

### The `config_set` trap, and the way out

Do not let a human type the identifier. On EC2:

- **Instance tags via IMDS** — the box learns its own identity with no stored
  credential and nothing to typo; Terraform sets the tag at launch.
- **The IAM instance role** — determines which SSM path it may read.

Identity then determines both *what it can read* and *which config it gets*,
and both are set by provisioning code under review. This is also why **SSM
beats either Supabase option**: the instance identity *is* the credential, so
there is no bootstrap secret at all.

---

## Multi-firm: `.env` conflates three things with three owners

| Layer | Examples | Belongs in |
| --- | --- | --- |
| Per-deployment infrastructure | `SUPABASE_URL`, `REDIS_URL`, `ID` | SSM, by instance tag |
| Shared vendor credentials | `ANTHROPIC_API_KEY`, `STRIPE_SECRET_KEY` | SSM, one path per environment |
| **Per-firm settings** | `FIRM_NAME`, `time_increment_options`, `referral_types`, `default_refresh_trigger_pct` | **A `firms` table** |

The third row is the real scaling blocker and it is **not a secrets problem**.
Those values are served out of `/api/config` today from environment variables —
a single-tenant assumption compiled into the deployment. A second firm on one
deployment needs them per-request, not per-process.

The pattern already exists: firm-wide default as a NULL scope, a row as an
override — `transaction_tags`, `transaction_category_rules`,
`fis_category_settings`, `transaction_payee_classifications`, and the
`matter_preferences` table CLAUDE.md already anticipates. Extending it to firm
settings is a shape we have used five times.

---

## The acquirer/partner story

- **Boring wins.** SSM + Terraform + config in git is a five-second answer
  nobody probes. A bespoke config system invites an hour of questions and reads
  as key-person risk.
- **Do not lead with the secret store.** "We moved secrets to Vault" is weak —
  vendor coupling, and a diligence engineer asks the blast-radius question.
  "Configuration is declarative and version-controlled, deploys are immutable,
  and every node publishes a fingerprint we diff across the fleet" is strong.
- **What already holds up:** `schema_check.py` (startup column diff vs models);
  `llm_profiles.json` (call sites name a task, never a model — and chain order
  is *measured*, which answers "what is your model risk" in one sentence);
  provenance end to end (per-pass extraction records, `category_source`,
  `MANUAL_CORRECTION`, `audit_log`); `.env.example` committed with no values.
- The honest gap is **deployment and tenancy, not engineering judgment** —
  the easier of the two to fix.

---

## Plan, in order. Nothing started.

1. **Config fingerprint on `/api/config`.** A hash over resolved settings: every
   key name, non-secret values, and a short digest of each secret's *value*
   (never the value). ~50 lines. Turns "matter of luck" into
   `curl a | jq .config_hash` vs `curl b | jq .config_hash`. Per-key digests
   behind auth say *which* key differs. **Works with `.env` exactly as it is and
   is wasted by nothing later — it is the check, not the mechanism.**
   ← *Tom was asked whether to start here. Power went out before he answered.*
2. **`sb_secret_` / `sb_publishable_` migration.** On a clock (end of 2026).
3. **Delete `SUPABASE_JWT_SECRET` and `SUPABASE_ANON_JWT_SECRET`** — verified
   dead. Two high-value secrets carried for nothing. (Free; could fold into 2.)
4. **Split `.env` three ways** along the table above. No infrastructure change,
   but it makes 5 and 6 obvious instead of tangled.
5. **Config into version control; deploys replace rather than edit.** Drift
   becomes structurally impossible. This is the fix for "I SSH in and edit
   `.env` and restart the containers."
6. **SSM by instance tag** for the secrets half, once 4 has settled what is
   actually a secret.
7. **`firms` table** when a second firm is real — not before, but designed for
   by doing 4 now.

### Also noted, separate track

`IMAP_PASSWORD` / `SMTP_PASSWORD` deserve their own handling regardless of
where the rest lands: in a law firm that mailbox holds privileged
communications, and it is the one credential whose compromise is a notification
event rather than an inconvenience. Dedicated app password, its own rotation.

---

# What shipped, 2026-09-13

Items 1-6 of the plan above. Item 7 waits for a second firm to be real.

## Three bugs found on the way, all of the class this was about

1. **`.env.example` could not boot.** `Settings` sets `extra = "forbid"`, and
   the file listed `SUPABASE_JWT_SECRET` and `SUPABASE_ANON_JWT_SECRET` — not
   fields at all. A node provisioned from it died on import. Every numeric line
   was also written blank, and an empty string is not a float. The document
   whose job was preventing configuration mistakes was one.
2. **`API_BASE_URL` was a live trap.** `docker-compose.yml` referenced
   `${API_BASE_URL}` and nothing declared it, so anyone setting it would have
   stopped the API container booting.
3. **The deprecation warning would have fired forever.** `db_type` defaults to
   `"supabase"`, so testing the *value* reported it on every node on every boot.
   `model_fields_set` is the question actually being asked.

## Operational note — 2026-09-13

All vendor credentials were **rotated** after they were briefly placed in EC2
instance tags (readable from IMDS by any process on the box, and by anyone with
`ec2:DescribeTags`). Tags were deleted and keys rotated the same day. The
runbook now opens with a warning: the ONLY tag involved is
`cyclone:environment`, whose value is a word.

Consequences to carry forward:

- Both nodes' `.env` files must hold the new values. A stale LLM key fails
  *quietly*, by failing the candidate over to the next model in the chain.
- `VITE_SUPABASE_ANON_KEY` is a docker build arg, compiled into the frontend
  bundle — rotating it requires **rebuilding** the frontend image, not
  restarting it.
- Rotating the legacy `service_role` key means rotating the project JWT secret,
  which invalidates every user session. That is the argument for doing the
  `sb_secret_` migration now rather than at the deadline: per-key revocation.

## Deployment model — learned 2026-09-13, and it changed item 5

**The production servers have no checkout.** They carry `.env`,
`docker-compose.yml`, and whatever scripts get copied across by hand. Images come
from ghcr; nothing is built there.

That invalidates item 5 as it was first built. `docker-compose.yml` was changed
to layer `config/fleet.env` and `config/environments/production.env` under
`.env` — but `env_file` is read by compose ON THE HOST, so a server without
those files fails to start outright. **It was reverted before it shipped.**
`docker-compose.yml` in git is once again byte-identical to the servers'.

Copying a third file to each box would have ADDED a drift surface anyway. For an
image-deployment model the fleet layer is already solved twice over:

- **Non-secret defaults live in `Settings` and ship inside the image.** Two nodes
  on the same tag cannot disagree about `llm_temperature` or `job_concurrency`.
  That is a stronger guarantee than a copied file. `config/fleet.env` was mostly
  restating them.
- **The fingerprint catches the remaining case**, because `version` is hashed —
  a node running an older image reports a different `fleet_hash`. Worth bumping
  `settings.version` per release so the drift check doubles as a deploy check;
  today it says `2026.04.05` while images are tagged `1.0.3`.
- **Anything that must genuinely vary goes in SSM.** `secret_source.load()`
  copies EVERY parameter under the path into the environment, not only the ones
  `config_spec.SECRET` names, so `LOG_LEVEL` or `JOB_CONCURRENCY` can live there
  too. `seed_ssm.py` only writes secret names, deliberately.

So the layering for this fleet is: **code defaults -> SSM -> `.env` for node
identity.** No file anyone hand-copies. `config/` stays in the repo for local
development and as the written record of which setting belongs in which layer;
`config_spec.py` is the part of it that earns its keep in production.

Consequence for the cutover: `CYCLONE_SECRETS_SOURCE=ssm` and `AWS_REGION` go in
`.env` on each node, not in `config/environments/production.env`. Both are
declared `Settings` fields, so `extra = "forbid"` accepts them.

## What to do next, in order

1. **Run `scripts/check-fleet.sh` against both EC2 boxes.** This is the payoff:
   it will say within a second whether the drift found by luck was the only
   drift. Needs the new build deployed to both.
2. **Deploy `config/`** and cut the per-node `.env` down to section 1 (per-node)
   and section 3 (secrets) of the new `.env.example`. Everything in
   `config/fleet.env` can come out of both files.
3. **The Supabase key swap.** Create `sb_secret_...` / `sb_publishable_...` in
   the dashboard, change the VALUES of `SUPABASE_SERVICE_ROLE_KEY` and
   `SUPABASE_ANON_KEY` — the names do not change — rebuild API + worker +
   frontend, confirm, then delete the legacy keys. Startup tells you how many
   days are left until it is no longer optional.
4. **SSM, when you want it.** Put parameters at `/cyclone/<env>/<NAME>`, tag the
   instances `cyclone:environment`, give the instance role
   `ssm:GetParametersByPath` + `kms:Decrypt` on that path, set
   `CYCLONE_SECRETS_SOURCE=ssm`. Nothing is overwritten, so secrets can leave
   `.env` one at a time and go back if anything is wrong.

## Deliberately not done

- **The `firms` table (item 7).** `FIRM_NAME`, `REFERRAL_TYPES`,
  `TIME_INCREMENT_OPTIONS` and `DEFAULT_REFRESH_TRIGGER_PCT` are classified
  `PER_FIRM` in `config_spec` and still served per process from `/api/config`.
  Classifying them now is what makes the move obvious later; building the table
  before a second firm exists would be guessing at its shape.
- **Deleting the six dead `Settings` fields.** `extra = "forbid"` makes removal
  a coordinated change — the moment a field goes, a node whose `.env` still
  carries the line stops booting. They warn at startup now; delete them once
  `scripts/check-fleet.sh` reports 0 deprecated across the fleet.
- **Rotating `IMAP_PASSWORD` / `SMTP_PASSWORD`.** Still the separate track. In a
  law firm that mailbox holds privileged communications.
