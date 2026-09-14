# 2. Secrets from AWS Parameter Store

[← How configuration is put together](01-configuration.md) · [Index](README.md) · Next: [Proving the nodes agree →](03-fleet-checks.md)

---

A node running on EC2 can read its credentials from AWS Systems Manager
Parameter Store, using the instance's own IAM role. **Nothing is stored on the
box, and there is no bootstrap key** — the instance identity *is* the
credential. That is the property neither Supabase option could offer, and it is
the whole reason for choosing SSM.

This chapter is what an operator needs to know. The full build-out — IAM
policies, instance metadata options, Terraform, the triage tables — is
[`docs/aws-ssm-setup.md`](../aws-ssm-setup.md), and it is worth reading once
before the first cutover.

---

## Turning it on

```ini
# .env, on the node
CYCLONE_SECRETS_SOURCE=ssm
AWS_REGION=us-east-1
```

Absent or anything else, the loader does nothing and `.env` is read exactly as
before. **This is the whole opt-in**, and because SSM never overwrites a value
already set ([Chapter 1](01-configuration.md#which-layer-wins)), turning it on
changes nothing until a line is deleted from `.env`.

**If SSM is configured and cannot be read, the process raises and does not
start.** That is deliberate and it is the house rule in a sentence: a worker
with no Anthropic key does not fail, it silently produces nothing. A node with
no credentials is a louder and much cheaper failure than a node with the wrong
ones.

---

## Which environment a node is in

Parameters live at `/cyclone/<environment>/<VARIABLE_NAME>` — a plain path, e.g.
`/cyclone/production/ANTHROPIC_API_KEY`.

The `<environment>` segment is resolved at startup, in this order:

1. **The EC2 instance tag `cyclone:environment`**, read over instance metadata.
   **This wins.**
2. **`CYCLONE_ENV` from `.env`** — consulted only when the tag yields nothing.
3. Neither → the process raises and does not start.

> **`CYCLONE_ENV` is a fallback, not an override.** An override would win, and a
> typo in one box's `.env` would then point that node silently at another
> environment's secrets — which is exactly the drift the tag removes. (Our own
> source called it an override until this release. Expect it to be wrong in
> people's heads.)

### The tag needs two things, and both are easy to miss

- The tag must exist on the instance: `cyclone:environment` = `production`.
- **`instance-metadata-tags` must be enabled.** It is **off by default**, and
  without it metadata returns 404 for every tag even when the tag exists.
- The **metadata hop limit must be 2**, or containers cannot reach metadata at
  all — one hop is consumed getting out of the container network.

A 404 on the tag has two indistinguishable causes: the tag is missing, or
metadata tags are disabled. The triage table in
[`docs/aws-ssm-setup.md`](../aws-ssm-setup.md) covers both, including a probe run
**from inside the container** — which is the only version of the question that
matters, because the host can reach metadata on hop limit 1 where the container
cannot.

> **Success is silence.** When the tag works there is no warning and no "using
> CYCLONE_ENV" line — just the `loaded N parameter(s)` summary. Do not go
> looking for a confirmation that does not come.

---

## The region

Cyclone resolves the AWS region itself and passes it to boto3 explicitly:
`AWS_REGION`, then `AWS_DEFAULT_REGION`, then the region from instance metadata.
Nothing resolvable **raises** rather than defaulting to a guess.

**Do not think of this as "boto3 picks it up from the environment".** It does
not reliably: `AWS_REGION` was set in `.env`, reached the process, and
`boto3.client("ssm")` still raised `NoRegionError`. Which variables botocore
consults is a property of the pinned version, not something to build a boot
sequence on.

It raises instead of guessing because a guessed region reads a parameter path
that does not exist there and reports an **empty environment** — which is
indistinguishable from a correct load of a path where everything is already set.

---

## Putting the secrets in

Use the script. Thirteen hand-typed `aws ssm put-parameter` invocations invite
exactly one kind of mistake — a truncated paste — and that mistake does not
announce itself: a wrong LLM key fails over to the next model in the chain and
keeps answering, and a wrong Stripe key waits until somebody pays.

```sh
# Dry run is the default. Shows what would change, writes nothing.
python scripts/seed_ssm.py --region us-east-1 --env production

# Do it.
python scripts/seed_ssm.py --region us-east-1 --env production --apply
```

- It writes **only credentials** — the same list the fingerprint hashes and the
  tests assert is complete. Fleet, per-firm and infrastructure values are left
  alone on purpose.
- **No value is ever printed**, including on failure and including with
  `--apply`. What it prints is the same twelve-character digest the fingerprint
  publishes, which is enough to see that the local value and the stored one
  agree.
- It reports **orphans** — parameters in the path that are not credentials — so
  anything created by hand is visible rather than mysterious.
- Everything it writes is a **`SecureString`** on the standard tier. There is no
  "encrypted or not" decision to make, because the decision was made by not
  putting non-secrets in SSM at all.

If boto3 is not importable on the node (the `aws` CLI bundles its own botocore,
which is not the same thing), run it inside the container — where boto3 exists
because the loader needs it, and where compose has already mounted `.env`
read-only. Pass `-f docker-compose.yml` explicitly: compose silently applies the
dev override when it is present, and matching production is the only reason to
run it in a container at all.

> **Cost:** Parameter Store standard tier is **free** — no per-parameter charge,
> and `kms:Decrypt` on the AWS-managed key costs nothing. This is the first
> question everybody asks, usually because Secrets Manager is the obvious
> alternative at $0.40 per secret per month — whose headline feature, rotation,
> cannot rotate an Anthropic, Stripe or IMAP credential anyway.

---

## IAM, in four words that other systems treat as one

| | |
| - | - |
| **User** | A person, or a long-lived key. **Not what an EC2 instance uses** |
| **Role** | A set of permissions something can assume |
| **Instance profile** | The container that attaches a role to an instance. One instance, one instance profile, one role |
| **Policy** | The permission document, attached to a role |

**A policy attached to an IAM user is invisible to an EC2 instance.** This cost
a debugging cycle here. The instance acts as `assumed-role/...`, never as
`user/...`:

```sh
# Run this ON THE INSTANCE. A "user/..." ARN means you are not
# measuring what the application does.
aws sts get-caller-identity
```

Two policies, deliberately separate:

- **read** — `ssm:GetParametersByPath` + `kms:Decrypt`, attached to each node's
  instance role, permanently.
- **seed** — `ssm:PutParameter`, attached **only while seeding, then removed.**
  Nodes should not be able to write their own secrets. Treat the removal as a
  step, not a suggestion.

> An inline policy does **not** appear in `list-attached-role-policies`, which
> shows managed policies only — use `list-role-policies` for inline. A
> customer-managed policy attached to the role is the cleaner choice.

---

## Moving a secret, one at a time

**Move them one at a time.** If the fleet hash moves after four, you know
something is wrong but not which one.

1. Seed the parameter (above), and confirm the digest matches.
2. Comment the line out of `.env` on **one** node.
3. `docker compose -f docker-compose.yml up -d --force-recreate api worker`
4. Check the fleet hash ([Chapter 3](03-fleet-checks.md)).

### The control-group technique

Leave the **other node untouched**, with the value still in its `.env`. A
matching fleet hash then proves the SSM value is byte-for-byte identical to the
known-good one — a live comparison against a control, which is far better than a
before-and-after on a single node.

A trailing newline from a copy-paste is enough to move the hash. That is exactly
what you want to learn on the Telegram bot token rather than on the Anthropic
key.

### Rollback

Uncomment the line in `.env` and recreate the containers. No AWS change, no
deploy, no build.

---

## Rotating a secret

1. `aws ssm put-parameter --overwrite`, or re-run `seed_ssm.py --apply`.
2. **Confirm no stale copy is still in `.env` on any node** — it would win, and
   it would say so only as a count in the startup log.
3. `docker compose -f docker-compose.yml up -d --force-recreate api worker`
4. Run the fleet check: the hash **should** change, and change identically on
   every node.

> **Exception — `SUPABASE_ANON_KEY` needs a rebuild, not a restart.** It is a
> docker build arg compiled into the JavaScript bundle, so changing it means
> rebuilding and redeploying the frontend image. See
> [Chapter 4](04-deploying.md).

---

[← How configuration is put together](01-configuration.md) · [Index](README.md) · Next: [Proving the nodes agree →](03-fleet-checks.md)
