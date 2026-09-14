"""
scripts/seed_ssm.py - Push the secrets in .env to SSM Parameter Store.

Step 1 of docs/aws-ssm-setup.md, done by machine rather than by hand. Typing
thirteen credentials into thirteen `aws ssm put-parameter` invocations invites
exactly one kind of mistake — a truncated paste — and that mistake does not
announce itself. A wrong LLM key fails the candidate over to the next model in
the chain and keeps answering; a wrong Stripe key waits until somebody pays.

So the values come from the file that is already known good, and the script
never handles them as anything but bytes to send.

WHAT IT WILL AND WILL NOT TOUCH
────────────────────────────────────────────────────────────────────────────
Only names in ``config_spec.SECRET``. That list is the same one the fingerprint
hashes and the same one the tests assert is complete, so there is no second
place to keep it in step with. Infrastructure, fleet settings and per-firm
values are deliberately left in the environment: SSM is for credentials.

NO VALUE IS EVER PRINTED, including on failure, including with --apply. What is
printed is the same twelve-character digest the fingerprint publishes, which is
enough to see that the local value and the stored one agree without either
appearing on a terminal that is probably being recorded.

DRY RUN IS THE DEFAULT
────────────────────────────────────────────────────────────────────────────
Running it shows what would change and writes nothing. --apply is a separate,
deliberate act. Every write is reported by name.

    python scripts/seed_ssm.py --region us-east-1 --env production
    python scripts/seed_ssm.py --region us-east-1 --env production --apply

WHERE TO RUN IT
────────────────────────────────────────────────────────────────────────────
On the node, from the checkout, if boto3 is importable there:

    python3 -c "import boto3"      # the aws CLI bundles its own botocore,
                                   # which is NOT the same thing

Otherwise inside the container, which has boto3 because the loader needs it,
and where compose has already mounted .env read-only:

    docker compose -f docker-compose.yml run --rm --no-deps \
      -v "$PWD/scripts:/scripts:ro" \
      api python /scripts/seed_ssm.py --region us-east-1 --env production

`-f docker-compose.yml` is not optional: docker compose silently applies
docker-compose.override.yml when it is present, which would pull in the dev
environment file. Seeding still works — .env is last in both layerings — but
matching production is the only reason to run this in a container at all.

Either way the credentials come from the instance role via IMDS: nothing to
configure, no key to supply. Running it in the container also proves the
CONTAINER can reach IMDS, which is where the hop limit bites and is the same
path the loader takes at cutover.
"""
import argparse
import hashlib
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# WHERE THIS RUNS IS NOT FIXED, so the app package is looked for rather than
# assumed. On a node it runs from the checkout, with the code at ./app. Inside
# a container it runs from a mounted /scripts, with the code already at /app and
# no checkout above it. Resolving both keeps the container invocation working on
# purpose instead of by an accident of how a relative path happens to land.
for _candidate in (os.path.join(ROOT, "app"), "/app"):
    if os.path.isdir(os.path.join(_candidate, "util")):
        sys.path.insert(0, _candidate)
        break
else:  # noqa: PLW0120
    print("Cannot find the app package. Run this from the repository root, or "
          "inside the container with the code at /app.", file=sys.stderr)
    raise SystemExit(1)

from util.config_spec import SECRET  # noqa: E402


def digest(value: str) -> str:
    """
    The same twelve characters config_fingerprint publishes for a secret.

    json.dumps rather than manual quoting, because that is what
    ``config_fingerprint._canonical`` uses — and a value containing a quote or
    a backslash would otherwise digest differently here than on the running
    node, which turns this column from a cross-check into a puzzle.
    """
    if not value:
        return "<unset>"
    return hashlib.sha256(json.dumps(value).encode("utf-8")).hexdigest()[:12]


def read_env(path: str) -> dict:
    """
    Parse a .env the way pydantic-settings does, near enough.

    Blank lines and comments are skipped; surrounding quotes are stripped,
    because a value written KEY="abc" means abc and storing the quotes would
    produce a credential that is wrong by two characters — the most annoying
    possible amount.
    """
    found = {}
    with io.open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip().lower()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            found[name] = value
    return found


