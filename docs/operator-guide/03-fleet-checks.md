# 3. Proving the nodes agree

[← Secrets from AWS Parameter Store](02-secrets.md) · [Index](README.md) · Next: [Deploying a release →](04-deploying.md)

---

Every node publishes a short hash over the configuration that is supposed to be
identical across the fleet. Two nodes disagreeing is then a **diff**, not a
discovery.

```sh
scripts/check-fleet.sh https://cyclone.jdbot.us
```

**No credential and no SSH.** `GET /api/config` is unauthenticated precisely so
that nothing stands between somebody and running the check — a hash reveals
nothing. The breakdown that names *which* setting differs is behind auth.

---

## Two modes, because there are two topologies

### Sampling — one URL

For a fleet behind a load balancer with no per-node address. This is the correct
architecture and the awkward one to verify:

```sh
scripts/check-fleet.sh https://cyclone.jdbot.us
CHECK_FLEET_SAMPLES=40 scripts/check-fleet.sh https://cyclone.jdbot.us
```

It asks the same URL repeatedly and groups the answers by the `id` each node
reports, letting the load balancer's own spreading reach the other node. Hit and
miss by nature — but testable, which is the point, and each sample is a separate
connection, so only session affinity can defeat it.

### Direct — two or more URLs

```sh
scripts/check-fleet.sh https://a.example.com https://b.example.com
scripts/check-fleet.sh http://10.0.1.5:8094 http://10.0.1.6:8094
```

> **On this deployment, direct mode does not work across the network.** The
> compose file publishes `8094:80` on every interface, but the nodes were not
> reachable on that port from another host when it was tried — security groups
> and/or the host firewall. Expect this:
>
> - **Sampling the load-balancer URL is the practical check here.**
> - Direct mode works from a node **against itself**: `http://127.0.0.1:8094`.
> - The obvious first attempt — the public IPs from the AWS console — fails, and
>   looks like the script is broken rather than like a closed port.
>
> Direct mode needs each node addressable on 8094 from wherever the check runs,
> which needs a security-group rule that does not exist today.

`CHECK_FLEET_HOST` sets a `Host` header for addressing a node by IP behind a
proxy that routes by hostname. Over HTTPS prefer `curl --resolve`, which keeps
SNI and certificate validation intact.

---

## Exit codes

| | |
| - | - |
| **0** | Every node seen reported the same hash **and** the same build |
| **1** | **Config drift** — they do not agree about configuration |
| **2** | **Inconclusive** — nothing could be read, or only one node was ever seen |
| **3** | **Build skew** — configuration agrees, but the nodes run different images |

**2 is deliberately not 0.** A node that is down, and a sample that only ever
reached one server, are both *"we did not learn the answer"* — and letting
either read as "all clear" is the exact failure this script exists to prevent.

**3 is deliberately not 1.** Different code and different configuration are
different problems with different fixes, and one hash covering both could not
say which you had. It is also transiently normal mid-deploy, and a check that
called that "drift" would cry wolf on every release.

---

## What the hash covers

**Covered:** the fleet settings, the credentials, and the per-firm settings.

- A credential contributes `sha256(value)[:12]` — **never the value**. Enough to
  prove two nodes hold the same key, useless for recovering it.
- An unset value contributes the literal `<unset>`, which is deliberately
  distinct from any digest. *"Both nodes have no Stripe key"* and *"both nodes
  hold the same Stripe key"* are different states, and the first is the
  dangerous one.

**Excluded:** `id`, `host_url`, the Supabase and Redis URLs, and the loader
switches. They differ per node by design, and a hash over `id` would differ
between any two machines forever — a drift report that always fires is one
nobody reads.

**The build is excluded and reported separately.** See
[Chapter 4](04-deploying.md#build-identity).

---

## When it says they disagree

The public hash says *that* two nodes differ. The authenticated breakdown says
*which setting*:

```sh
curl -H "Authorization: Bearer $TOKEN" https://node/api/v1/config/fingerprint
```

- Every fingerprinted setting with its digest — **values are never returned**,
  so the output is safe to paste into a ticket.
- Which bucket each setting is in.
- The infrastructure values in the clear and labelled, so a reader can see that
  `id` differing is *expected* rather than wonder whether it was overlooked.
- The counts behind the startup warnings: deprecated settings in use, legacy
  Supabase keys, misplaced secrets ([Chapter 5](05-startup-log.md)).

It is behind auth because the set of digests is an **inventory**: which
credentials this node holds and which it does not. Not a secret, but a map, and
a map is worth a login.

Two of these side by side answer the question the hash only raises.

---

## Running it as part of the routine

The check is worth running at three moments, and they are the three where a
difference is cheapest to fix:

| When | What you are asking |
| ---- | ------------------- |
| **After a deploy** | Did every node get the release? A node still reporting the old build did not |
| **After changing any setting** | Did the fleet move together, or just the box you were on? |
| **After moving a secret to SSM** | Does the stored value match the known-good one, byte for byte? |

---

[← Secrets from AWS Parameter Store](02-secrets.md) · [Index](README.md) · Next: [Deploying a release →](04-deploying.md)
