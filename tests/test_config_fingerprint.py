"""
tests/test_config_fingerprint.py - Can two servers be shown to agree?

Drift between two nodes' `.env` files was found by accident. The fix is not a
different place to keep the file, it is being able to ask two running servers
whether they agree — which needs a hash over the settings that are SUPPOSED to
be identical, and not over the ones that are supposed to differ.

The most valuable check here is the dullest: **`.env.example` must boot.** It
did not. `Settings` sets `extra = "forbid"`, and the example file carried
`SUPABASE_JWT_SECRET` and `SUPABASE_ANON_JWT_SECRET`, which are not fields. Copy
it to a new server and the container dies on import. The document whose whole
job is preventing configuration mistakes was one.

Run:  venv/Scripts/python.exe tests/test_config_fingerprint.py
"""
import io
import os
import re
import sys
from datetime import date

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

from util.config_fingerprint import (  # noqa: E402
    LEGACY_KEY_SUNSET, UNSET, deprecated_in_use, detailed_fingerprint, digest,
    field_digests, fleet_hash, legacy_supabase_keys, misplaced_secrets,
    public_fingerprint,
)
from util.config_spec import (  # noqa: E402
    ALL_BUCKETS, COMPOSE_ONLY, DEPRECATED, DEPRECATION_REASON, FINGERPRINTED,
    COMPOSE_INTERPOLATED, COMPOSE_REQUIRED, INFRASTRUCTURE, SECRET,
    bucket_of, overlapping, unclassified,
)
from util.settings import Settings  # noqa: E402

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got:  %r\n         want: %r" % (label, got, want))
        FAILURES.append(label)


def check_true(label: str, got) -> None:
    check(label, bool(got), True)


def settings(**overrides) -> Settings:
    """A Settings built from defaults plus explicit overrides, ignoring any .env."""
    return Settings(_env_file=None, **overrides)


# ── The classification has to be total ───────────────────────────────────────

print("Every setting is classified exactly once")

fields = set(Settings.model_fields)
check("nothing is unclassified", unclassified(fields), [])
check("nothing is in two buckets", overlapping(), [])

classified = set().union(*ALL_BUCKETS.values())
check("no bucket names a setting that does not exist", sorted(classified - fields), [])

# THE GUARANTEE. A setting added without a decision about what it is cannot
# ship: is it fleet-identical, a credential, per-firm, or per-node? Answering
# that is what makes a fingerprint mean anything.
check("the buckets cover the model exactly", classified, fields)


# ── What is hashed, and what deliberately is not ─────────────────────────────

print("\nOnly what should be identical is hashed")

# A fingerprint over `id` would differ between any two machines, forever, on
# purpose — a drift report that always fires is one nobody reads.
for field in ("id", "host_url", "supabase_url", "redis_url"):
    check_true("%s is excluded from the hash" % field, field not in FINGERPRINTED)
    check_true("  and is classified as infrastructure", field in INFRASTRUCTURE)

base = settings()
moved = settings(id="ec2-other-box", host_url="https://other.example.com")
check("two nodes differing only by identity still agree",
      fleet_hash(base), fleet_hash(moved))

drifted = settings(llm_temperature=0.9)
check_true("a drifted model temperature is caught", fleet_hash(base) != fleet_hash(drifted))
check_true("so is a drifted job concurrency",
           fleet_hash(base) != fleet_hash(settings(job_concurrency=9)))
check_true("so is a drifted log level",
           fleet_hash(base) != fleet_hash(settings(log_level="DEBUG")))


# ── Secrets are proved equal without being printed ───────────────────────────

print("\nA secret is compared, never revealed")

SECRET_VALUE = "sb_secret_thisIsTheRealKeyAndMustNotAppear"
with_key = settings(anthropic_api_key=SECRET_VALUE)

published = "%r %r %r" % (
    field_digests(with_key), public_fingerprint(with_key), detailed_fingerprint(with_key))
check("the value appears nowhere in anything published",
      SECRET_VALUE in published, False)
check_true("but two nodes holding it agree",
           field_digests(with_key)["anthropic_api_key"]
           == field_digests(settings(anthropic_api_key=SECRET_VALUE))["anthropic_api_key"])
check_true("and a node holding a different one does not",
           field_digests(with_key)["anthropic_api_key"]
           != field_digests(settings(anthropic_api_key="other"))["anthropic_api_key"])

