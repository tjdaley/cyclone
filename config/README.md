# `config/` — the versioned half of the configuration

Everything in this directory is **committed, non-secret, and identical on every
node that reads it**. Nothing here is a credential, and nothing secret may be
added: the test suite is not what stops you, judgement is.

## Why it exists

Two servers' `.env` files drifted and it was found by luck. Storage was never
the problem — *editing* was. While the way to change a setting is "SSH in, edit
the file, restart the containers", two nodes agree only for as long as nobody
makes a mistake, and nothing reports when somebody does.

So the fleet layer stops being server state and becomes a build artifact: one
file, in git, reviewed like code, **deployed rather than edited**.

## The three layers, in the order compose applies them

| | File | Contains | Committed? |
| - | ---- | -------- | ---------- |
| 1 | `config/fleet.env` | Identical everywhere: model sampling, poll intervals, timeouts | ✅ yes |
| 2 | `config/environments/<env>.env` | Per-environment, still non-secret | ✅ yes |
| 3 | `.env` on the node | Node identity (`ID`, `HOST_URL`) and **all secrets** | ❌ never |

**Later wins.** A node *can* override a fleet value in its own `.env`, and doing
so immediately shows up as a different `fleet_hash` on `GET /api/config`. The
override is not forbidden — sometimes it is needed at 2am — it is merely
impossible to do quietly.

## ⚠ Only `docker-compose.override.yml` layers these in — development only

`env_file` is read by compose **on the host**, and the production servers carry
only `.env` and `docker-compose.yml`; there is no checkout on them. Listing
these files in `docker-compose.yml` makes `docker compose up` fail outright
there, so it deliberately does not — which means **editing
`config/environments/production.env` changes nothing on a production node.**

For an image deployment the fleet layer is already solved twice over: non-secret
defaults ship inside the image as `Settings` defaults, so two nodes on one tag
cannot disagree, and anything that must genuinely vary goes in SSM. So this
directory is the development layering *and* the written record of which setting
belongs in which layer — `app/util/config_spec.py` is the part that earns its
keep in production.

Production layering is **image defaults → SSM → `.env`**. See
[`docs/operator-guide/01-configuration.md`](../docs/operator-guide/01-configuration.md).

## Changing a fleet value

Edit, commit, deploy, then confirm the fleet moved together:

```sh
scripts/check-fleet.sh https://a.example.com https://b.example.com
```

A node still reporting the old hash did not get the deploy.

## What is deliberately NOT here

- **Secrets.** They go to SSM under an IAM instance role, where the instance
  identity *is* the credential and nothing is stored. Until then, `.env`.
- **Per-firm settings** — `FIRM_NAME`, `REFERRAL_TYPES`, `TIME_INCREMENT_OPTIONS`,
  `DEFAULT_REFRESH_TRIGGER_PCT`. Those become a `firms` row when a second firm is
  real. They are in the environment today, which is a single-tenant assumption
  compiled into the deployment — see `docs/config-hardening-notes.md`.
