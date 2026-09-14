# AWS setup for SSM-backed secrets

Everything the AWS side needs for `app/util/secret_source.py`, in the order you
do it. Written for one environment (`production`); repeat for `dev` by changing
the name in the paths and the tag.

**Nothing here changes how the app behaves until the last step.** The loader is
off unless `CYCLONE_SECRETS_SOURCE=ssm`, and even then it never overwrites a
value already in the environment — so secrets leave `.env` one at a time, and go
back by deleting one line if anything looks wrong.

---

## The four IAM words, because they are four things other systems call one

AWS splits permissions across four nouns, and three of them sound like they
might be the same noun. Almost every mistake in this document comes from that.

| Word | What it actually is | Does it have a password or keys? |
| ---- | ------------------- | -------------------------------- |
| **Policy** | A document listing allowed actions. Attaches to things. **Does nothing on its own.** | no |
| **User** | A person, or a long-lived credential a person holds | yes |
| **Role** | A set of permissions something *temporarily becomes*. Credentials are issued on demand and expire | **no** |
| **Instance profile** | The hook that lets an EC2 instance wear a role. Usually shares the role's name, which is why it seems redundant | no |

The model that makes it stick: **a role is a costume, an instance profile is the
hook it hangs on, and an instance has exactly one hook.** Boto3 on the instance
puts on whatever costume is on that hook and is issued short-lived credentials —
which is why there is nothing to store and nothing to leak.

Two consequences that matter here:

- **A policy on a USER is invisible to the app.** The app is never a user. It
  authenticates as `assumed-role/<RoleName>/<instance-id>`, so a policy attached
  to a person grants that person access and the application nothing.
- **One hook means one role.** A Cyclone-specific role is a *replacement* for
  whatever is on the hook, not an addition, and it must carry everything the box
  needs.

## The three things that will bite you

Read these first. Two of them are invisible failures that look like "the tag
isn't working", and the third is a silent region error.

### 1. IMDS hop limit must be 2, or containers can't read metadata

The instance metadata service defaults to `HttpPutResponseHopLimit = 1`. A
process inside a Docker container reaches `169.254.169.254` through the bridge
NAT, which costs **one extra hop**, so the response is dropped and the token
request times out. The app is on the host, the `curl` you run to test it works
fine, and the container sees nothing.

```sh
aws ec2 modify-instance-metadata-options \
  --instance-id i-0123456789abcdef0 \
  --http-tokens required \
  --http-put-response-hop-limit 2 \
  --http-endpoint enabled
```

No reboot needed. This is the single most common reason IMDS "doesn't work in
Docker".

### 2. Instance tags are NOT in metadata by default

`/latest/meta-data/tags/instance/...` returns **404** until you turn it on, per
instance. Tagging the instance is not enough on its own.

```sh
aws ec2 modify-instance-metadata-options \
  --instance-id i-0123456789abcdef0 \
  --instance-metadata-tags enabled
```

If the tag is missing, `secret_source` logs a warning naming the tag and falls
back to `CYCLONE_ENV`. It does **not** guess.

### 3. boto3 needs a region, and won't reliably find one in a container

Set it explicitly.

**On a production node it goes in `.env`.** `config/environments/production.env`
is not layered into `docker-compose.yml` — the servers have no checkout, and
`env_file` is read by compose on the host, so listing it there would stop
`docker compose up` outright. Editing that file changes nothing on a production
node.

```sh
# On the node.
echo 'AWS_REGION=us-east-1' >> .env
```

`AWS_REGION` and `AWS_DEFAULT_REGION` are declared as `Settings` fields precisely
so this is safe: `.env` is also mounted at `/app/.env` and parsed by pydantic,
where `extra = "forbid"` rejects any key the model does not declare. **Any
*other* `AWS_*` variable in `.env` will stop the container booting** — those
belong in the process environment (in development, `config/environments/dev.env`,
which compose injects and pydantic never parses).

In development, where the checkout exists and `docker-compose.override.yml`
layers the committed files in:

```sh
echo 'AWS_REGION=us-east-1' >> config/environments/dev.env
```

---

## ⛔ Tags are not a secret store

**Exactly one tag is involved in any of this: `cyclone:environment`, whose value
is a word like `production`.** No credential ever goes in a tag.

An EC2 instance tag is world-readable inside the box and widely readable outside
it:

- **Any process on the instance can read every tag from IMDS with no
  credentials**, once `instance-metadata-tags` is enabled — which step 4 turns
  on deliberately. Every container, every dependency, and anything that can be
  induced to make an HTTP request. SSRF against IMDS is the best-known cloud
  attack there is, and a secret in a tag sits at the end of it.
- Any principal with `ec2:DescribeInstances` or `ec2:DescribeTags` can read
  them, and that is a routinely-granted read permission.
- They are plain text in the console, and they propagate into AWS Config,
  CloudTrail, Resource Groups, cost-allocation reports, launch templates, and
  most monitoring and tagging tools.

A secret in a tag is **worse than the `.env` file it came from**, which was at
least root-readable on a single host.

If credentials have gone into tags, treat it as an exposure:

