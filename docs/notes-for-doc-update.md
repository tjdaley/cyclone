# Notes for the config/secrets documentation update

Handoff notes for whoever writes the operator, deployer and admin docs for this
release. **Everything here is something the diff does not tell you** — the
reasoning behind a choice, a procedure that spans AWS and the repo, or a trap
that has already cost us a production outage. The code is authoritative for
*what*; this is the *why* and the *how*, plus the things that will be got wrong.

Authoritative sources, in case a statement here has drifted:

| | |
| - | - |
| `app/util/config_spec.py` | What every setting IS. Six buckets, total and disjoint. |
| `app/util/secret_source.py` | The SSM loader, and every rule it enforces. |
| `app/util/config_fingerprint.py` | The fleet hash and the startup warnings. |
| `scripts/check-fleet.sh` | The drift check. Its header comment is long and worth reading. |
| `scripts/seed_ssm.py` | Pushes `.env` secrets into SSM. Dry-run by default. |
| `docs/aws-ssm-setup.md` | The full AWS runbook — IAM, metadata, cutover, rollback, Terraform. |
| `CLAUDE.md` §5a | The design rationale in condensed form. |

---

## 1. ⚠ THE NAME COLLISION — READ THIS FIRST

**`PER_FIRM` means two unrelated things, and they share no members.** If the
docs conflate them, somebody provisioning a new firm will change the wrong set
of values.

| | `config_spec.PER_FIRM` | The AWS tag `PER_FIRM=Y` |
| - | - | - |
| Applies to | `Settings` **fields** | **SSM parameters** |
| Lives in | the node's `.env` | AWS Parameter Store |
| Members | `FIRM_NAME`, `REFERRAL_TYPES`, `TIME_INCREMENT_OPTIONS`, `DEFAULT_REFRESH_TRIGGER_PCT`, `STRIPE_PUBLISHABLE_KEY` | a subset of the 12 `SECRET` names |
| Fingerprinted | yes | yes (as secrets) |
| Means | "becomes a `firms` table row when there is a second firm" | "this credential MUST be swapped for a new firm" |

The overlap is **empty**: only `SECRET` names are ever written to SSM
(`seed_ssm.py` enforces this), and no `config_spec.PER_FIRM` field is a secret.

Suggested wording for the docs: call the tag **"per-firm credential"** and the
bucket **"per-firm setting"**, and never write bare `PER_FIRM` without saying
which. A new-firm checklist needs *both* lists and should say so explicitly.

**The tag's absence is meaningful and should be documented as such.** Tom tagged
only the parameters that *must* change for a new firm. An untagged parameter
(say a shared LLM key) is one the firm may reasonably inherit — it is not "we
forgot to tag it". Worth stating, because the natural reading of an untagged row
is that nobody got to it.

---

## 2. The precedence chain — highest wins

This is the single most important thing for an operator to hold in their head,
and it is not visible from any one file:

```
1. process environment   ← .env via compose env_file, and /app/.env mounted
2. SSM parameters        ← only fill names NOT already set
3. Settings defaults     ← compiled into the image
```

**SSM never overwrites something already set.** `secret_source.load()` skips any
name already in `os.environ` and reports the count (`… (8 already set, left
alone)`). Three consequences the docs should state plainly:

- **The cutover is reversible in both directions.** Turn the loader on and
  nothing changes until a line is deleted from `.env`. Turn it off and `.env`
  takes over again.
- **Rollback is uncommenting a line in `.env` and recreating the containers.**
  No AWS change, no deploy. This is the emergency procedure.
- **A stale `.env` line silently wins over a rotated SSM value.** If somebody
  rotates a key in Parameter Store and the old value is still in `.env`, the
  node keeps using the old one and says so only as a count. This is the most
  likely operator mistake with this design and deserves a callout.

---

## 3. Where each value belongs

**`.env` holds what identifies the node and what compose must read. SSM holds
the credentials.** That sentence is the whole rule; everything below is why.