# "Both nodes have no Stripe key" and "both nodes have the SAME Stripe key" are
# different states, and the more dangerous one is the first. A scheme that
# collapsed them would hide it.
check("an unset secret is its own marker", digest(""), UNSET)
check_true("which is not a digest", digest("x") != UNSET)

check_true("every credential is in the SECRET bucket",
           {"anthropic_api_key", "stripe_secret_key", "imap_password",
            "smtp_password", "telegram_bot_token",
            "supabase_service_role_key"} <= SECRET)

# The public summary is a hash, a build, and three counts. Naming the SETTINGS
# publicly would inventory the deployment for anyone who asks -- which is what
# this pins: the key set is asserted exactly, so a field name cannot be added to
# the unauthenticated endpoint without this test failing and somebody deciding.
#
# `build` is a deliberate member. It is an image tag, not a setting, and it has
# to be readable without a credential for the same reason the hash does: the
# check exists so that nothing stands between somebody and running it. The
# endpoint already publishes `version` and `id`.
check("the public summary lists no field names",
      sorted(public_fingerprint(base)),
      ["build", "deprecated_in_use", "fields", "fleet_hash",
       "legacy_supabase_keys", "misplaced_secrets"])

# BUILD IS REPORTED BESIDE THE HASH AND NEVER INSIDE IT. Two nodes on different
# images must still be comparable on configuration -- that is what makes a node
# deliberately left on an older build a usable CONTROL when migrating secrets
# into SSM, since a matching hash then proves the value moved byte-for-byte.
# Folding the build in would destroy that and make every release read as drift.
check("the build does not change the fleet hash",
      fleet_hash(settings(cyclone_build="1.0.3")),
      fleet_hash(settings(cyclone_build="9.9.9")))
check("an unstamped image reports so rather than looking like agreement",
      public_fingerprint(base)["build"], "unknown")
check("and a stamped one reports its tag",
      public_fingerprint(settings(cyclone_build="1.0.4"))["build"], "1.0.4")
check("cyclone_build is classified, and not as fingerprinted",
      (bucket_of("cyclone_build"), "cyclone_build" in FINGERPRINTED),
      ("build", False))


# ── Dead settings: warned about, not guessed at ──────────────────────────────

print("\nDeprecated settings")

# db_type defaults to "supabase" and supabase_max_rows to 1000, so testing the
# VALUE reported them on every node forever. The question is whether somebody
# set it, and pydantic records that.
check("a default is not 'in use'", deprecated_in_use(settings()), {})
check("a value somebody supplied is",
      list(deprecated_in_use(settings(db_type="supabase"))), ["db_type"])
check_true("and it says why it is dead",
           "SupabaseManager" in deprecated_in_use(settings(db_type="supabase"))["db_type"])
check("every deprecated setting has a reason",
      sorted(DEPRECATED - set(DEPRECATION_REASON)), [])
check("none of them is hashed", sorted(DEPRECATED & FINGERPRINTED), [])


# ── The Supabase key deadline ────────────────────────────────────────────────

print("\nLegacy Supabase keys")

check("2026-12-31 is the sunset", LEGACY_KEY_SUNSET, date(2026, 12, 31))

legacy = settings(supabase_service_role_key="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.aaa")
found = legacy_supabase_keys(legacy, today=date(2026, 9, 13))
check("a JWT-format key is spotted by its format, not by a flag",
      [f["variable"] for f in found], ["SUPABASE_SERVICE_ROLE_KEY"])
check("with the days left", found[0]["days_remaining"], 109)
check("and what to replace it with", found[0]["replace_with"], "sb_secret_...")
check("not overdue yet", found[0]["overdue"], False)
check("overdue after the date",
      legacy_supabase_keys(legacy, today=date(2027, 1, 1))[0]["overdue"], True)

check("a new-format key is not reported",
      legacy_supabase_keys(settings(supabase_service_role_key="sb_secret_abc123")), [])
check("nor is an unset one", legacy_supabase_keys(settings()), [])
check("the anon key is told to become publishable, not secret",
      legacy_supabase_keys(settings(supabase_anon_key="eyJhbGciOi.bbb"))[0]["replace_with"],
      "sb_publishable_...")

# The migration is a change of VALUE in the same variable: db_handler's
# SupabaseManager reads os.environ["SUPABASE_SERVICE_ROLE_KEY"] itself and is a
# third-party package. A second field naming the same credential would be two
# places for the fleet to disagree about which one is live.
check("there is no second field for the new key format",
      [f for f in Settings.model_fields if "secret_key" in f and "supabase" in f], [])