```sh
# 1. See what is there.
aws ec2 describe-tags   --filters "Name=resource-id,Values=i-0123456789abcdef0"   --query 'Tags[].{Key:Key,Value:Value}' --output table

# 2. Delete every tag that is not cyclone:environment (or an ordinary label
#    like Name). Deleting the tag does NOT undo the exposure.
aws ec2 delete-tags --resources i-0123456789abcdef0   --tags Key=ANTHROPIC_API_KEY Key=STRIPE_SECRET_KEY   # ...and the rest
```

**3. Rotate every key that was ever in a tag.** Deletion removes the current
value; it does not retract what has already been read, and tag history survives
in Config and CloudTrail. Rotate at the vendor — Anthropic, OpenAI, Stripe,
Supabase — and change the mailbox password. The mailbox is the one to do first:
in a law firm it holds privileged communications, and it is the credential whose
compromise is a notification event rather than an inconvenience.

Then put them where they belong, which is step 1.

---

## Step 1 — Create the parameters

Path shape is fixed by the code: `/cyclone/<environment>/<EXACT_ENV_VAR_NAME>`.
The leaf **is** the environment variable name — `secret_source` takes everything
after the last `/` and sets it verbatim.

### Use the script, not thirteen `put-parameter` calls

`scripts/seed_ssm.py` reads the `.env` that is already known good and pushes
only the names in `config_spec.SECRET`. Typing thirteen credentials by hand
invites exactly one mistake — a truncated paste — and that mistake does not
announce itself: a wrong LLM key fails the candidate over to the next model in
the chain and keeps answering.

### Which credentials to seed with — the least bad of three

The step-2 policy grants `GetParametersByPath` and nothing else, deliberately:
**an instance that can rewrite its own secrets is an instance that can be *made*
to rewrite its own secrets.** So the steady state has no `PutParameter`
anywhere. Seeding still has to write once, and the three ways to arrange that
are not equally safe.

| | What it costs |
| - | ------------- |
| Copy `.env` to a workstation and seed from there | Puts **every credential** on a second machine, permanently, to avoid a one-off permission. Worst of the three |
| Put admin access keys on the node | Admin is the highest-value credential in the account, and it lands — however briefly, and in shell history — on the host most exposed to the internet |
| **Grant the instance role `PutParameter` temporarily**, scoped to the one path, then remove it | A window in which a compromised app could rewrite its own secrets. Bounded, scoped, and visible as a policy you can see and delete |

**The third is the one to use.** It is the only one where no long-lived
credential moves anywhere: the instance role's credentials are already
short-lived and rotating, and the extra grant is a separate attachment that can
be removed in one command and confirmed gone.

Attach this as a second managed policy — separate, never merged into the read
policy, so that deleting it is one action and forgetting to is visible:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "TemporarySeedingOnly",
      "Effect": "Allow",
      "Action": ["ssm:PutParameter"],
      "Resource": [
        "arn:aws:ssm:us-east-1:<ACCOUNT_ID>:parameter/cyclone/production",
        "arn:aws:ssm:us-east-1:<ACCOUNT_ID>:parameter/cyclone/production/*"
      ]
    },
    {
      "Sid": "EncryptForSecureString",
      "Effect": "Allow",
      "Action": ["kms:Encrypt", "kms:GenerateDataKey"],
      "Resource": "arn:aws:kms:us-east-1:<ACCOUNT_ID>:key/alias/aws/ssm",
      "Condition": {
        "StringEquals": { "kms:ViaService": "ssm.us-east-1.amazonaws.com" }
      }
    }
  ]
}
```

A `SecureString` write needs the KMS half as well as `PutParameter`; without it
the put fails with a KMS error rather than an SSM one, which reads like a
different problem entirely.

**Seed from ONE node only.** Parameters are account-wide, so the second node has
nothing to add — and if `check-fleet.sh` reported agreement, both nodes hold
identical values, so it does not matter which one. That is the fingerprint
paying for itself: without it, "are these two files the same?" would be a
question, and seeding from the wrong box would quietly publish the wrong values.

When the seeding is done, remove the grant and confirm:

```sh
aws iam detach-role-policy --role-name EC2_DynamoDB_Plus_Streams \
  --policy-arn arn:aws:iam::<ACCOUNT_ID>:policy/SeedCycloneParameters

# Should list ONLY the read policy.
aws iam list-attached-role-policies \
  --role-name EC2_DynamoDB_Plus_Streams --output table
```

Run it **on the node**, where `.env` lives. Two ways, and the difference is one
dependency:

```sh
cd ~/cyclone
python3 -c "import boto3"    # the aws CLI bundles its own botocore, which is
                             # NOT importable. This is the actual question.
```

**If boto3 imports**, run it directly. `app/util/config_spec.py` is source in the
checkout, so nothing else is missing:

```sh
python3 scripts/seed_ssm.py --region us-east-1 --env production           # dry run
python3 scripts/seed_ssm.py --region us-east-1 --env production --apply
```

**If it does not**, run it in the container, which has boto3 because the loader
needs it:

```sh
docker compose -f docker-compose.yml run --rm --no-deps \
  -v "$PWD/scripts:/scripts:ro" \
  api python /scripts/seed_ssm.py --region us-east-1 --env production
