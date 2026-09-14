"""
app/util/secret_source.py - Secrets from SSM, with no stored credential.

This is the one thing neither Supabase option could do. Edge Function secrets
are readable only by ``Deno.env.get()`` inside a function on Supabase's own
infrastructure; Vault is reachable, but reading it needs the service-role key,
so the bootstrap secret remains — and it becomes a master key that opens both
the database and every downstream credential, which are two independent
compromises today.

**On EC2 the instance identity IS the credential.** An IAM instance role means
boto3 obtains short-lived credentials from the instance metadata service with
nothing stored on disk, and the role's policy decides which parameter path this
node may read. Nothing to leak, nothing to rotate, nothing to typo.

THE PATH IS NOT TYPED BY A HUMAN
────────────────────────────────────────────────────────────────────────────
The obvious design — a ``CONFIG_SET=production-a`` line in ``.env`` — recreates
the problem it is meant to solve. It is the same drift surface, one line instead
of forty-eight, and *harder to diagnose*: today two files can be diffed, whereas
a central store keyed on a hand-typed identifier hides disagreement until
behaviour differs.

So the environment name is read from the instance's own **EC2 tag**, set by
provisioning code at launch and never by a person at a prompt. ``CYCLONE_ENV``
exists as an escape hatch for a laptop or a container that is not on EC2, and
using it is a deliberate act rather than the default path.

FAILURE IS LOUD, AND ONLY WHEN IT MATTERS
────────────────────────────────────────────────────────────────────────────
Loading is **opt-in** (``CYCLONE_SECRETS_SOURCE=ssm``) and never overwrites a
value already in the environment, so a node keeps working exactly as it does
today until somebody turns it on. If SSM is unreachable the process raises
rather than starting with half its credentials: a worker that boots without an
Anthropic key does not fail, it produces *nothing*, quietly, which is the shape
of failure this codebase spends its effort avoiding.
"""
import os
import sys
from typing import Any, Optional

#: THE LOGGER IS BUILT LAZILY, and it has to be. `util.settings` imports this
#: module above its own singleton, `LoggerFactory` imports `settings` for the
#: level, and importing LoggerFactory here at module scope closes that loop:
#:
#:     settings -> secret_source -> loggerfactory -> settings
#:
#: Resolving it the other way -- calling `logging.getLogger` directly -- is
#: forbidden by §6, and for a good reason: LoggerFactory sets the format, the
#: level and `propagate = False`, and a module that opts out of it logs
#: differently from everything around it.
_LOGGER: Any = None


class _BootstrapLogger:
    """
    A logger-shaped object for the window in which no real logger can exist.

    **A lazy import breaks the cycle at module scope and does NOT survive being
    called from inside it**, which is the distinction that crashed the worker at
    cutover. The process usually reaches `util.settings` THROUGH
    `util.loggerfactory` -- its line 6 imports `settings` for the log level --
    so by the time this module wants to log, `util.loggerfactory` is a
    *partially initialized* module: `LoggerFactory` is not bound yet, and
    importing it raises `ImportError` rather than recursing. That was an
    unhandled exception during settings construction, so the container did not
    start at all:

        crm_worker -> dependencies -> repositories.user_role
            -> util.loggerfactory (line 6)
            -> util.settings (line 23: secret_source.load())
            -> environment_name -> instance_environment
            -> _log() -> ImportError

    Writing to stderr instead is not a workaround for a missing logger; at that
    instant a configured logger genuinely cannot exist, and the alternative is
    to say nothing about the one part of the boot that can stop the boot.
    Docker sends stderr to the same place the logger would, so the operator
    reading `docker compose logs` sees these lines in order with the rest.

    DEBUG is dropped rather than printed: the log level lives in the `Settings`
    object that is still being built, so honouring it is impossible and
    ignoring it would print debug output on a node configured for WARNING.
    """

    @staticmethod
    def _emit(level: str, message: str, *args: Any) -> None:
        try:
            print("%s:%s:%s" % (level, __name__, message % args if args else message),
                  file=sys.stderr, flush=True)
        except Exception:  # noqa: BLE001 -- a failed log must never fail a boot
            pass

    def debug(self, message: str, *args: Any) -> None:
        """Dropped. The configured level is not knowable this early."""

    def info(self, message: str, *args: Any) -> None:
        self._emit("INFO", message, *args)

    def warning(self, message: str, *args: Any) -> None:
        self._emit("WARNING", message, *args)

    def error(self, message: str, *args: Any) -> None:
        self._emit("ERROR", message, *args)