# ── A secret in a slot that reaches a browser ────────────────────────────────

print("\nSecret keys in publishable slots")

# NOT HYPOTHETICAL. A `sb_secret_` value was found in
# SUPABASE_LANDING_PAGES_ANON_KEY, where it was harmless only because nothing
# reads that variable. The two checked here are different: SUPABASE_ANON_KEY is
# compiled into the JavaScript bundle by the docker build, and
# STRIPE_PUBLISHABLE_KEY is served from the unauthenticated GET /api/config. The
# same paste ships the credential to every visitor.
check("a clean config reports nothing", misplaced_secrets(settings()), [])

leaked = misplaced_secrets(settings(supabase_anon_key="sb_secret_abc123"))
check("a secret key in the anon slot is caught",
      [f["variable"] for f in leaked], ["SUPABASE_ANON_KEY"])
check_true("and it says where the value ends up",
           "frontend bundle" in leaked[0]["exposure"])

check("a publishable key there is fine",
      misplaced_secrets(settings(supabase_anon_key="sb_publishable_abc123")), [])
# Legacy JWT anon keys are still valid until the 2026 sunset; that is the other
# check's business, not this one's.
check("so is a legacy JWT anon key, which is a different finding",
      misplaced_secrets(settings(supabase_anon_key="eyJhbGciOi.x")), [])

for bad in ("sk_live_51abc", "sk_test_51abc"):
    check("Stripe %s in the publishable slot is caught" % bad[:8],
          [f["variable"] for f in misplaced_secrets(settings(stripe_publishable_key=bad))],
          ["STRIPE_PUBLISHABLE_KEY"])
check("a real publishable Stripe key is fine",
      misplaced_secrets(settings(stripe_publishable_key="pk_live_51abc")), [])

# Both at once, and both named — fixing one and missing the other is the
# obvious way to half-solve this.
both = misplaced_secrets(settings(supabase_anon_key="sb_secret_a",
                                  stripe_publishable_key="sk_live_b"))
check("both slots are reported, not just the first",
      sorted(f["variable"] for f in both),
      ["STRIPE_PUBLISHABLE_KEY", "SUPABASE_ANON_KEY"])

# It rides on the public summary as a COUNT only. Naming the variable there
# would tell an anonymous caller exactly which credential to go looking for in
# the bundle.
summary = public_fingerprint(settings(supabase_anon_key="sb_secret_abc123"))
check("the public summary counts it", summary["misplaced_secrets"], 1)
check_true("without naming it", "SUPABASE" not in repr(summary))


# ── The dullest and most valuable check ──────────────────────────────────────

print("\n.env.example must actually boot")

# It did not. `extra = "forbid"` refuses a key the model does not declare, so
# every line in the example file is a promise that a node copying it starts.
# Two shapes: `KEY=` must be filled in, `# KEY=default` documents an optional
# override. Both are checked against the model — a typo in a commented default
# is a line somebody will uncomment one day, and it should fail here first.
example_keys, commented_keys = [], []
with io.open(os.path.join(ROOT, ".env.example"), encoding="utf-8") as handle:
    for line in handle:
        line = line.strip()
        if not line or "=" not in line:
            continue
        if line.startswith("#"):
            candidate = line.lstrip("#").strip().split("=", 1)[0].strip()
            # Only prose that looks like a KEY=..., not every commented sentence.
            if re.fullmatch(r"[A-Z][A-Z0-9_]*", candidate):
                commented_keys.append(candidate.lower())
        else:
            example_keys.append(line.split("=", 1)[0].strip().lower())

check("no duplicate keys in the example", len(example_keys), len(set(example_keys)))

unknown = sorted(set(example_keys) - fields)
check(".env.example names no key Settings would refuse", unknown, [])
check("nor does a commented default name one",
      sorted(set(commented_keys) - fields), [])

# The reverse is not an error — a setting with a sane default need not be in
# the example — but a SECRET missing from it is: somebody provisioning a node
# from this file would not know to set it.
missing_secrets = sorted(SECRET - set(example_keys) - set(commented_keys))
check("every credential is listed in the example", missing_secrets, [])

check("the compose-only keys are listed too, since compose reads them",
      sorted(COMPOSE_ONLY - set(example_keys) - set(commented_keys)), [])

# Proof rather than inference: build a Settings from the example file itself.
example_path = os.path.join(ROOT, ".env.example")
try:
    Settings(_env_file=example_path)
    booted = True