```

`-f docker-compose.yml` is not optional: `docker compose` silently applies
`docker-compose.override.yml` when it is present, which would pull in the dev
environment file. Seeding still works — `.env` is last in both layerings — but
matching production is the only reason to run it in a container at all.

`docker compose run` builds a throwaway container and does not disturb the
running API. `.env` is already mounted at `/app/.env`, and the script finds it
there without being told.

**The container route is worth preferring even when boto3 is present on the
host**, for one reason: it proves the CONTAINER can reach IMDS. That is where
the metadata hop limit actually bites, and it is the same path the loader takes
at cutover — so a successful dry run here is a rehearsal of the real thing
rather than an adjacent check.

The script imports nothing but `config_spec`: no `Settings` singleton, no
logger, and `secret_source.load()` never runs. It cannot disturb the
application's own startup path even by accident.

It prints a table of every secret, the same twelve-character digest the
fingerprint publishes, and what it would do — `create`, `match`, or
`UPDATE (differs from stored)`. **No value is ever printed**, with or without
`--apply`, so the output is safe on a terminal that is being recorded.

Three things it tells you that the raw CLI does not:

- Which secrets are **absent from `.env`** — each is a feature that is simply
  off, and seeing the list is how you notice one that should not be.
- Which are **already in SSM and differ** from the local value, before you
  overwrite anything.
- **Orphans**: names under the path that are not known secrets. The loader will
  still set them, so a stale one is worth knowing about.

Because it compares digests against what the node reports, the same numbers
appear on `GET /api/v1/config/fingerprint` — the check and the seeding agree by
construction rather than by care.

Doing it by hand instead:

```sh
REGION=us-east-1
ENV=production

put () {   # put NAME "value"
  aws ssm put-parameter \
    --region "$REGION" \
    --name "/cyclone/$ENV/$1" \
    --value "$2" \
    --type SecureString \
    --key-id alias/aws/ssm \
    --tier Standard \
    --overwrite
}

put ANTHROPIC_API_KEY                       'sk-ant-...'
put OPENAI_API_KEY                          'sk-...'
put GEMINI_API_KEY                          '...'
put SUPABASE_SERVICE_ROLE_KEY               'sb_secret_...'
put SUPABASE_LANDING_PAGES_SERVICE_ROLE_KEY 'sb_secret_...'
put STRIPE_SECRET_KEY                       'sk_live_...'
put STRIPE_WEBHOOK_SECRET                   'whsec_...'
put SMTP_PASSWORD                           '...'
put IMAP_PASSWORD                           '...'
put TELEGRAM_BOT_TOKEN                      '...'
```

Notes:

- **`SecureString` for every one.** A `String` parameter is stored in plaintext
  and readable by anything with `ssm:GetParameter`, with no KMS step in the way.
- **`alias/aws/ssm` is the free, account-managed key.** A customer-managed key
  costs about $1/month and buys you a key policy of your own and its own
  CloudTrail trail — worth it if you ever want "who read this secret" answerable
  independently of the IAM policy. Either works; decide once.
- **Standard tier** is free and caps at 4 KB per parameter. Every credential
  here is well under that. Advanced tier ($0.05/parameter/month) buys 8 KB and
  parameter policies; you don't need it.
- **Shell history.** `put ... 'sk-ant-...'` puts live keys in `~/.bash_history`.
  Use `--value "$(cat key.txt)"` and delete the file, or set them in the
  console, or prefix each command with a space if `HISTCONTROL=ignorespace`.

Verify the shape before moving on:

```sh
aws ssm get-parameters-by-path --region "$REGION" \
  --path "/cyclone/$ENV/" --recursive \
  --query 'Parameters[].Name' --output table
```

---

## Step 2 — The IAM policy

Least privilege is easy here because the app only ever does one call.
`GetParametersByPath` with `Recursive=True`, and `Decrypt` on the key that
protects them.

> **`<ACCOUNT_ID>` below is a placeholder and the policy is invalid until it is
> replaced.** It is written this way on purpose: an earlier draft used a
> realistic-looking `123456789012`, which pastes cleanly, saves without
> complaint, attaches without complaint, and then denies every request — because
> it grants access to resources in somebody else's account. The failure appears
> only at the first real call, as an `AccessDeniedException` that reads exactly
> like a missing policy. A placeholder that cannot be saved by accident is worth
> more than a comment asking you to remember.

Save as `cyclone-ssm-read.json`. Substitute `<ACCOUNT_ID>` (and the region, if
it is not `us-east-1`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadCycloneProductionParameters",
      "Effect": "Allow",
      "Action": ["ssm:GetParametersByPath"],
      "Resource": [
        "arn:aws:ssm:us-east-1:<ACCOUNT_ID>:parameter/cyclone/production",
        "arn:aws:ssm:us-east-1:<ACCOUNT_ID>:parameter/cyclone/production/*"
      ]
    },
    {
      "Sid": "DecryptThemViaSSMOnly",
      "Effect": "Allow",
      "Action": ["kms:Decrypt"],
      "Resource": "arn:aws:kms:us-east-1:<ACCOUNT_ID>:key/alias/aws/ssm",
      "Condition": {
        "StringEquals": {
          "kms:ViaService": "ssm.us-east-1.amazonaws.com"
        }
      }
    }
  ]
}
```