### Must stay in `.env` — cannot come from SSM

| | Why |
| - | - |
| `ID` | Genuinely per-node. It is how a report traces back to a machine. |
| `CYCLONE_SECRETS_SOURCE` | Chicken-and-egg: it is what turns the loader on. |
| `SUPABASE_URL` | **compose build arg** |
| `SUPABASE_ANON_KEY` | **compose build arg** |
| `API_BASE_URL` | **compose build arg** |

The last three are the trap. `docker-compose.yml` interpolates them **on the
host**, before any container exists:

```yaml
VITE_SUPABASE_URL: ${SUPABASE_URL}
VITE_SUPABASE_ANON_KEY: ${SUPABASE_ANON_KEY}
VITE_API_BASE_URL: ${API_BASE_URL:-}
```

`secret_source` runs *inside* the container and cannot possibly supply them.
Move `SUPABASE_ANON_KEY` to SSM only and compose substitutes an **empty
string**: one "variable is not set" warning into a build log, exit code 0, an
image built with no anon key, and a failure that first appears **in a browser at
login**. Nothing in the API log, nothing on the fleet hash, container healthy.

There is now a guard — `_refuse_if_compose_reads_it()` raises at startup if a
`COMPOSE_ONLY` value arrives from SSM — but it fires only if the parameter was
created by hand, since the seeder cannot create one. Document the rule, not just
the guard.

**`SUPABASE_SERVICE_ROLE_KEY` is fine in SSM.** Only its `ANON` sibling is a
build arg. Easy to get backwards.

### Should stay in `.env`, though not strictly required

- `CYCLONE_ENV` — see §4. A fallback; costs nothing, prevents an outage.
- `AWS_REGION` — see §5. Optional wherever IMDS is reachable.

### Belongs in SSM

All 12 `config_spec.SECRET` names. `seed_ssm.py` writes only these, and reports
"orphans" — parameters in the path that are not in `SECRET` — so a hand-created
parameter is visible.

### Deliberately NOT in SSM

`FLEET` and `PER_FIRM` settings. Non-secret defaults already ship inside the
image as `Settings` defaults, so two nodes on the same image tag structurally
cannot disagree about `LLM_TEMPERATURE`. Putting them in SSM would add a place
to look without removing one, and turn a value readable in a `diff` into one
needing AWS access. Worth documenting as a deliberate boundary, because
"centralise everything" is the obvious instinct.

---

## 4. Which environment a node is in

The SSM path is `/cyclone/<environment>/<VARIABLE_NAME>` — a plain path, e.g.
`/cyclone/production/ANTHROPIC_API_KEY`. No `key=value` segments.

The `<environment>` segment is resolved at startup:

1. **EC2 instance tag `cyclone:environment`**, read over IMDS. *Wins.*
2. `CYCLONE_ENV` from `.env`. **Fallback only** — consulted when the tag yields
   nothing.
3. Neither → the process raises and does not start.