def _log() -> Any:
    """
    The module logger, built on first use rather than at import.

    Falls back to `_BootstrapLogger` while `util.loggerfactory` is mid-import,
    and **caches only the real one** -- so the first call during settings
    bootstrap goes to stderr and every call afterwards, once the cycle has
    closed, goes through the factory like everything else.
    """
    global _LOGGER
    if _LOGGER is None:
        try:
            from util.loggerfactory import LoggerFactory  # noqa: PLC0415
            _LOGGER = LoggerFactory.create_logger(__name__)
        except (ImportError, AttributeError):
            # Mid-cycle. Do not cache: a later call can have the real thing.
            return _BootstrapLogger()
    return _LOGGER

#: Turns the loader on. Absent or anything else means "read .env as before",
#: so this file changes nothing until a node opts in.
SOURCE_VARIABLE = "CYCLONE_SECRETS_SOURCE"

#: Escape hatch for anything not on EC2 — a laptop, a CI runner, a container
#: somewhere else. Deliberately not the primary path: see the module docstring.
ENV_VARIABLE = "CYCLONE_ENV"

#: Where parameters live: /cyclone/<environment>/<VARIABLE_NAME>
PATH_TEMPLATE = "/cyclone/%s/"

#: The EC2 tag provisioning sets at launch.
ENVIRONMENT_TAG = "cyclone:environment"

#: Region variables, in the order they are consulted. BOTH are listed because
#: botocore has not always read the first: which one a given botocore honours
#: is a detail of a pinned dependency, and a node that boots or does not boot
#: should not turn on it. Resolving the region here and passing `region_name`
#: explicitly makes the question ours to answer.
REGION_VARIABLES = ("AWS_REGION", "AWS_DEFAULT_REGION")

#: IMDS. A short timeout because this runs at import: off EC2 the endpoint is
#: unroutable, and the loader must say so in a second rather than hang a boot.
_IMDS = "http://169.254.169.254"
_IMDS_TIMEOUT = 2.0


class SecretSourceError(RuntimeError):
    """Secrets were asked for and could not be had. Never swallowed."""


def enabled() -> bool:
    """Whether this node opted in to loading secrets from SSM."""
    return (os.environ.get(SOURCE_VARIABLE) or "").strip().lower() == "ssm"


def _imds_token(requests_module) -> Optional[str]:
    """An IMDSv2 session token, or None if only v1 is available."""
    try:
        response = requests_module.put(
            "%s/latest/api/token" % _IMDS,
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
            timeout=_IMDS_TIMEOUT,
        )
        if response.status_code == 200:
            return response.text
    except Exception:  # noqa: BLE001 — absence of IMDS is an answer, not a fault
        return None
    return None