**Both resource forms, with and without the trailing path**, because
`GetParametersByPath` authorises against the path being queried and the request
carries it with a trailing slash. `.../production/*` ought to cover it — an IAM
`*` matches the empty string — but the two ARNs cost nothing and remove the
question entirely, which is worth more than being right about a wildcard at the
moment something is denied.

Sanity-check what you actually attached, which is not always what you wrote:

```sh
ARN=arn:aws:iam::<ACCOUNT_ID>:policy/ReadCycloneProductionParameters
aws iam get-policy-version --policy-arn "$ARN" \
  --version-id "$(aws iam get-policy --policy-arn "$ARN" \
                    --query 'Policy.DefaultVersionId' --output text)" \
  --query 'PolicyVersion.Document'
```

A placeholder account id surviving into the attached document is the single most
likely reason for an `AccessDeniedException` that names the right action on the
right resource.

Two deliberate narrowings:

- **`GetParametersByPath` only** — not `GetParameter`, not `GetParameters`, and
  emphatically not `ssm:*`. The app uses exactly one API; anything more is reach
  that only matters when something has gone wrong.
- **`kms:ViaService`** means the role can decrypt these parameters *through
  Parameter Store* and cannot use the key for anything else.

The resource path is environment-scoped, so a dev box's role cannot read
production parameters even if somebody mis-tags it.

```sh
aws iam create-policy \
  --policy-name CycloneSSMReadProduction \
  --policy-document file://cyclone-ssm-read.json
```

### Inline or managed? Both work; one of them drifts

The console's JSON editor creates an **inline** policy — a document that lives
inside one role and cannot be shared. It grants exactly what a managed policy
grants, so nothing is wrong with it on a single role.

On a **fleet** it reintroduces the problem this whole exercise is about. Two
servers wearing two different roles need two inline copies of the same document,
maintained separately, with nothing anywhere reporting when they diverge — and
an IAM policy that quietly differs between nodes is harder to notice than a
`.env` that does, because there is no fingerprint for it.

A managed policy attached to both roles is one document with one source of
truth. Prefer it wherever more than one role needs the same grant.

There is a second, quieter cost: **inline policies do not appear in
`list-attached-role-policies`.** Anyone auditing "what can this role do" with
the obvious command sees nothing. Both listings are needed:

```sh
aws iam list-attached-role-policies --role-name <role> --output table   # managed
aws iam list-role-policies          --role-name <role> --output table   # inline
aws iam get-role-policy --role-name <role> --policy-name <name>         # read one
```

Converting an inline policy to the managed one, once it is attached:

```sh
aws iam attach-role-policy --role-name <role>   --policy-arn arn:aws:iam::084547597492:policy/CycloneSSMReadProduction
aws iam delete-role-policy --role-name <role> --policy-name <inline-name>
```

Attach before deleting. In that order there is no instant where the role lacks
the permission.

### ⚠ Attach it to the ROLE, never to an IAM user

The application authenticates as the **instance role**, never as a person. Its
principal looks like this — it is what the CLI reports when run on the box with
no profile configured:

```
arn:aws:sts::084547597492:assumed-role/EC2_DynamoDB_Plus_Streams/i-09e2...
```

A policy attached to an IAM *user* is invisible to that principal. Doing it that
way fails twice over: the app still cannot read its secrets, **and** a human
identity has quietly gained read access to the firm's production credentials.

Check where a policy actually landed:

```sh
aws iam list-entities-for-policy \
  --policy-arn arn:aws:iam::084547597492:policy/CycloneSSMReadProduction \
  --output table
```

`PolicyRoles` should name the instance role. `PolicyUsers` and `PolicyGroups`
should be empty — detach anything there with `aws iam detach-user-policy`.

---

## Step 3 — The instance role

This is the part that makes the whole approach worth doing: **there is no stored
credential.** boto3 gets short-lived credentials from IMDS, and the role decides
what they can reach.

### One instance, one role — which decides the whole question

**An EC2 instance can have exactly one instance profile, and therefore exactly
one role.** There is no attaching a second. So a Cyclone-specific role is not an
*addition*, it is a *replacement*, and it must carry everything the box needs —
not only Cyclone's part.

These boxes already carry `EC2_DynamoDB_Plus_Streams`, from an earlier life or
from something else sharing the hardware. That leaves two honest options:

| | What it is | Cost |
| - | ---------- | ---- |
| **A. Attach to the existing role** | One `attach-role-policy`, nothing disturbed | The role's name now lies about what it grants, and **every** instance using it gains read access to the firm's credentials |
| **B. A `CycloneNode` role carrying both sets** | Correctly named, scoped to these boxes | A live change to a running instance, and the DynamoDB permissions must be replicated exactly or the other workload breaks |

**B is the right end state** and the reason is not tidiness. A role named for
DynamoDB that also unlocks a law firm's production credentials is a thing
nobody inheriting this will expect, and IAM is read by people looking for
surprises. It is also the version that survives the question "what can this
instance do, and why".

Find out what the existing role actually grants before deciding — if nothing on
the box still uses DynamoDB, B costs almost nothing:

```sh
aws iam list-attached-role-policies --role-name EC2_DynamoDB_Plus_Streams --output table
aws iam list-role-policies          --role-name EC2_DynamoDB_Plus_Streams --output table
```

The second command matters: **inline** policies do not show in the first, and
missing one is how the replacement silently drops a permission.

