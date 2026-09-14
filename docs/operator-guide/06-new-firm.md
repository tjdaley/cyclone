# 6. Onboarding a new firm

[← Reading the startup log](05-startup-log.md) · [Index](README.md)

---

**There is no `firms` table.** Tenancy is compiled into the deployment today: the
firm's name, its intake dropdowns and its billing increments are process
configuration, served per process from `GET /api/config`. A second firm means a
second deployment.

Those settings are already classified as *per-firm*
([Chapter 1](01-configuration.md)), which is what will make moving them into a
table an obvious change rather than a guess at its shape. Until then, this
chapter is the checklist.

---

## Two lists, and they are not the same list

The word "per-firm" covers two unrelated things that share no members. **A
provisioning run needs both**, and doing one without the other produces a
deployment that looks right and serves the previous firm's identity or the
previous firm's credentials.

### List 1 — per-firm **settings** (`.env` on each node)

| Variable | What it is |
| -------- | ---------- |
| `FIRM_NAME` | Displayed in the UI and in `GET /api/config` |
| `REFERRAL_TYPES` | The client-intake dropdown |
| `TIME_INCREMENT_OPTIONS` | Valid billing increments |
| `DEFAULT_REFRESH_TRIGGER_PCT` | Retainer refresh threshold |
| `STRIPE_PUBLISHABLE_KEY` | Publishable, and served to the browser |

These are **not** secrets and are **not** in SSM.

### List 2 — per-firm **credentials** (SSM)

In AWS, the parameters that must be swapped for a new firm carry the tag
`PER_FIRM=Y`. To see the current set:

```sh
aws ssm describe-parameters --region us-east-1 \
  --parameter-filters "Key=Path,Values=/cyclone/production" \
  --query "Parameters[].Name"
# then, per parameter:
aws ssm list-tags-for-resource --resource-type Parameter --resource-id <name>
```

**The absence of the tag is meaningful.** It was applied only to credentials that
*must* change — the firm's own Supabase project, its payment account, its
mailbox. An untagged parameter, such as a shared LLM vendor key, is one a second
firm may reasonably inherit. It does not mean nobody got to it.

> Rule of thumb: if the credential identifies **the firm** (its database, its
> money, its mail, its bot), it is per-firm. If it identifies **us** to a vendor
> who does not care which firm is asking, it can be shared.

---

## The rest of the provisioning run

1. **A new environment name.** Parameters live at `/cyclone/<environment>/...`,
   so the new deployment gets its own path, its own instance tag
   (`cyclone:environment`), and its own read policy scoped to that path.
2. **Per-node identity in `.env`**: `ID`, `HOST_URL`, `SUPABASE_URL`,
   `SUPABASE_ANON_KEY`, `API_BASE_URL`, `CYCLONE_SECRETS_SOURCE`, `AWS_REGION`.
   The build args in that list are why a new firm needs its **own frontend
   image** ([Chapter 4](04-deploying.md)).
3. **Seed the credentials** ([Chapter 2](02-secrets.md#putting-the-secrets-in)),
   then remove the seeding policy from the role.
4. **Run every migration**, in numeric order, against the new database.
   `run_all.sql` covers `001`–`005` only.
5. **Create the `matter-documents` storage bucket** in the Supabase dashboard —
   private. Nothing creates it automatically, and the first statement upload is
   where you find out.
6. **Create the first `admin`** so somebody can grant everyone else a role. No
   one can sign themselves up: see
   [user guide, Chapter 1](../user-guide/01-getting-started.md).
7. **Check the fleet** ([Chapter 3](03-fleet-checks.md)) and **read the startup
   log** ([Chapter 5](05-startup-log.md)) before anyone is invited in.

---

## What a new firm inherits that it may not want

Two things are firm-wide by design and arrive pre-populated. Both are correct
defaults and both are worth a look during onboarding:

- **The chart of accounts** (transaction categories) and the **keyword rules**
  that file transactions automatically.
- **The standing payee rulings** — around thirty platforms that hold value, and
  the buy-now-pay-later lenders. See
  [user guide, Chapter 11](../user-guide/11-discovery-tracking.md#rulings-that-are-already-in-place).

---

[← Reading the startup log](05-startup-log.md) · [Index](README.md)