def instance_environment() -> Optional[str]:
    """
    The environment name from this instance's own EC2 tag.

    Read from IMDS rather than from configuration, because the whole point is
    that no person types it. A box cannot be provisioned into the wrong
    environment by a typo when the answer comes from the tag its launch template
    set.

    :return: e.g. ``"production"``, or None when not on EC2 or untagged.
    """
    try:
        import requests  # noqa: PLC0415 -- only needed on this path
    except ImportError:
        # Say so. Returning None silently here is indistinguishable in the log
        # from an unreachable IMDS and from an untagged instance -- three
        # different next moves (rebuild the image / fix the hop limit / tag the
        # instance) behind one identical symptom. It is declared in
        # requirements.txt, so absence means the running image predates it.
        _log().warning(
            "secret_source: requests is not installed, so the %s tag cannot be "
            "read. The running image is older than app/requirements.txt.",
            ENVIRONMENT_TAG)
        return None

    headers = {}
    token = _imds_token(requests)
    if token:
        headers["X-aws-ec2-metadata-token"] = token
    try:
        response = requests.get(
            "%s/latest/meta-data/tags/instance/%s" % (_IMDS, ENVIRONMENT_TAG),
            headers=headers, timeout=_IMDS_TIMEOUT,
        )
        if response.status_code == 200 and response.text.strip():
            return response.text.strip()
        _log().warning(
            "secret_source: instance has no %s tag (HTTP %s). Tag it in the launch "
            "template, or set %s explicitly.",
            ENVIRONMENT_TAG, response.status_code, ENV_VARIABLE,
        )
    except Exception as e:  # noqa: BLE001
        # WARNING, not DEBUG. This function is only ever reached because
        # somebody turned the loader on, so an unreachable IMDS is always a
        # degradation: the node is about to fall back to a hand-typed
        # CYCLONE_ENV, which is precisely the drift surface the tag removes.
        # The exception CLASS is the diagnosis -- a timeout is a metadata hop
        # limit below 2, a connection error is no route at all -- and the
        # message alone does not carry it.
        _log().warning(
            "secret_source: could not reach IMDS for the %s tag (%s: %s). "
            "Check the instance metadata hop limit is 2 and that instance "
            "metadata tags are enabled.",
            ENVIRONMENT_TAG, type(e).__name__, str(e))
    return None


def environment_name() -> str:
    """
    Which environment this node belongs to.

    The instance tag first, then CYCLONE_ENV. **CYCLONE_ENV is a FALLBACK, not
    an override** -- it is consulted only when the tag yields nothing, and it
    never beats a tag that can be read. An override would win, and a typo in one
    node's .env would then point that box silently at another environment's
    secrets: the drift surface the tag exists to remove, restored in full.

    Resolution therefore reads: the machine says where it is; if the machine
    cannot say, the file may.

    :raises SecretSourceError: When neither source can say.
    """
    from_tag = instance_environment()
    if from_tag:
        return from_tag
    explicit = (os.environ.get(ENV_VARIABLE) or "").strip()
    if explicit:
        _log().info(
            "secret_source: using %s=%s (no EC2 instance tag found)",
            ENV_VARIABLE, explicit)
        return explicit
    raise SecretSourceError(
        "Cannot tell which environment this node is in. Either tag the instance "
        "%s, or set %s." % (ENVIRONMENT_TAG, ENV_VARIABLE))


def instance_region() -> Optional[str]:
    """
    The region from IMDS, which is the answer nobody has to type.

    Exactly the argument `instance_environment` makes about the environment
    name: the instance knows where it is, and a value read from the machine
    cannot drift from the machine.
    """
    try:
        import requests  # noqa: PLC0415
    except ImportError:
        return None

    headers = {}
    token = _imds_token(requests)
    if token:
        headers["X-aws-ec2-metadata-token"] = token
    try:
        response = requests.get(
            "%s/latest/meta-data/placement/region" % _IMDS,
            headers=headers, timeout=_IMDS_TIMEOUT,
        )
        if response.status_code == 200 and response.text.strip():
            return response.text.strip()
    except Exception:  # noqa: BLE001 -- already reported by instance_environment
        return None
    return None


def region() -> str:
    """
    Which region to read parameters from.

    **Resolved here and passed to boto3 explicitly, rather than left to its own
    environment scan.** `AWS_REGION` was set in `.env` and reached the process,
    and boto3 still raised `NoRegionError` -- botocore's own list of region
    variables is a property of whichever version is pinned, and a container that
    starts or does not start must not turn on that.

    The variable comes first because a node that states a region means it; IMDS
    is the fallback that needs no configuration at all.

    :raises SecretSourceError: When nothing can say. Guessing a region reads
        parameters from a path that does not exist there and reports it as an
        empty environment, which looks exactly like a correct empty load.
    """
    for variable in REGION_VARIABLES:
        value = (os.environ.get(variable) or "").strip()
        if value:
            return value

    from_imds = instance_region()
    if from_imds:
        _log().info(
            "secret_source: region %s from instance metadata (no %s set)",
            from_imds, " or ".join(REGION_VARIABLES))
        return from_imds

    raise SecretSourceError(
        "Cannot tell which AWS region to read parameters from. Set %s in the "
        "node's .env, or make instance metadata reachable from the container."
        % REGION_VARIABLES[0])