#### Doing the swap

```sh
# The new role, trusting EC2 (trust.json as below).
aws iam create-role --role-name CycloneNode \
  --assume-role-policy-document file://trust.json

# Cyclone's own read access.
aws iam attach-role-policy --role-name CycloneNode \
  --policy-arn arn:aws:iam::084547597492:policy/ReadCycloneProductionParameters

# Plus every policy the old role carried, if the box still needs them.
# aws iam attach-role-policy --role-name CycloneNode --policy-arn <each one>

aws iam create-instance-profile --instance-profile-name CycloneNode
aws iam add-role-to-instance-profile \
  --instance-profile-name CycloneNode --role-name CycloneNode

# Swap it in. No restart, and no downtime -- but credentials in flight keep the
# OLD permissions until they refresh, so allow a few minutes before concluding
# anything from a failure.
ASSOC=$(aws ec2 describe-iam-instance-profile-associations \
  --filters Name=instance-id,Values=i-09e2a550893c5be9a \
  --query 'IamInstanceProfileAssociations[0].AssociationId' --output text)

aws ec2 replace-iam-instance-profile-association \
  --association-id "$ASSOC" --iam-instance-profile Name=CycloneNode
```

Do one instance, verify, then the other. The load balancer covers a node that
misbehaves, and doing both at once removes the only comparison available.

Verify as the instance, not as yourself:

```sh
aws sts get-caller-identity     # expect assumed-role/CycloneNode/i-...
aws ssm get-parameters-by-path --region us-east-1 --path /cyclone/production/ \
  --recursive --query 'Parameters[].Name' --output table
```

---

### First: does the instance already have one?

The Cyclone boxes **do** — `EC2_DynamoDB_Plus_Streams`, from an earlier
deployment. Check before doing anything:

```sh
# From an ADMIN workstation, not from the instance.
aws ec2 describe-iam-instance-profile-associations \
  --filters Name=instance-id,Values=i-09e2a550893c5be9a \
  --query 'IamInstanceProfileAssociations[].{Profile:IamInstanceProfile.Arn,Assoc:AssociationId,State:State}' \
  --output table
```

If a profile is already attached, **do not run the `associate` command below** —
it will fail, or replace the existing profile and take whatever that role was
for with it. Instead, attach the new policy to the role that is already there:

```sh
aws iam attach-role-policy \
  --role-name EC2_DynamoDB_Plus_Streams \
  --policy-arn arn:aws:iam::084547597492:policy/CycloneSSMReadProduction
```

That is the fast, non-disruptive move and it is what to do today.

**It is worth knowing what it costs.** A role is a grant to every instance that
assumes it, so attaching Cyclone's secret-read policy to a shared role gives
*every* instance using that role the ability to read this firm's production
credentials. Find out how wide that is before deciding it is fine:

```sh
aws ec2 describe-instances \
  --query 'Reservations[].Instances[?IamInstanceProfile.Arn!=`null`].{
      Id:InstanceId,
      Name:Tags[?Key==`Name`]|[0].Value,
      Profile:IamInstanceProfile.Arn
    }' --output table
```

If the answer is "only the two Cyclone boxes", attaching the policy is
effectively a dedicated role and there is nothing to fix. If other workloads
share it, the tidier end state is a role of Cyclone's own carrying *both* sets of
permissions, swapped in with
`replace-iam-instance-profile-association` — which is a live change to a running
instance, so it is a deliberate job for a quiet morning rather than something to
fold into this one.

### Creating one from scratch

Only if the instance has no profile at all:

```sh
cat > trust.json <<'JSON'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "ec2.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
JSON

aws iam create-role --role-name CycloneNode \
  --assume-role-policy-document file://trust.json

aws iam attach-role-policy --role-name CycloneNode \
  --policy-arn arn:aws:iam::<ACCOUNT_ID>:policy/ReadCycloneProductionParameters

aws iam create-instance-profile --instance-profile-name CycloneNode
aws iam add-role-to-instance-profile \
  --instance-profile-name CycloneNode --role-name CycloneNode

# Attach to a running instance — no restart required.
aws ec2 associate-iam-instance-profile \
  --instance-id i-0123456789abcdef0 \
  --iam-instance-profile Name=CycloneNode
```

If the instance already has a profile, use `replace-iam-instance-profile-association`
with the existing association id from `describe-iam-instance-profile-associations`.

---

## Step 4 — Tag the instance, and fix metadata options

The tag is what decides which parameters this node reads. **Nobody types it at a
prompt** — that was the whole objection to a `CONFIG_SET` line, which is the same
drift surface one line wide and harder to diagnose.

**One tag, and its value is a word.** `cyclone:environment=production` names the
SSM path; the credentials live at that path, never in a tag. Enabling
`instance-metadata-tags` below makes every tag on this instance readable by
every process on it, with no credentials — which is exactly what makes the
environment name convenient and a secret catastrophic. See the section above.

```sh
aws ec2 create-tags \
  --resources i-0123456789abcdef0 \
  --tags Key=cyclone:environment,Value=production

# The two from "will bite you" above.
aws ec2 modify-instance-metadata-options \
  --instance-id i-0123456789abcdef0 \
  --http-tokens required \
  --http-put-response-hop-limit 2 \
  --http-endpoint enabled \
  --instance-metadata-tags enabled
```