except Exception as e:  # noqa: BLE001 — the assertion is that this never happens
    booted = False
    print("       %s" % str(e).splitlines()[0])
check_true("a node provisioned from .env.example starts", booted)


# ── Secrets from SSM: the instance identity is the credential ───────────────

print("\nSSM secret loading")

import types  # noqa: E402

from util import secret_source  # noqa: E402


class FakeSSM:
    """Stands in for boto3's SSM client. Records what was asked for."""

    def __init__(self, parameters, explode=False):
        self._parameters = parameters
        self._explode = explode
        self.paths: list[str] = []

    def get_paginator(self, _name):
        outer = self

        class Paginator:
            def paginate(self, Path, Recursive, WithDecryption):  # noqa: N803
                outer.paths.append(Path)
                if outer._explode:
                    raise RuntimeError("AccessDenied")
                yield {"Parameters": [
                    {"Name": Path + name, "Value": value}
                    for name, value in outer._parameters.items()
                ]}

        return Paginator()


def with_fake_ssm(parameters, explode=False, env=None):
    """Run load(force=True) against a fake SSM and return (loaded, environ, fake)."""
    fake = FakeSSM(parameters, explode=explode)
    saved_env = dict(os.environ)
    saved_modules = dict(sys.modules)
    # A region, because `load` now resolves one and passes it explicitly rather
    # than leaving botocore to scan the environment for it. Off EC2 there is no
    # metadata to fall back to, so without this the helper fails on the region
    # before it reaches anything it is testing.
    os.environ.setdefault("AWS_REGION", "us-east-1")
    os.environ.update(env or {})
    # Neither boto3 nor a real instance exists here. The tag lookup is stubbed
    # rather than mocked over the network: the point under test is the ORDER of
    # resolution, not HTTP.
    sys.modules["boto3"] = types.SimpleNamespace(
        client=lambda _service, region_name=None: fake)
    original_tag = secret_source.instance_environment
    try:
        secret_source.instance_environment = lambda: (env or {}).get("_TAG")
        loaded = secret_source.load(force=True)
        return loaded, dict(os.environ), fake
    finally:
        secret_source.instance_environment = original_tag
        os.environ.clear()
        os.environ.update(saved_env)
        sys.modules.clear()
        sys.modules.update(saved_modules)


# OFF UNLESS ASKED. The whole point of the rollout being safe is that this file
# changes nothing on a node that has not opted in.
check("loading is off by default", secret_source.load(), [])
check("and on only for the exact value",
      secret_source.enabled(), False)

loaded, environ, fake = with_fake_ssm(
    {"ANTHROPIC_API_KEY": "from-ssm", "STRIPE_SECRET_KEY": "also-ssm"},
    env={"_TAG": "production"})
check("parameters land in the environment", sorted(loaded),
      ["ANTHROPIC_API_KEY", "STRIPE_SECRET_KEY"])
check("under the environment's own path", fake.paths, ["/cyclone/production/"])
check("with their values", environ["ANTHROPIC_API_KEY"], "from-ssm")

# NOTHING ALREADY SET IS REPLACED. A value in the environment came from compose,
# the node's .env, or a human debugging at 2am -- each a more specific intent
# than a fleet-wide default. It is also what makes the rollout reversible in
# both directions.
loaded, environ, _ = with_fake_ssm(
    {"ANTHROPIC_API_KEY": "from-ssm"},
    env={"_TAG": "production", "ANTHROPIC_API_KEY": "already-here"})
check("an existing value is left alone", environ["ANTHROPIC_API_KEY"], "already-here")
check("and is not reported as loaded", loaded, [])

# FAILURE IS LOUD. A worker that boots without an Anthropic key does not fail --
# it silently produces nothing, which is the shape of failure this codebase
# spends its effort avoiding.
raised = False
try:
    with_fake_ssm({"X": "y"}, explode=True, env={"_TAG": "production"})
except secret_source.SecretSourceError as e:
    raised = True
    check_true("and the message says the node is not starting", "not starting" in str(e))
check_true("an unreadable SSM raises rather than booting half-configured", raised)