def existing(client, prefix: str) -> dict:
    """Everything already under the path, as {NAME: value}."""
    out = {}
    paginator = client.get_paginator("get_parameters_by_path")
    for page in paginator.paginate(Path=prefix, Recursive=True, WithDecryption=True):
        for parameter in page.get("Parameters", []):
            out[parameter["Name"].rsplit("/", 1)[-1]] = parameter["Value"]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", required=True)
    parser.add_argument("--env", required=True,
                        help="Environment name; must match the instance's "
                             "cyclone:environment tag, e.g. production")
    # Candidates in the order they are worth trying. `/app/.env` is where
    # compose mounts it for the RUNNING api container; a `docker compose run`
    # container does not reliably get that mount, which is why /host is here
    # too — it is the conventional mount point in the runbook's invocation.
    candidates = [os.path.join(ROOT, ".env"), "/app/.env", "/host/.env"]
    default_env = next((c for c in candidates if os.path.exists(c)), candidates[0])
    parser.add_argument("--file", default=default_env,
                        help="Path to the .env holding the secrets. Tried in "
                             "order: %s" % ", ".join(candidates))
    parser.add_argument("--key-id", default="alias/aws/ssm",
                        help="KMS key. The default is the free account-managed one")
    parser.add_argument("--apply", action="store_true",
                        help="Actually write. Without it, nothing changes")
    args = parser.parse_args()

    if not os.path.exists(args.file):
        # Say what was looked for, not just what was missing. Inside a container
        # the answer is almost always "pass --file", and a bare "no such file"
        # sends somebody hunting for a mount that is working fine.
        print("No .env found. Looked for:", file=sys.stderr)
        for candidate in candidates:
            print("    %s" % candidate, file=sys.stderr)
        print("", file=sys.stderr)
        print("Pass --file explicitly. Running in a container with the host "
              "directory mounted at /host, that is:", file=sys.stderr)
        print("    --file /host/.env", file=sys.stderr)
        return 1

    import boto3

    local = read_env(args.file)
    prefix = "/cyclone/%s/" % args.env
    client = boto3.client("ssm", region_name=args.region)

    try:
        stored = existing(client, prefix)
    except Exception as e:  # noqa: BLE001
        print("Could not read %s: %s" % (prefix, e), file=sys.stderr)
        return 1

    print("%s  (%s)" % (prefix, args.file))
    print("%s" % ("-" * 74))
    print("%-44s %-12s %s" % ("PARAMETER", "LOCAL", "ACTION"))

    to_write, unchanged, empty = [], 0, []
    for field in sorted(SECRET):
        name = field.upper()
        value = local.get(field, "")
        if not value:
            empty.append(name)
            continue
        was = stored.get(name)
        if was is None:
            action = "create"
            to_write.append((name, value))
        elif was != value:
            action = "UPDATE (differs from stored)"
            to_write.append((name, value))
        else:
            action = "match"
            unchanged += 1
        print("%-44s %-12s %s" % (name, digest(value), action))

    if empty:
        print("")
        print("Not in %s, so not sent — each is a feature that is simply off:" % args.file)
        for name in empty:
            print("    %s" % name)

    orphans = sorted(set(stored) - {f.upper() for f in SECRET})
    if orphans:
        print("")
        print("Already in SSM but NOT a known secret. Left alone; check whether")
        print("they are stale, because the loader will still set them:")
        for name in orphans:
            print("    %s" % name)

    print("")
    if not to_write:
        print("Nothing to do — %d parameter(s) already match." % unchanged)
        return 0

    if not args.apply:
        print("DRY RUN — %d parameter(s) would be written, %d already match."
              % (len(to_write), unchanged))
        print("Re-run with --apply to write them.")
        return 0

    for name, value in to_write:
        client.put_parameter(
            Name=prefix + name, Value=value, Type="SecureString",
            KeyId=args.key_id, Tier="Standard", Overwrite=True,
        )
        print("wrote %s" % name)   # name only, never the value
    print("")
    print("%d written, %d already matched." % (len(to_write), unchanged))
    print("")
    print("Nothing has changed on the running app yet: the loader is off until")
    print("CYCLONE_SECRETS_SOURCE=ssm, and even then it never overwrites a value")
    print("already in the environment. Move secrets out of .env one at a time.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