### Confirm both settings actually applied

The console shows four separate fields here and two of them sound alike, so
check it with one command instead. `Instance metadata service: Enabled` is
`HttpEndpoint` — whether IMDS answers at all — and is **not** the tags setting.
The one that matters is labelled *"Allow tags in instance metadata"*.

> **Run this from an admin workstation, not from the instance.** On the box the
> CLI authenticates as the *instance role*, which does not have
> `ec2:DescribeInstances` and must not be given it — that is an administrator's
> question, and granting it to a production instance role to satisfy a one-off
> check is how a permission becomes permanent. The app never calls it.

```sh
aws ec2 describe-instances   --instance-ids i-0123456789abcdef0 i-0fedcba9876543210   --query 'Reservations[].Instances[].{
      Instance:InstanceId,
      Endpoint:MetadataOptions.HttpEndpoint,
      Version:MetadataOptions.HttpTokens,
      Hops:MetadataOptions.HttpPutResponseHopLimit,
      Tags:MetadataOptions.InstanceMetadataTags,
      Applied:MetadataOptions.State,
      Env:Tags[?Key==`cyclone:environment`]|[0].Value
    }' --output table
```

What you want on every row:

| Field | Wanted | Console label | If it is wrong |
| ----- | ------ | ------------- | -------------- |
| `Endpoint` | `enabled` | Instance metadata service | IMDS is off entirely |
| `Hops` | `2` | Metadata token response hop limit | Containers cannot reach IMDS; the host still can |
| `Tags` | `enabled` | **Allow tags in instance metadata** | The tag lookup 404s however the instance is tagged |
| `Applied` | `applied` | — | Still `pending`; wait and re-run |
| `Env` | `production` | (instance tags, not metadata options) | The tag itself is missing or misspelled |

### The same question from the box, with no IAM at all

IMDS will answer this itself, which makes it the better check of the two: it
tests the thing the app actually does rather than the setting meant to enable
it.

```sh
TOKEN=$(curl -sX PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 60")

# Every tag this instance can see. 404 means instance-metadata-tags is off.
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/tags/instance/

# The one the app reads.
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/tags/instance/cyclone:environment
```

An empty `$TOKEN` is the hop limit. A `404` body from the first call is
`instance-metadata-tags` still disabled. Both together printing `production` is
the whole of what steps 1 and 2 were for.

Run it inside the container too — that is where the hop limit actually bites,
and the host answering proves nothing about the container.

`Version` is `required` if you ran the command as written, meaning IMDSv2 only.
That is a security improvement rather than a requirement — `secret_source` tries
for a v2 token and falls back to v1 if it cannot get one — but with `required`
set, a failed token request means the tag read gets a 401 rather than an answer,
which is another way the hop limit shows up.

Listing both instances in one call is the point: this is the same question
`scripts/check-fleet.sh` asks about application config, one layer down.

> If the colon in `cyclone:environment` causes trouble with any tooling you use,
> change `ENVIRONMENT_TAG` in `app/util/secret_source.py` and re-tag. The colon
> is legal in EC2 tag keys and in a URL path segment; it's just occasionally
> awkward in shell one-liners.

---

## Step 5 — Verify, before turning anything on

Run these **inside the container**, not on the host. The host almost always
works; the container is where the hop limit bites.

```sh
docker compose exec api sh -c '
  TOKEN=$(curl -sX PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
  echo "token: ${TOKEN:+ok}${TOKEN:-FAILED (hop limit?)}"
  echo "tag:   $(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
    http://169.254.169.254/latest/meta-data/tags/instance/cyclone:environment)"
'
```

Expect `token: ok` and `tag: production`. A blank token means the hop limit; a
404-ish body for the tag means metadata tags aren't enabled.

Then prove the role can actually read the parameters. **Check who you are
first** — if user credentials have since been configured on this box, the CLI
uses those instead of the instance role, and the test passes while the container
still fails:

```sh
aws sts get-caller-identity
# Expect: arn:aws:sts::...:assumed-role/EC2_DynamoDB_Plus_Streams/i-...
# A "user/..." arn means this is not measuring what the app will do.

aws ssm get-parameters-by-path --region us-east-1 \
  --path /cyclone/production/ --recursive \
  --query 'Parameters[].Name' --output table
```

Then the same question through the app itself:

```sh
docker compose exec api python -c "
from util import secret_source
print('environment:', secret_source.environment_name())
print('loaded:', secret_source.load(force=True))
"
```

It prints parameter **names only** — values are never logged or returned. An
empty list with no error means every name was already set in the environment,
which is expected at this stage because they're still in `.env`.

---

## Step 6 — Cut over, one secret at a time

Now turn the loader on. It still changes nothing, because nothing already in the
environment is overwritten — which is what makes this reversible in both
directions.

These go in **`.env` on each node**, not in `config/`: the production servers
carry only `.env` and `docker-compose.yml`, and compose reads `env_file` on the
host. All three are declared `Settings` fields, so `extra = "forbid"` accepts
them.

```
CYCLONE_SECRETS_SOURCE=ssm
AWS_REGION=us-east-1
CYCLONE_ENV=production
```

