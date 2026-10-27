"""
app/util/redis_client.py - Redis / Valkey helpers for distributed locks and
inbound-email idempotency.

The CRM poller uses these to guarantee a single active runner and to claim each
inbound message exactly once. Redis is the fast path only — the durable source
of truth for processed messages is the processed_inbound_emails table, so a
Redis flush can never cause a message to be reprocessed (the table still blocks it)
nor permanently lost (the message stays UNSEEN in the mailbox until committed).
"""
from contextlib import contextmanager
from typing import Iterator, Optional

import redis

from util.loggerfactory import LoggerFactory
from util.settings import settings

LOGGER = LoggerFactory.create_logger(__name__)

_client: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    """
    Return a process-wide Redis client built from settings.redis_url.

    **BOTH TIMEOUTS ARE LOAD-BEARING, and neither was here.** Without them the
    socket default is *never* -- the same defect the mail connections had, with
    the same fleet-wide lock at stake: the CRM tick holds `crm:poller` while it
    works, so one node waiting forever on Redis stops **every** node from
    polling until the lock TTL expires.

    ``socket_connect_timeout`` bounds the loud failure (nothing listening, no
    route). ``socket_timeout`` bounds the quiet one -- a host that accepts the
    connection and then stops talking -- and the quiet one is worse, because
    nothing anywhere reports it.

    **What these do NOT fix, measured rather than assumed:** a *refused*
    connection already returns quickly, so the cap never engages. On one Windows
    box a refused connect to 127.0.0.1 took 2.04s and to ::1 another 2.04s --
    and ``localhost`` resolves to BOTH, tried in order, so a URL spelled
    ``redis://localhost`` cost 4.07s where ``redis://127.0.0.1`` cost 2.04s.
    Setting the cap to 0.2s did bound it (0.43s total), which is how we know the
    timeouts are wired correctly; 3.0s simply sits above the refusal.

    What they DO fix is the case with no bound at all: a host that accepts the
    connection and never answers, or one whose packets are dropped by a changed
    security group. That is the failure worth having a number for, and it is the
    quiet one.
    """
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.redis_timeout_seconds,
            socket_timeout=settings.redis_timeout_seconds,
        )
    return _client


def claim_once(key: str, ttl_seconds: int = 86400) -> bool:
    """
    Atomically claim a one-time key (e.g. ``inbound:<message-id>``).

    :return: True if this caller won the claim, False if it was already claimed.
    :rtype: bool
    """
    return bool(get_redis().set(key, "1", nx=True, ex=ttl_seconds))


def claim_for(key: str, owner: str, ttl_seconds: int = 3600) -> tuple[bool, Optional[str]]:
    """
    Claim a key for a NAMED owner, or report who already holds it.

    `claim_once` cannot express this and using it here was a bug that reached
    production. It stores the literal value "1", so the holder is anonymous and
    a second call by the SAME person is indistinguishable from a conflict: the
    splitting bench re-opens its session after every bundle, so from the second
    bundle onward the operator was told somebody else had the document open.
    They were somebody else to themselves.

    Re-entry by the owner also EXTENDS the claim, which `claim_once` could not
    do either — `SET NX` on an existing key is a no-op, so the "refresh" that
    was supposed to keep a long session alive did nothing at all.

    Best-effort, like every other use of Redis here: if it is unreachable the
    claim is granted. A bench nobody can open because a cache is down is worse
    than two people on one pile, and the exclusion constraint still stops either
    of them corrupting a single document.

    :return: ``(is_ours, current_owner)``.
    """
    try:
        r = get_redis()
        if r.set(key, owner, nx=True, ex=ttl_seconds):
            return True, owner
        current = r.get(key)
        if current == owner:
            # Ours already: extend it rather than reporting a conflict.
            r.expire(key, ttl_seconds)
            return True, owner
        return False, current
    except Exception as e:  # noqa: BLE001
        LOGGER.warning("claim_for: Redis unavailable for %s, granting: %s", key, str(e))
        return True, owner


def release_claim(key: str) -> None:
    """
    Give up a key taken with ``claim_once``.

    ``lock`` is the right primitive for work that finishes inside one function;
    it cannot express a claim held across HTTP requests, which is what one
    operator holding one pile on the splitting bench is. That needs
    take-and-hold with a TTL, and therefore an explicit release.

    Never raises: failing to release costs a wait until the TTL expires, while
    raising would fail the operation that had already succeeded.
    """
    try:
        get_redis().delete(key)
    except Exception as e:  # noqa: BLE001
        LOGGER.warning("release_claim: could not release %s: %s", key, str(e))


@contextmanager
def lock(key: str, ttl_seconds: int = 300) -> Iterator[bool]:
    """
    Best-effort distributed lock. Yields True if acquired, False otherwise.
    Releases on exit only if this caller still owns it (ownership token guards
    against deleting a lock that expired and was re-taken by someone else).

    Usage::

        with lock("crm:poller") as got:
            if not got:
                return  # another runner holds it
            ...
    """
    r = get_redis()
    token = make_msgid_token()
    acquired = bool(r.set(key, token, nx=True, ex=ttl_seconds))
    try:
        yield acquired
    finally:
        if acquired:
            try:
                if r.get(key) == token:
                    r.delete(key)
            except redis.RedisError as e:
                LOGGER.warning("redis lock release failed key=%s err=%s", key, str(e))


def make_msgid_token() -> str:
    """A unique-enough ownership token for a lock holder."""
    import uuid
    return uuid.uuid4().hex