**`CYCLONE_ENV` is a FALLBACK, not an override**, and the docs must not call it
an override. An override would win, and a typo in one box's `.env` would then
point that node silently at another environment's secrets — which is exactly the
drift surface the tag removes. (This wording was wrong in our own source until
this release; expect it to be wrong in anyone's head.)

**The tag needs two things, and both are easy to miss:**

- the tag must exist on the instance (`cyclone:environment` = `production`)
- **`instance-metadata-tags` must be enabled** on the instance — it is **off by
  default**, and without it IMDS returns 404 for every tag even when the tag
  exists
- the **metadata hop limit must be 2**, or containers cannot reach IMDS at all
  (1 hop is consumed getting out of the container network)

A 404 on the tag has two indistinguishable causes — tag missing, or
metadata-tags disabled. The triage table in `docs/aws-ssm-setup.md` covers it,
including a probe that asks *from inside the container*, which is the only
version of the question that matters: the host can reach IMDS on hop limit 1
where the container cannot.

**Success is silence.** When the tag works there is no warning and no
"using CYCLONE_ENV" line — just the `loaded N parameter(s)` summary. Tell
operators that, or they will look for a confirmation that does not come.

---

## 5. The AWS region

`secret_source.region()` resolves it and passes `region_name` to boto3
explicitly: `AWS_REGION`, then `AWS_DEFAULT_REGION`, then IMDS
`placement/region`. Nothing resolvable raises rather than defaulting.

**Do not document this as "boto3 picks it up from the environment".** It does
not reliably: `AWS_REGION` was set in `.env`, reached the process, and
`boto3.client("ssm")` still raised `NoRegionError`. Which variables botocore
consults is a property of the pinned version.

Why it raises instead of defaulting to something: a guessed region reads a
parameter path that does not exist there and reports an **empty environment** —
which is indistinguishable from a correct load of a path where everything is
already set.

---

## 6. Parameter types: SecureString for everything we write

`seed_ssm.py` writes **`Type="SecureString"`**, `Tier="Standard"`, KMS key from
`--key-id` (default `alias/aws/ssm`). Everything it writes is in the `SECRET`
bucket, so there is no "clear vs encrypted" decision to make — **the decision was
made by not putting non-secrets in SSM at all**.

If the docs describe adding a parameter by hand, they should say: use
`SecureString`, and if the value is not a credential it probably does not belong
in Parameter Store (see §3, "Deliberately NOT in SSM").

**Cost:** Parameter Store **Standard tier is free** — no per-parameter charge.
This matters because Secrets Manager, the obvious alternative, is $0.40 per
secret per month, and its headline feature (rotation) cannot rotate an Anthropic,
Stripe or IMAP credential anyway. Worth a sentence in the docs; it is the first
question anyone asks.

**`WithDecryption=True`** is used on read, which is why the instance role needs
`kms:Decrypt` as well as the `ssm:` actions.

---

## 7. IAM — the part that confuses people

Four words other systems treat as one. Worth a short glossary in the admin doc:

- **User** — a person or a long-lived key. *Not what an EC2 instance uses.*
- **Role** — a set of permissions something can assume.
- **Instance profile** — the container that attaches a role to an EC2 instance.
  One instance, one instance profile, one role.
- **Policy** — the permission document, attached to a role.

**A policy attached to an IAM user is invisible to an EC2 instance.** This cost
us a debugging cycle. The instance acts as `assumed-role/...`, never as
`user/...` — `aws sts get-caller-identity` run *on the instance* is the check,
and a `user/` ARN in that output means you are not measuring what the app does.

Two separate policies, deliberately:

- **read** — `ssm:GetParametersByPath` + `kms:Decrypt`, attached to each node's
  instance role, permanently
- **seed** — `ssm:PutParameter`, attached only while seeding and then removed

Document the removal as a step, not a suggestion. Nodes should not be able to
write their own secrets.

Also: an inline policy does **not** appear in `list-attached-role-policies`
(that only shows managed policies) — use `list-role-policies` for inline. A
customer-managed policy attached to the role is the cleaner choice and is what
Tom ended up with (`ReadCycloneProductionParameters`).

---

## 8. Checking that the nodes agree — `check-fleet.sh`

The whole reason this project exists: two servers' `.env` files drifted and it
was found **by luck**.

```sh
scripts/check-fleet.sh https://cyclone.jdbot.us              # sampling, one URL
scripts/check-fleet.sh https://a.example.com https://b...    # direct, per node
```

**Two modes, because there are two topologies.** Behind a load balancer with no
per-node address — the correct architecture and the awkward one to verify —
sampling asks the same URL repeatedly and groups answers by the `id` each node
reports. Hit and miss by nature, but testable. `CHECK_FLEET_SAMPLES=40` raises
the count.

**On this deployment, direct mode does not work across the network.**
`docker-compose.yml` publishes `8094:80`, which binds every interface, but the
nodes were not reachable on that port from another host when we tried it —
security groups and/or host firewall. Do not write "the nodes listen on
localhost only" as though it were configuration; it is not what the compose file
says. Write it as what an operator will experience:

- sampling the load-balancer URL is the practical check here
- direct mode works from a node against **itself** (`http://127.0.0.1:8094`)
- the obvious first attempt — the public IPs from the AWS console — fails, and
  looks like the script is broken rather than like a closed port

If the docs recommend direct mode for any environment, they should say it
depends on each node being addressable on 8094 from wherever the check runs, and
that this needs a security-group rule that does not exist today.

### Exit codes

| | |
| - | - |
| **0** | every node seen reported the same hash **and** the same build |
| **1** | config drift — they disagree about configuration |
| **2** | inconclusive — nothing readable, or only one node ever answered |
| **3** | build skew — config agrees, different images |

**2 is deliberately not 0.** A node that is down and a sample that only ever
reached one server are both "we did not learn the answer", and letting either
read as "all clear" is the failure the script exists to prevent.

**3 is deliberately not 1.** Different code and different config are different
problems with different fixes, and one hash covering both could not say which
you had. It is also transiently normal mid-deploy.

Needs **no credential and no SSH** — `/api/config` is unauthenticated precisely
so nothing stands between somebody and running the check. A hash reveals
nothing. The breakdown naming *which* setting differs is
`GET /api/v1/config/fingerprint`, behind auth, because the set of digests is an
inventory of what a node holds.

### What the hash covers, and what it does not

- **Covers:** `FLEET` + `SECRET` + `PER_FIRM`. Secrets contribute
  `sha256(value)[:12]`, never the value. `<unset>` is a distinct marker from any
  digest — "both nodes have no Stripe key" and "both hold the same Stripe key"
  are different states and the first is the dangerous one.
- **Excludes:** `id`, `host_url`, the Supabase/Redis URLs, the loader switches.
  A hash over `id` would differ between any two machines forever, and a drift
  report that always fires is one nobody reads.
- **Excludes the build**, reported separately — see §9.

### The control-group technique (worth documenting; it is not obvious)

When migrating a secret into SSM, leave the **other node untouched** with the
value still in `.env`. A matching hash then proves the SSM value is
byte-for-byte identical to the `.env` value — a live comparison against
known-good, better than a before/after on one node. A trailing newline from a
copy-paste is enough to move the hash, which is precisely what you want to learn
on the Telegram token rather than on the Anthropic key.

Move secrets **one at a time**. If the hash moves after four, you know something
is wrong but not which.

---

## 9. Build identity — new in this release

`CYCLONE_BUILD` is baked into the image by `app/Dockerfile` (`ARG` → `ENV`) and
reported at `/api/config` as `config_fingerprint.build`. `deploy-version.bat`
drives both the image tag and the stamp from one variable so they cannot
disagree.

It comes from the **build**, never from `.env`: a value in configuration could
be set wrong, and a node claiming a build it is not running is worse than one
saying nothing. An image built without it reports `unstamped`.

**It is reported beside the fleet hash and never inside it.** Folding it in
would make every release read as drift, and would destroy the control-group
technique in §8 — which only works while the hash stays comparable across
builds.

Why this matters enough to build: see §11.

---

## 10. Procedures the docs should spell out

**Rotating a secret**

1. `aws ssm put-parameter --overwrite` (or re-run `seed_ssm.py --apply`)
2. Confirm no stale copy is still in `.env` on any node — **it would win**
3. `docker compose up -d --force-recreate api worker`
4. `check-fleet.sh` — hash *should* change, and identically on both nodes

**Exception — `SUPABASE_ANON_KEY` needs a rebuild, not a restart.** It is a
docker build arg compiled into the JS bundle. Changing it requires rebuilding
and redeploying the frontend image.

**Deploying**

- **The `worker` service runs the same image as the API.** Backend changes need
  *both* rebuilt; shipping only the API leaves jobs queued with nothing running
  them. This is the most repeated deployment mistake in this project.
- **Migrations are run by hand, in numeric order.** `run_all.sql` only covers
  001–005. Name the migration file explicitly in release notes.

**Onboarding a new firm** — needs both lists from §1: the per-firm *credentials*
(SSM, tagged `PER_FIRM=Y`) and the per-firm *settings* (`.env`:
`FIRM_NAME`, `REFERRAL_TYPES`, `TIME_INCREMENT_OPTIONS`,
`DEFAULT_REFRESH_TRIGGER_PCT`, `STRIPE_PUBLISHABLE_KEY`). There is no `firms`
table yet — tenancy is currently compiled into the deployment, and these
settings are the clearest evidence of it.

**Startup warnings an operator will see and should understand**

- legacy Supabase JWT keys, with days remaining — see §12
- deprecated settings still in `.env` — reported from `model_fields_set`, i.e.
  "somebody set it", not "it holds a value"
- misplaced secrets — e.g. an `sb_secret_` value in `SUPABASE_ANON_KEY`, or an
  `sk_live_` in `STRIPE_PUBLISHABLE_KEY`. This one caught a real mistake.

---

## 11. Why any of this exists — the story worth telling in the docs

Abstract arguments about configuration management do not motivate anybody. This
one does, and it is real.

Model chains used to be named in `.env`. The two EC2 nodes drifted, so a pleading
was read by Anthropic on one server and Gemini on the other, depending on where
the load balancer sent the request. **Gemini's content filters decline material
describing crimes against minors** — which in family law is not an edge case but
ordinary evidence: a petition alleging abuse, a protective-order affidavit, a CPS
record. So the same document processed, then did not, then did.

Three things make this the perfect illustration:

- **It presented as intermittent.** Nobody diagnoses intermittency as a
  configuration difference; they look for a race, a timeout, a bad PDF.
- **The refusal was invisible.** `llm_service` fails a candidate over to the next
  model and keeps answering, so a refusal, a timeout and a quota error all look
  identical — the work just quietly moves to another vendor.
- **It is now structurally impossible.** Model chains live in
  `app/config/llm_profiles.json` *inside the image*, so two nodes on the same tag
  cannot disagree — and `check-fleet.sh` exit 3 catches the case where they are
  not on the same tag.

This is the house theme and it is worth naming in the docs: **the failures that
matter here look like successes.** A worker with no Anthropic key does not
crash — it silently produces nothing. That is why an unreadable SSM raises and
stops the process rather than starting half-configured.

---

## 12. The Supabase key deadline — has a real date

Supabase retires the legacy `anon` / `service_role` JWT keys at the **end of
2026**. Startup logs a warning with days remaining for any key still in that
format, detected by the format itself rather than a flag somebody sets — so a
node that was missed reports itself. It becomes an ERROR once overdue.

Points the docs should carry:

- The migration is a change of **value in the same variable**, not a rename.
- Keys live under **Settings → API Keys** in the Supabase dashboard (there is no
  longer a *Settings → API* page). This is genuinely hard to find.
- **There are two projects** to do — Cyclone's own and the landing-pages one.
- The **frontend must be rebuilt**, because the anon key is a build arg.
- New-format keys are `sb_publishable_...` and `sb_secret_...`. Both systems run
  in parallel, so the swap is reversible.
- Secret keys are revocable individually, where rotating `service_role` today
  means rotating the project JWT secret and logging every user out.

Full procedure: `docs/supabase-key-migration.md`.

---

## 13. ⚠ Security note for whatever the docs say about secrets

**Never suggest EC2 instance tags as a place to put credentials.** Instance tags
are readable from IMDS by any process on the box with **no credentials at all**.
This happened here: a verification script that read config values from tags made
tags look like the intended location, API keys went in, and the keys had to be
rotated. The instance tag carries the environment *name* — one non-secret string
— and nothing else, ever.

If the docs show any script that reads instance tags, it should say in the same
breath what tags are not for.
