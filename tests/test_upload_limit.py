"""
tests/test_upload_limit.py - The upload limit lives in two files and must agree.

`client_max_body_size` in frontend/nginx.conf is the only place the limit is
ENFORCED: nginx rejects an oversized body mid-flight and answers with its own
HTML error page, before the request reaches any Python. `settings.max_upload_mb`
is what the API tells the browser, so the browser can refuse a file instantly
instead of spending minutes uploading one that will be thrown away.

**A mismatch is invisible from either side.** Too high in settings and the
browser accepts a file nginx then refuses with a raw error page -- which is
exactly what a user hit with a 73 MB file. Too low and uploads are refused that
the server would have taken, with nothing anywhere explaining why.

Nothing can make these one value: nginx.conf is static and read by a different
container than the one that holds settings. So they are two, and this is what
stops them drifting -- the same argument as the test that pins
COMPOSE_INTERPOLATED to what docker-compose.yml actually interpolates.

**A THIRD LIMIT EXISTS AND THIS TEST CANNOT SEE IT.** Supabase Storage enforces
its own upload cap, set in the storage project settings and defaulting to 50 MB.
Received documents PUT straight to storage over a signed URL, so that cap
governs a request this codebase never makes -- a file inside both limits above
is still refused there, with an HTTP 400 whose body says 413. It is a third
party's setting behind a network call, so it is named rather than asserted:
raise it to at least `max_upload_mb`, or lower `max_upload_mb` to match it.

Run:  venv/Scripts/python.exe tests/test_upload_limit.py
"""
import io
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "app"))

FAILURES: list[str] = []


def check(label, got, want):
    if got == want:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s\n         got  %r\n         want %r" % (label, got, want))
        FAILURES.append(label)


print("\n1. nginx and settings agree about the limit")

nginx = io.open(os.path.join(ROOT, "frontend", "nginx.conf"), encoding="utf-8").read()
sizes = re.findall(r"client_max_body_size\s+(\d+)([kmg])\s*;", nginx, re.I)
check("nginx declares exactly one body-size limit", len(sizes), 1)

scale = {"k": 1 / 1024, "m": 1, "g": 1024}
nginx_mb = int(sizes[0][0]) * scale[sizes[0][1].lower()]

from util.settings import Settings  # noqa: E402

declared = Settings.model_fields["max_upload_mb"].default
check("and it matches settings.max_upload_mb", nginx_mb, float(declared))

print("\n2. The limit is big enough to be worth having")

# Real productions from one opposing firm measured 36 MB to 665 MB. A limit
# below the SMALLEST of them is not protecting anything, it is refusing
# ordinary input -- which is what 50 MB was doing.
check("it clears the smallest production actually seen (36 MB)",
      declared > 36, True)
# And bounded: every upload route reads the whole body into RAM with
# file.file.read(), and no container here sets a memory limit, so an overrun
# takes the host rather than one container. Raising this past a few hundred
# megabytes needs streaming uploads first.
check("and stays inside what a non-streaming read can hold",
      declared <= 256, True)

print("\n3. The API tells the browser, so it can refuse before uploading")

health = io.open(os.path.join(ROOT, "app", "routers", "health.py"),
                 encoding="utf-8").read()
check("GET /api/config publishes the limit",
      "settings.max_upload_mb" in health, True)

# Classified, or the fleet fingerprint silently stops covering it. A node
# accepting larger uploads than its neighbour makes refusal depend on which
# server the load balancer picked.
from util.config_spec import FLEET  # noqa: E402

check("and the setting is classified FLEET, so two nodes cannot disagree",
      "max_upload_mb" in FLEET, True)

print("\n4. The example file documents it")

example = io.open(os.path.join(ROOT, ".env.example"), encoding="utf-8").read()
# A non-string setting is always the commented shape: an empty string is not an
# int, and pydantic refuses the whole file over one.
check("as a commented default, since a blank value is not an int",
      bool(re.search(r"^#\s*MAX_UPLOAD_MB=\d+", example, re.M)), True)

print("")
if FAILURES:
    print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
    sys.exit(1)
print("all upload-limit checks passed")