**`CYCLONE_ENV` is a safety net, not the primary path.** The environment name
comes from the EC2 instance tag, and the tag **wins** — the variable is only
consulted when the tag cannot be read. It is worth setting anyway for a first
cutover, because a failed tag lookup is a hard failure by design: the loader
refuses to guess and the container does not start. With the variable set, an
IMDS problem degrades to a log line instead of an outage.

You can tell which path was taken, which is the point of setting both:

```
secret_source: loaded 0 parameter(s) from /cyclone/production/ (9 already set, left alone)
```

means the tag worked. If the fallback was used, the line above it says so:

```
secret_source: using CYCLONE_ENV=production (no EC2 instance tag found)
```

**`loaded 0 ... (9 already set)` IS the success signal for this first step.**
Every secret is still in `.env`, and nothing is overwritten. It proves the
container reached SSM, resolved the environment, and read the path.

Then move secrets across **one at a time**, confirming after each:

1. Delete one line from the node's `.env` — on **both** nodes. Start with
   something low-stakes: `TELEGRAM_BOT_TOKEN`.
2. `docker compose up -d --force-recreate api worker`
3. The log should now say `loaded 1 parameter(s)`.
4. Confirm the fleet still agrees:
   ```sh
   ./check-fleet.sh https://cyclone.jdbot.us
   ```
   **The hash must NOT change.** The value is identical; only its source moved.
   A changed hash means the SSM value and the `.env` value differ — and it is
   worth learning that on the Telegram token rather than on the Anthropic key.

Do the Supabase and LLM keys last. A wrong LLM key does not fail: it fails the
candidate over to the next model in the chain and keeps answering, which is the
one failure here that produces evidence rather than an error.

### Rollback

Put the line back in `.env` and recreate the containers. The environment wins
over SSM by design, so this takes effect immediately and needs no AWS change.

### Reading the log when the tag lookup fails

The environment name comes from the EC2 tag, and there are **three** ways that
can fail. They used to look identical in the log — one returned `None` in
silence — and they have three different fixes, so each now says which it is.

| What the log says | What it means | What to do |
| ----------------- | ------------- | ---------- |
| `requests is not installed` | The running image predates `app/requirements.txt`. The tag path cannot work at all. | Rebuild and redeploy **api and worker** (§15: they share an image). |
| `could not reach IMDS ... (ConnectTimeout: ...)` | The packet is dying in transit. | Metadata hop limit below 2 — Step 4. |
| `could not reach IMDS ... (ConnectionError: ...)` | No route to 169.254.169.254 from inside the container. | Host networking or firewall, not a hop limit. |
| `instance has no cyclone:environment tag (HTTP 404)` | IMDS answered. The tag is not in metadata. | `instance-metadata-tags` is off, or the tag was deleted. See below. |
| `instance has no cyclone:environment tag (HTTP 401)` | IMDSv2 is required and the token request failed. | Check `HttpTokens` and the hop limit together. |

Whichever it is, `CYCLONE_ENV` covers it and the node still starts. That is the
point of setting it for a first cutover — but a warning on every boot is a real
finding, not noise: the tag exists so that no person types the environment name,
and falling back to the variable puts the typo surface back.

**Ask the container directly**, which is the only version of this question that
matters — the host can reach IMDS on hop limit 1 and the container cannot:

```sh
docker compose exec api python - <<'PY'
import requests
T = "http://169.254.169.254"
try:
    tok = requests.put(T + "/latest/api/token",
                       headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
                       timeout=2).text
    print("IMDSv2 token: ok")
    h = {"X-aws-ec2-metadata-token": tok}
    print("instance:", requests.get(T + "/latest/meta-data/instance-id",
                                    headers=h, timeout=2).text)
    r = requests.get(T + "/latest/meta-data/tags/instance", headers=h, timeout=2)
    print("tags:", r.status_code, r.text)
except Exception as e:
    print("FAILED:", type(e).__name__, e)
PY
```

A 404 on the last line with an instance id on the line above is the useful
result: metadata works, tags are switched off.

**A 404 has two causes and they look identical.** Either `instance-metadata-tags`
is disabled, or the tag itself is gone. Check the tag first — it is the cheaper
question, and deleting a batch of instance tags takes `cyclone:environment` with
them if it was in the batch:

```sh
# From an admin workstation. Does the tag exist at all?
aws ec2 describe-tags --region us-east-1   --filters "Name=resource-id,Values=<INSTANCE_ID>"              "Name=key,Values=cyclone:environment"   --query "Tags[].Value" --output text

# If it is missing, put it back.
aws ec2 create-tags --region us-east-1 --resources <INSTANCE_ID>   --tags Key=cyclone:environment,Value=production

# And make sure metadata carries tags (Step 4). Both are needed.
aws ec2 modify-instance-metadata-options --region us-east-1   --instance-id <INSTANCE_ID> --instance-metadata-tags enabled
```

Metadata options apply immediately; a tag added after boot appears within a
minute or so. Nothing needs restarting, but the container reads the tag once at
import — so recreate it to pick the tag up.

### The region is resolved by Cyclone, not by boto3

`AWS_REGION=us-east-1` was in `.env`, reached the process, and
`boto3.client("ssm")` still raised **`NoRegionError`**. Which environment
variables botocore consults for a region is a property of the pinned version,
and a container that starts or does not start must not turn on that. So
`secret_source.region()` answers it:

