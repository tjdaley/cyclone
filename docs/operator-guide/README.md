# Cyclone — Operator Guide

For whoever runs the servers: deploying a release, changing a setting, moving a
credential, and proving the nodes agree with each other.

This is not the [user guide](../user-guide/README.md), which is about doing
legal work inside Cyclone. Nothing here is visible to a paralegal, and nothing
here is the in-app **Admin** screen — that is
[Chapter 12](../user-guide/12-user-roles.md) of the user guide.

## The chapters

| | |
| - | - |
| [1. How configuration is put together](01-configuration.md) | The six kinds of setting, which layer wins, and where each value has to live |
| [2. Secrets from AWS Parameter Store](02-secrets.md) | How a node gets its credentials with nothing stored on disk, and how to move one |
| [3. Proving the nodes agree](03-fleet-checks.md) | `check-fleet.sh`, the fleet hash, and what its exit codes mean |
| [4. Deploying a release](04-deploying.md) | Build, push, roll out, migrate, verify |
| [5. Reading the startup log](05-startup-log.md) | Every warning a node can print at boot, and what to do about each |
| [6. Onboarding a new firm](06-new-firm.md) | The two lists, which are not the same list |

## The deep runbooks

These sit outside the guide because they are procedures, not explanation. Follow
them step by step when the time comes:

| | |
| - | - |
| [`docs/aws-ssm-setup.md`](../aws-ssm-setup.md) | The full AWS build-out: IAM, instance metadata, cutover, rollback, Terraform |
| [`docs/supabase-key-migration.md`](../supabase-key-migration.md) | Swapping the legacy Supabase JWT keys before they are retired |
| [`docs/config-hardening-notes.md`](../config-hardening-notes.md) | The working notes and the reasoning behind what shipped |
| [`config/README.md`](../../config/README.md) | The committed, non-secret half of the configuration |

---

## Why any of this exists

Two servers' `.env` files drifted apart, and it was found **by luck**.

Model chains used to be named in `.env`. Because the two EC2 nodes disagreed,
a pleading was read by Anthropic on one server and by Gemini on the other,
depending on where the load balancer sent the request. **Gemini's content
filters decline material describing crimes against minors** — which in family
law is not an edge case but ordinary evidence: a petition alleging abuse, a
protective-order affidavit, a CPS record.

So the same document processed, then did not, then did.

Three things make that worth building a whole subsystem around:

- **It presented as intermittent.** Nobody diagnoses intermittency as a
  configuration difference. They look for a race, a timeout, a bad PDF.
- **The refusal was invisible.** When a model declines, `llm_service` fails the
  candidate over to the next one and keeps answering — so a refusal, a timeout
  and a quota error all look identical from outside, and the work quietly moves
  to another vendor.
- **It is now structurally impossible.** Model chains live in
  `app/config/llm_profiles.json` *inside the image*, so two nodes on the same
  image tag cannot disagree — and `check-fleet.sh` catches the case where they
  are not on the same tag.

**This is the theme of everything in this guide: here, the failures that matter
look like successes.** A worker with no Anthropic key does not crash — it
silently produces nothing. A node reading the wrong environment's secrets starts
perfectly. That is why a node whose secret store cannot be read **refuses to
start** rather than starting half-configured, and why the check that says two
servers agree needs no credential and no SSH.

---

## Where everything lives, in one table

| | Holds | Changed by |
| - | ----- | ---------- |
| **The image** | Code, `llm_profiles.json`, and every non-secret default (`Settings`) | Building and deploying a release |
| **`config/`** (in git) | The non-secret fleet and per-environment layers, used by local development and as the written record of which setting belongs where | A commit |
| **`.env` on each node** | Node identity (`ID`, `HOST_URL`), the switches that turn the secret loader on, and any credential not yet moved to SSM | Editing the file on the node, then recreating the containers |
| **SSM Parameter Store** | The credentials, at `/cyclone/<environment>/<NAME>` | `seed_ssm.py --apply`, or `aws ssm put-parameter` |
| **The EC2 instance tag** | `cyclone:environment` — **one non-secret string, and nothing else, ever** | The AWS console or CLI |

> ⛔ **Instance tags are not a secret store.** Any process on the box can read
> every tag from instance metadata **with no credentials at all**. This has
> already happened here: a verification script that read config values from tags
> made tags look like the intended location, API keys went in, and they had to
> be rotated. The tag carries the environment *name*. Nothing else.

---

[Index](../README.md) · Next: [How configuration is put together →](01-configuration.md)
