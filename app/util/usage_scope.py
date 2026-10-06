"""
app/util/usage_scope.py - Who a piece of LLM spend is for.

Ten services call the model, through helpers several layers deep, and none of
them knows which job, matter or staff member the work is for. Threading those
ids through every signature would touch dozens of functions and break the first
time somebody added a call. So the attribution is set ONCE, where the work
starts, and ``llm_service`` reads it when it records a call:

    with usage_scope(source_type="job", source_id=job.id,
                     staff_id=job.requested_by_staff_id, matter_id=job.matter_id):
        run_the_job()

Scopes NEST. An inner scope inherits every field it does not set, so the CRM
tick can say "crm" and the welcome loop inside it can narrow that to one lead.

TWO TRAPS, BOTH MEASURED OR INHERENT TO THE MECHANISM
────────────────────────────────────────────────────────────────────────────
**A context value does not cross into a ThreadPoolExecutor thread.** The pool
thread starts with an empty context, so a scope set around ``pool.submit`` is
gone by the time the job runs. Set it INSIDE the function the pool runs --
``job_service.run_claimed`` does exactly that.

**A value set inside a SYNC FastAPI dependency never reaches the endpoint.**
FastAPI runs a sync dependency in its own threadpool call, with its own copy of
the context, and discards that copy when the dependency returns. Checked by
running it, not assumed: a middleware value reached a sync endpoint, and a value
set in a sync dependency on the same request did not. That is why a request's
scope is set in ``AuthMiddleware`` and not in ``require_role``, which would have
looked right and recorded nothing.

Nothing set means ``source_type="unattributed"``: the spend is still recorded,
and the gap shows up in the totals instead of vanishing from them.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional

#: The fields a scope can carry. Anything else is refused, so a typo in a
#: keyword cannot silently record nothing.
FIELDS = ("source_type", "source_id", "staff_id", "matter_id", "auth_uid", "firm_id")

UNATTRIBUTED = "unattributed"

_SCOPE: ContextVar[dict[str, Any]] = ContextVar("llm_usage_scope", default={})


def current() -> dict[str, Any]:
    """
    The attribution in force right now.

    :return: Every field in ``FIELDS``, with ``source_type`` defaulting to
        ``"unattributed"`` and the rest to None.
    :rtype: dict[str, Any]
    """
    scope = _SCOPE.get()
    resolved = {field: scope.get(field) for field in FIELDS}
    resolved["source_type"] = resolved["source_type"] or UNATTRIBUTED
    return resolved


def _merged(fields: dict[str, Any]) -> dict[str, Any]:
    """The scope in force, overlaid with ``fields``. One rule for both entry points."""
    unknown = sorted(set(fields) - set(FIELDS))
    if unknown:
        raise TypeError("usage scope: unknown field(s) %s; expected %s"
                        % (", ".join(unknown), ", ".join(FIELDS)))
    merged = dict(_SCOPE.get())
    for key, value in fields.items():
        if value is not None:
            merged[key] = str(value) if key in ("source_id", "auth_uid") else value
    return merged


@contextmanager
def usage_scope(**fields: Any) -> Iterator[None]:
    """
    Attribute every LLM call made inside the block.

    Fields given as None are ignored rather than clearing an outer value, so a
    route that has no matter can still say ``usage_scope(matter_id=body.matter_id)``
    without wiping a matter set further out.

    :raises TypeError: On a field name not in ``FIELDS``.
    """
    token = _SCOPE.set(_merged(fields))
    try:
        yield
    finally:
        _SCOPE.reset(token)


def set_scope(**fields: Any) -> Any:
    """
    Enter a scope without a ``with`` block, returning a token for ``reset_scope``.

    For middleware, where the block is an ``await call_next(...)`` that a context
    manager can wrap just as well -- prefer ``usage_scope`` wherever it fits.
    """
    return _SCOPE.set(_merged(fields))


def reset_scope(token: Optional[Any]) -> None:
    """Undo a ``set_scope``."""
    if token is not None:
        _SCOPE.reset(token)