def _refuse_if_compose_reads_it(name: str, prefix: str) -> None:
    """
    Stop a value that docker-compose must read from arriving here instead.

    **This loader runs inside the container; compose runs on the host.** A
    `${SUPABASE_ANON_KEY}` in `docker-compose.yml` is interpolated before any
    container exists, so a parameter in SSM cannot possibly reach it. Delete
    that line from `.env` and put it in SSM and compose substitutes an EMPTY
    STRING -- one "variable is not set" line into a build log, exit code 0, an
    image built with no anon key, and a failure that first appears **in a
    browser at login**. Nothing in the API log, nothing on the fleet hash, and
    a container that says it is healthy.

    So reaching this point is proof the mistake has already been made in
    Parameter Store, and it is the last moment anything can say so. Raising
    stops the node rather than letting it serve a frontend that cannot
    authenticate -- the same trade as an unreadable SSM: a process that will
    not start is diagnosable, one that silently does nothing is not.

    The value is NOT applied before raising. The parameter still exists in SSM
    and still needs deleting; the message says so.
    """
    from util.config_spec import COMPOSE_ONLY  # noqa: PLC0415 -- pure data, no cycle

    if name.lower() not in COMPOSE_ONLY:
        return
    raise SecretSourceError(
        "%s%s exists in Parameter Store, but docker-compose reads %s ON THE "
        "HOST, before this container is created -- so SSM can never supply it. "
        "Put it back in the node's .env and delete the parameter. Left alone, "
        "compose would substitute an empty string and the frontend would build "
        "without it." % (prefix, name, name))


def load(force: bool = False) -> list[str]:
    """
    Copy SSM parameters into ``os.environ``, without overwriting.

    Must run BEFORE ``Settings()`` is constructed — ``util.settings`` calls it
    at import, above its own singleton, which is the only point every entry
    point shares.

    **Nothing already set is replaced.** A value in the process environment came
    from compose, the node's ``.env``, or a human debugging at 2am, and each of
    those is a more specific intent than a fleet-wide default. It also makes the
    rollout safe in both directions: turn the loader on and nothing changes
    until a line is removed from ``.env``; turn it off and the file takes over
    again.

    :param force: Load even when the source variable says otherwise. For tests.
    :return: The names loaded, sorted. Values are never logged or returned.
    :raises SecretSourceError: When SSM is configured but unreadable. Raising
        beats booting half-configured: a worker with no Anthropic key does not
        fail, it silently does nothing.
    """
    if not (force or enabled()):
        return []

    try:
        import boto3  # noqa: PLC0415 — only imported when this path is used
    except ImportError as e:
        raise SecretSourceError(
            "%s=ssm but boto3 is not installed. Add it to app/requirements.txt."
            % SOURCE_VARIABLE) from e

    prefix = PATH_TEMPLATE % environment_name()
    client = boto3.client("ssm", region_name=region())

    loaded: list[str] = []
    skipped = 0
    try:
        paginator = client.get_paginator("get_parameters_by_path")
        for page in paginator.paginate(
            Path=prefix, Recursive=True, WithDecryption=True,
        ):
            for parameter in page.get("Parameters", []):
                name = parameter["Name"].rsplit("/", 1)[-1]
                if name in os.environ:
                    skipped += 1
                    continue
                _refuse_if_compose_reads_it(name, prefix)
                os.environ[name] = parameter["Value"]
                loaded.append(name)
    except SecretSourceError:
        raise
    except Exception as e:  # noqa: BLE001 — every AWS failure means the same thing here
        raise SecretSourceError(
            "Could not read SSM parameters under %s: %s. The node has no "
            "credentials rather than the wrong ones, so it is not starting."
            % (prefix, e)) from e

    # Names only. A parameter name is not a secret; its value is, and §6 forbids
    # the value reaching a log even at DEBUG.
    _log().info(
        "secret_source: loaded %d parameter(s) from %s (%d already set, left alone)",
        len(loaded), prefix, skipped)
    return sorted(loaded)
