"""
tests/test_secret_source_bootstrap.py - Logging must not crash the boot it logs.

`util.settings` calls `secret_source.load()` at import, above its own
singleton. `util.loggerfactory` line 6 imports `settings` for the log level. So
the ordinary way a process reaches configuration is:

    crm_worker -> dependencies -> repositories.user_role
        -> util.loggerfactory        (mid-import, LoggerFactory not bound yet)
        -> util.settings             (line 23: secret_source.load())
        -> environment_name -> instance_environment
        -> _log()

A lazy `from util.loggerfactory import LoggerFactory` inside `_log()` breaks
that cycle **at module scope** and does nothing for a call made from *inside*
it: the module is in `sys.modules` but the name is not bound, so the import
raises `ImportError` rather than recursing. That was an unhandled exception
during settings construction, and the worker did not start:

    ImportError: cannot import name 'LoggerFactory' from partially initialized
    module 'util.loggerfactory' (most likely due to a circular import)

**It reached production, and the suite could not have caught it**, because
`load()` returns immediately when the loader is off — so `_log()` was never
called during a settings import until somebody set CYCLONE_SECRETS_SOURCE=ssm
on a live node. The tests below turn it on, which is the only condition that
executes the path.

Run:  venv/Scripts/python.exe tests/test_secret_source_bootstrap.py
"""
import io
import os
import sys
import types

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got:  %r\n         want: %r" % (label, got, want))
        FAILURES.append(label)


def check_true(label: str, got) -> None:
    check(label, bool(got), True)


class _CapturedStderr:
    """Collects what the bootstrap logger writes, without losing the text."""

    def __init__(self) -> None:
        self.buffer = io.StringIO()
        self._real = None

    def __enter__(self) -> "_CapturedStderr":
        self._real = sys.stderr
        sys.stderr = self.buffer
        return self

    def __exit__(self, *exc) -> None:
        sys.stderr = self._real

    @property
    def text(self) -> str:
        return self.buffer.getvalue()


# ── The unit case: a partially initialized loggerfactory ─────────────────────

print("_log() when util.loggerfactory is mid-import")

from util import secret_source  # noqa: E402

# This is exactly what a partially initialized module looks like to an
# importer: present in sys.modules, with the name not yet bound. Building it by
# hand rather than orchestrating a real cycle keeps the assertion about the
# behaviour under that condition, not about how the condition arose.
_real_factory = sys.modules.get("util.loggerfactory")
sys.modules["util.loggerfactory"] = types.ModuleType("util.loggerfactory")
secret_source._LOGGER = None
try:
    with _CapturedStderr() as captured:
        logger = secret_source._log()
        logger.warning("secret_source: %s is unreadable", "IMDS")
        logger.debug("secret_source: this one is dropped")
    check_true("_log() returns something rather than raising ImportError", logger)
    check_true("the message reaches stderr", "IMDS is unreadable" in captured.text)
    check_true("it carries a level", "WARNING" in captured.text)
    check("DEBUG is dropped, the level being unknowable this early",
          "dropped" in captured.text, False)
    check("a bootstrap logger is NOT cached, so a later call can be the real one",
          secret_source._LOGGER, None)
finally:
    if _real_factory is None:
        sys.modules.pop("util.loggerfactory", None)
    else:
        sys.modules["util.loggerfactory"] = _real_factory
    secret_source._LOGGER = None


# ── The regression: the whole cycle, entered where production enters it ──────

print("")
print("The import cycle that crashed the worker at cutover")


class _UnreachableIMDS:
    """`requests`, with the metadata endpoint refusing the connection.

    The traceback from production took the `except` branch of
    `instance_environment`, not its no-tag branch — IMDS raised inside the
    container. Stubbing the module is also what keeps this test fast: the real
    path is two 2-second timeouts.
    """

    class exceptions:  # noqa: N801 — mirroring the real module's shape
        class ConnectionError(Exception):
            pass

    @staticmethod
    def put(*_args, **_kwargs):
        raise _UnreachableIMDS.exceptions.ConnectionError("no route to 169.254.169.254")

    @staticmethod
    def get(*_args, **_kwargs):
        raise _UnreachableIMDS.exceptions.ConnectionError("no route to 169.254.169.254")


class _EmptySSM:
    """boto3, answering with a parameter path that exists and is empty.

    Empty is the honest fixture: the assertion here is that the process starts,
    and loading values would test SSM rather than the import order.
    """

    class _Paginator:
        @staticmethod
        def paginate(**_kwargs):
            return [{"Parameters": []}]

    class _Client:
        @staticmethod
        def get_paginator(_name):
            return _EmptySSM._Paginator()

    #: What region the client was built for. Asserted below: a region resolved
    #: but not passed through is the bug this fixture exists to catch.
    region_asked_for = None

    @staticmethod
    def client(_service, region_name=None):
        _EmptySSM.region_asked_for = region_name
        return _EmptySSM._Client()