# THE ENVIRONMENT NAME IS NOT TYPED BY A HUMAN. A CONFIG_SET line in .env is the
# same drift surface, one line instead of forty-eight, and harder to diagnose.
# The instance tag wins; CYCLONE_ENV is the escape hatch for anything not on EC2.
saved = dict(os.environ)
original_tag = secret_source.instance_environment
try:
    secret_source.instance_environment = lambda: "production"
    os.environ["CYCLONE_ENV"] = "staging"
    check("the EC2 tag beats an explicit override",
          secret_source.environment_name(), "production")

    secret_source.instance_environment = lambda: None
    check("the override is used when there is no tag",
          secret_source.environment_name(), "staging")

    os.environ.pop("CYCLONE_ENV")
    refused = False
    try:
        secret_source.environment_name()
    except secret_source.SecretSourceError:
        refused = True
    check_true("and neither source means it refuses to guess", refused)
finally:
    secret_source.instance_environment = original_tag
    os.environ.clear()
    os.environ.update(saved)

# The two switches are per-node by definition -- a laptop reads .env while an
# EC2 box reads SSM -- so they must not read as drift.
# A COMPOSE_ONLY value cannot come from SSM, and the loader must say so rather
# than apply it. compose interpolates ${SUPABASE_ANON_KEY} ON THE HOST, before
# the container exists; this loader runs inside it. Moving that key to SSM makes
# compose substitute an empty string, builds a frontend with no anon key, and
# fails in a BROWSER AT LOGIN -- nothing in the API log, nothing on the fleet
# hash, and a container reporting healthy.
raised = None
try:
    with_fake_ssm({"SUPABASE_ANON_KEY": "sb_publishable_xxx"},
                  env={"_TAG": "production"})
except secret_source.SecretSourceError as e:
    raised = str(e)
check_true("a compose-only value in SSM stops the node", raised is not None)
check_true("and the message says compose reads it on the host",
           raised and "ON THE HOST" in raised)
check_true("and says to delete the parameter",
           raised and "delete the parameter" in raised)

# The ordinary case must be untouched by the guard.
loaded, environ, _fake = with_fake_ssm(
    {"SUPABASE_SERVICE_ROLE_KEY": "from-ssm"}, env={"_TAG": "production"})
check("the sibling service-role key is NOT compose-only and loads normally",
      loaded, ["SUPABASE_SERVICE_ROLE_KEY"])

# ── What docker-compose.yml reads on the HOST ────────────────────────────────
#
# THE FILE THAT DOES THE INTERPOLATING IS THE ONLY HONEST SOURCE OF TRUTH, and
# a hand-maintained list was not it. `_refuse_if_compose_reads_it` keyed on the
# COMPOSE_ONLY bucket, which holds SUPABASE_ANON_KEY and API_BASE_URL. It does
# NOT hold SUPABASE_URL -- that is INFRASTRUCTURE, because the API needs it at
# runtime too -- and compose interpolates it all the same.
#
# So when SUPABASE_URL was removed from `.env` during the SSM migration, the
# next build substituted an empty string into VITE_SUPABASE_URL and shipped a
# bundle that threw `supabaseUrl is required.` at module scope. Blank white page
# on every route, container healthy, nothing in any server log.
print("")
print("Every ${VARIABLE} in docker-compose.yml is accounted for")

compose = io.open(os.path.join(ROOT, "docker-compose.yml"), encoding="utf-8").read()
# `${NAME}` and `${NAME:-default}` alike; the name is what matters.
interpolated = {m.lower() for m in re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", compose)}

check("the declared set matches the file exactly",
      sorted(COMPOSE_INTERPOLATED), sorted(interpolated))

# Each one must also be a Settings field, or `extra = "forbid"` refuses the
# line the moment somebody sets it in .env -- the API_BASE_URL trap.
undeclared = sorted(n for n in COMPOSE_INTERPOLATED
                    if n not in Settings.model_fields)
check("and every one is a declared Settings field", undeclared, [])

# The ones with no compose-side default must be REQUIRED lines in the example
# (`NAME=`), not commented ones. A commented default reads as optional, and
# these are the opposite of optional: absent, they build a broken image.
example = io.open(os.path.join(ROOT, ".env.example"), encoding="utf-8").read()
for name in sorted(COMPOSE_REQUIRED):
    upper = name.upper()
    required_shape = re.search(r"(?m)^%s=\s*$" % re.escape(upper), example)
    check_true(".env.example marks %s as required to fill in" % upper,
               required_shape is not None)

check_true("COMPOSE_REQUIRED is a subset of what compose actually reads",
           COMPOSE_REQUIRED <= COMPOSE_INTERPOLATED)


check("the source switches are not fingerprinted",
      sorted({"cyclone_secrets_source", "cyclone_env"} & FINGERPRINTED), [])


print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all config-fingerprint checks passed")