1. `AWS_REGION`
2. `AWS_DEFAULT_REGION`
3. IMDS `placement/region` — the same argument as the environment tag, since
   the instance knows where it is and nobody has to type it

Nothing resolvable raises, rather than defaulting. A guessed region reads a
parameter path that does not exist there and reports an **empty environment**,
which reads identically to a correct load of a path where everything is already
set — the confident-wrong failure this whole module exists to avoid.

With IMDS reachable, `AWS_REGION` in `.env` is belt and braces rather than a
requirement.

---

## Terraform, which is the version worth showing people

The CLI steps above are fine for two boxes. This is the one that makes
"provisioning code under review" true rather than aspirational — and it's the
five-second answer in a diligence conversation.

```hcl
variable "environment" { type = string }   # "production" | "dev"
variable "secrets" {
  type      = map(string)
  sensitive = true
  # Supply via a tfvars file that is NOT committed, or from your own secret
  # store. The point is that the PATH and POLICY are in git; the values are not.
}

resource "aws_ssm_parameter" "cyclone" {
  for_each = var.secrets

  name   = "/cyclone/${var.environment}/${each.key}"
  type   = "SecureString"
  value  = each.value
  key_id = "alias/aws/ssm"
  tier   = "Standard"

  # Terraform should own the path and the policy, not the rotation. A key
  # rotated in the console must not be reverted by the next apply.
  lifecycle { ignore_changes = [value] }
}

data "aws_iam_policy_document" "read_secrets" {
  statement {
    sid       = "ReadCycloneParameters"
    actions   = ["ssm:GetParametersByPath"]
    resources = ["arn:aws:ssm:${var.region}:${var.account_id}:parameter/cyclone/${var.environment}/*"]
  }
  statement {
    sid       = "DecryptThemViaSSMOnly"
    actions   = ["kms:Decrypt"]
    resources = ["arn:aws:kms:${var.region}:${var.account_id}:key/alias/aws/ssm"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.region}.amazonaws.com"]
    }
  }
}

resource "aws_instance" "cyclone" {
  # ... ami, type, subnet ...
  iam_instance_profile = aws_iam_instance_profile.cyclone.name

  # BOTH of these are required and neither is the default. Without the hop
  # limit the containers cannot reach IMDS at all; without the tags option the
  # tag below is invisible to the instance.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
    instance_metadata_tags      = "enabled"
  }

  tags = {
    "cyclone:environment" = var.environment
  }
}
```

`ignore_changes = [value]` is the important line. Without it, rotating a key in
the console and then running `terraform apply` silently reverts it.

---

## Cost, and the service this is NOT

**Parameter Store and Secrets Manager are different products**, and it is the
other one that costs money. Everything here uses Parameter Store.

| | Storage | API calls |
| - | - | - |
| **SSM Parameter Store, Standard** ← what this uses | **free** | **free** |
| SSM Parameter Store, Advanced | $0.05/parameter/month | $0.05 / 10,000 |
| AWS Secrets Manager | $0.40/secret/month | $0.05 / 10,000 |
| KMS with `alias/aws/ssm` | free key | $0.03 / 10,000 decrypts |
| KMS with a customer-managed key | $1/month | same per-call rate |

Thirteen credentials would be **$5.20/month** in Secrets Manager and **$0** in
Parameter Store. The app reads each parameter once per container start — two
nodes, two containers, a few deploys a week — so the KMS decrypts come to a few
cents a year. Advanced tier buys 8 KB parameters and parameter policies; every
credential here is a few hundred bytes.

**Why not Secrets Manager, beyond the price.** Its headline feature is automatic
rotation, and it cannot rotate a single secret in this system. It rotates things
AWS can talk to itself — RDS, Redshift, DocumentDB — by invoking a Lambda that
knows the protocol. There is no such mechanism for an Anthropic key, a Stripe
key, or an IMAP password: those rotate at the vendor, by a person, and then the
new value lands here. The $0.40 buys a feature that does not apply.

What it *would* buy is resource-based policies (a policy attached to the secret
rather than to the reader) and cross-region replication. Neither is worth
$5.20/month for two instances in one region — but both are real, so if this ever
spans regions or grants access to an outside party, that is the moment to
revisit rather than now.

Prices are as at 2026-09; worth confirming before quoting them to anyone.

---

## Operational notes

**Rotating a key** — `aws ssm put-parameter --overwrite` and recreate the
containers. Nothing is cached beyond process lifetime, and nothing is written to
disk. This is the ability the whole exercise was for.

**Who read a secret** — CloudTrail records every `GetParametersByPath` with the
assumed role and instance id. With a customer-managed key you also get separate
`Decrypt` events.

**Adding a node** — attach the instance profile, set the tag, set the two
metadata options. No secret is copied anywhere, which is the point: adding a
server is no longer an occasion to hand someone a credential.

**A second environment** — same steps with `ENV=dev` and a
`CycloneSSMReadDev` policy. The path scoping means a dev role cannot read
production parameters even if the instance is mis-tagged.

**What the app does when this is misconfigured** — it raises and does not start.
That is deliberate: a worker with no Anthropic key doesn't fail, it silently
produces nothing, and a container that refuses to boot is far easier to notice.