_PURGE = ("util.settings", "util.loggerfactory", "util.secret_source")
_saved_modules = {name: sys.modules.get(name) for name in _PURGE}
_saved_modules["requests"] = sys.modules.get("requests")
_saved_modules["boto3"] = sys.modules.get("boto3")
_saved_env = {
    key: os.environ.get(key)
    for key in ((secret_source.SOURCE_VARIABLE, secret_source.ENV_VARIABLE)
                + secret_source.REGION_VARIABLES)
}

try:
    for name in _PURGE:
        sys.modules.pop(name, None)
    sys.modules["requests"] = _UnreachableIMDS
    sys.modules["boto3"] = _EmptySSM
    os.environ[secret_source.SOURCE_VARIABLE] = "ssm"
    os.environ[secret_source.ENV_VARIABLE] = "production"
    os.environ["AWS_REGION"] = "us-east-1"
    os.environ.pop("AWS_DEFAULT_REGION", None)

    failure = None
    with _CapturedStderr() as captured:
        try:
            # THE ENTRY POINT MATTERS. Importing `util.settings` directly does
            # not reproduce it — loggerfactory is then imported cleanly from
            # inside a cycle that has already passed its own line 6. Production
            # arrives through the logger, which is what leaves it half built.
            import util.loggerfactory  # noqa: F401, PLC0415
        except Exception as e:  # noqa: BLE001
            failure = e

    check("importing util.loggerfactory with the loader on does not raise",
          None if failure is None else "%s: %s" % (type(failure).__name__, failure),
          None)
    check_true("the unreachable IMDS was reported, not swallowed",
               "could not reach IMDS" in captured.text)
    check_true("the exception class is named, so a timeout is distinguishable "
               "from no route", "ConnectionError" in captured.text)
    check_true("the fallback to CYCLONE_ENV is stated",
               "CYCLONE_ENV=production" in captured.text)
    check("the region is passed to the client, not left to botocore's own scan",
          _EmptySSM.region_asked_for, "us-east-1")
finally:
    for name, module in _saved_modules.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module
    for key, value in _saved_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


# ── ─────────────────────────────────────────────────────────────────────────

# ── The region is resolved here, not left to botocore ─────────────────

print("")
print("Which region the parameters are read from")

# `AWS_REGION=us-east-1` was set in .env and DID reach the process, and boto3
# still raised NoRegionError -- whether botocore reads AWS_REGION or only
# AWS_DEFAULT_REGION is a property of the pinned version. A container starting
# or not starting must not turn on that, so the region is resolved here and
# passed as `region_name`.
_saved_region = {k: os.environ.get(k) for k in secret_source.REGION_VARIABLES}
try:
    os.environ["AWS_REGION"] = "us-east-1"
    os.environ.pop("AWS_DEFAULT_REGION", None)
    check("AWS_REGION is honoured", secret_source.region(), "us-east-1")

    os.environ.pop("AWS_REGION", None)
    os.environ["AWS_DEFAULT_REGION"] = "us-west-2"
    check("AWS_DEFAULT_REGION is honoured too", secret_source.region(), "us-west-2")

    os.environ["AWS_REGION"] = "us-east-1"
    check("AWS_REGION wins when both are set", secret_source.region(), "us-east-1")

    # With neither set and no IMDS, it must raise rather than guess: a wrong
    # region reads a path that does not exist there and reports an empty
    # environment, which is indistinguishable from a correct empty load.
    for key in secret_source.REGION_VARIABLES:
        os.environ.pop(key, None)
    _saved_requests = sys.modules.get("requests")
    sys.modules["requests"] = _UnreachableIMDS
    try:
        raised = None
        try:
            secret_source.region()
        except secret_source.SecretSourceError as e:
            raised = str(e)
        check_true("with no variable and no IMDS it raises rather than guessing",
                   raised is not None)
        check_true("and the message names the variable to set",
                   raised and "AWS_REGION" in raised)
    finally:
        if _saved_requests is None:
            sys.modules.pop("requests", None)
        else:
            sys.modules["requests"] = _saved_requests
finally:
    for key, value in _saved_region.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


# ── ───────────────────────────────────────────────────────────────────────

print("")
if FAILURES:
    print("%d FAILED" % len(FAILURES))
    for label in FAILURES:
        print("  - %s" % label)
    sys.exit(1)
print("All secret_source bootstrap checks passed")
