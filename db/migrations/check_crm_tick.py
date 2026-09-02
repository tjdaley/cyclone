"""
check_crm_tick.py - Which connection in the CRM tick is refusing?

Read-only and side-effect free on purpose. It does NOT call _poll_welcomes or
_poll_inbound: the first sends welcome emails to real leads and the second can
mark mail seen. It only opens the sockets those steps would open, so running it
twice costs nothing and tells nobody anything.

Run inside the worker container, which is the network view that is failing:

    docker compose exec -T worker python - < db/migrations/check_crm_tick.py
"""
import socket
import sys
import traceback
from urllib.parse import urlparse

from util.settings import settings


def probe(label: str, host: str, port: int) -> None:
    """Open a TCP connection and report what happened, in plain words."""
    if not host:
        print("  SKIP  %-22s not configured" % label)
        return
    try:
        with socket.create_connection((host, port), timeout=5):
            print("  OK    %-22s %s:%s" % (label, host, port))
    except socket.gaierror as e:
        print("  DNS   %-22s %s:%s cannot be resolved here (%s)" % (label, host, port, e))
    except ConnectionRefusedError:
        print("  REFUSED %-20s %s:%s — reached the host, nothing listening on that port"
              % (label, host, port))
    except socket.timeout:
        print("  TIMEOUT %-20s %s:%s — packets dropped, not refused (firewall)"
              % (label, host, port))
    except Exception as e:  # noqa: BLE001 — this is a diagnostic
        print("  ERROR %-22s %s:%s %s: %s" % (label, host, port, type(e).__name__, e))


print("\nConfiguration as the worker sees it")
print("  redis_url          = %s" % settings.redis_url)
print("  imap_host:port     = %s:%s" % (settings.imap_host or "(unset)", settings.imap_port))
print("  smtp_host:port     = %s:%s" % (settings.smtp_host or "(unset)", settings.smtp_port))
print("  landing_pages_url  = %s" % (settings.supabase_landing_pages_url or "(unset)"))
print("  supabase_url       = %s" % (settings.supabase_url or "(unset)"))

print("\nSockets the tick opens")
redis_parts = urlparse(settings.redis_url or "")
probe("redis (poller lock)", redis_parts.hostname or "", redis_parts.port or 6379)
probe("imap (_poll_inbound)", settings.imap_host, settings.imap_port)
probe("smtp (_poll_welcomes)", settings.smtp_host, settings.smtp_port)

for label, url in (("landing-pages DB", settings.supabase_landing_pages_url),
                   ("cyclone DB", settings.supabase_url)):
    parts = urlparse(url or "")
    probe(label, parts.hostname or "", parts.port or (443 if parts.scheme == "https" else 80))

# The landing-pages read is the first thing _poll_welcomes does, and it is a
# plain select — safe to run, and it exercises auth as well as the socket.
print("\nThe landing-pages query _poll_welcomes starts with")
try:
    from db.repositories.foreign_lead import ForeignLeadRepository
    from dependencies import get_landing_pages_db

    leads = ForeignLeadRepository(get_landing_pages_db()).list_recent(limit=1)
    print("  OK    read %d lead(s)" % len(leads))
except Exception:  # noqa: BLE001 — this is a diagnostic
    print("  FAILED:")
    traceback.print_exc(file=sys.stdout)

print("\nWhat _run_diff_explainer reads")
try:
    from db.repositories.lead_agent_run import LeadAgentRunRepository
    from dependencies import get_db_manager

    pending = LeadAgentRunRepository(get_db_manager()).list_pending_explanations(limit=1)
    print("  OK    %d run(s) awaiting an explanation" % len(pending))
except Exception:  # noqa: BLE001 — this is a diagnostic
    print("  FAILED:")
    traceback.print_exc(file=sys.stdout)

print("")
