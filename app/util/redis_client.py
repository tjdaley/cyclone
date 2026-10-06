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
