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
    """Return a process-wide Redis client built from settings.redis_url."""
    global _client
    if _client is None:
        _client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
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
