#!/usr/bin/env sh
# scripts/check-fleet.sh — do these nodes agree?
#
# Two servers' .env files drifted and it was found by luck. This is the check
# that makes finding it design instead: every node publishes a hash over the
# configuration that is supposed to be identical, and disagreement is a diff.
#
# Needs no credential and no SSH — GET /api/config is unauthenticated, and it is
# unauthenticated precisely so that nothing stands between somebody and running
# this. A hash reveals nothing; the breakdown that names WHICH setting differs
# is behind auth at GET /api/v1/config/fingerprint.
#
# ── TWO MODES, BECAUSE THERE ARE TWO TOPOLOGIES ──────────────────────────────
#
# SAMPLING (one URL). For a fleet behind a load balancer with no per-node
# address — which is the correct architecture, and the awkward one to verify:
#
#     scripts/check-fleet.sh https://cyclone.jdbot.us
#     CHECK_FLEET_SAMPLES=40 scripts/check-fleet.sh https://cyclone.jdbot.us
#
# It asks the same URL repeatedly and groups the answers by the `id` each node
# reports, so the load balancer's own spreading is what reaches the other node.
# Hit and miss by nature — but testable, which is the point, and each sample is
# a separate connection, so affinity is the only thing that can defeat it.
#
# DIRECT (two or more URLs). When each node can be addressed on its own:
#
#     scripts/check-fleet.sh https://a.example.com https://b.example.com
#     scripts/check-fleet.sh http://10.0.1.5:8094 http://10.0.1.6:8094
#
# :8094 answers only where the app is published on that interface. A node
# publishing it on 127.0.0.1 is reachable this way only from itself.
#
# CHECK_FLEET_HOST sets a Host header, for addressing a node by IP behind a
# proxy that routes by hostname. Over HTTPS prefer curl --resolve, which keeps
# SNI and certificate validation intact:
#     curl --resolve app.example.com:443:10.0.1.5 https://app.example.com/api/config
#
# ── EXIT CODES ───────────────────────────────────────────────────────────────
#   0  every node seen reported the same hash AND the same build
#   1  config drift — they do not agree about configuration
#   2  inconclusive — nothing could be read, or only one node was ever seen
#   3  build skew - configuration agrees, but the nodes run different images
#
# 2 is deliberately not 0. A node that is down, and a sample that only ever
# reached one server, are both "we did not learn the answer" — and letting
# either read as "all clear" is the failure this script exists to prevent.
#
# 3 IS SEPARATE FROM 1, AND THAT SEPARATION IS THE POINT. Two nodes running
# different code is a different problem from two nodes configured differently,
# with a different fix, and one hash covering both could not say which you had.
# It is also transiently NORMAL: mid-deploy, the nodes genuinely differ, and a
# check that called that "drift" would cry wolf on every release.
#
# The failure this catches is real and was expensive: model chains used to be
# named in .env, the two nodes drifted, and a pleading was read by Anthropic on
# one server and Gemini on the other depending on where the balancer sent it.
# Gemini declines content describing crimes against minors -- ordinary family-law
# evidence -- so the same document processed, then did not, then did. Nobody
# diagnoses intermittency as a configuration difference.

set -eu

SAMPLES="${CHECK_FLEET_SAMPLES:-20}"
HOST_HEADER="${CHECK_FLEET_HOST:-}"

if [ "$#" -lt 1 ]; then
    cat >&2 <<'USAGE'
usage: check-fleet.sh <url>              sample one load-balanced endpoint
       check-fleet.sh <url> <url> ...    check each node directly

  check-fleet.sh https://cyclone.jdbot.us
      20 samples, grouped by the node id each answer carries

  CHECK_FLEET_SAMPLES=50 check-fleet.sh https://cyclone.jdbot.us
      more samples, when the balancer is not spreading much

  CHECK_FLEET_HOST=app.example.com check-fleet.sh http://10.0.1.5
      address one node by IP behind a proxy that routes by hostname
USAGE
    exit 64
fi

# curl's own diagnostics are the whole value when a node will not answer, so
# they are captured rather than discarded. An earlier version paired
# --show-error with 2>/dev/null, which cancelled it out and reported every
# distinct failure as the same word.
ERR="$(mktemp)"
SEEN="$(mktemp)"
trap 'rm -f "$ERR" "$SEEN"' EXIT INT TERM

field() {
    # Deliberately not jq: this gets run from whatever machine is to hand,
    # including a bare EC2 box, and a dependency is a reason not to run it.
    sed -n 's/.*"'"$2"'":[[:space:]]*"\{0,1\}\([^",}]*\)"\{0,1\}.*/\1/p' <<EOF | head -1
$1
EOF
}

# Fetch /api/config. Sets RAW, CODE, BODY. Returns non-zero only when the
# request itself failed; an HTTP error is data, not an error.
fetch() {
    if [ -n "$HOST_HEADER" ]; then
        RAW="$(curl --silent --show-error --location --max-time 15 \
                 --header "Host: $HOST_HEADER" \
                 --write-out '\n%{http_code}' "$1/api/config" 2>"$ERR")" || RAW=""
    else
        RAW="$(curl --silent --show-error --location --max-time 15 \
                 --write-out '\n%{http_code}' "$1/api/config" 2>"$ERR")" || RAW=""
    fi
    CODE="$(printf '%s' "$RAW" | tail -n 1)"
    BODY="$(printf '%s' "$RAW" | sed '$d')"
    [ -n "$RAW" ] && [ "$CODE" != "000" ]
}

explain() {
    case "$1" in
        404) echo "    Nothing serving /api/config here. Behind a proxy that routes by"
             echo "    hostname, a bare IP matches no backend — set CHECK_FLEET_HOST." ;;
        502|503|504) echo "    Something is in front, and the app behind it is not answering." ;;
        401|403) echo "    /api/config should be unauthenticated. Something in front is"
             echo "    demanding credentials." ;;
    esac
}

[ -n "$HOST_HEADER" ] && echo "Host: $HOST_HEADER (sent with every request)"

# ── Sampling mode ────────────────────────────────────────────────────────────
if [ "$#" -eq 1 ]; then
    base="$1"
    echo "Sampling $base — $SAMPLES requests, grouped by the node id each answers with."
    echo ""

    ok=0
    failed=0
    nofinger=0
    i=0
    while [ "$i" -lt "$SAMPLES" ]; do
        i=$((i + 1))
        if ! fetch "$base"; then
            failed=$((failed + 1))
            [ "$failed" -eq 1 ] && sed 's/^/    /' "$ERR" 2>/dev/null | head -2
            continue
        fi
        if [ "$CODE" != "200" ]; then
            failed=$((failed + 1))
            [ "$failed" -eq 1 ] && { echo "    HTTP $CODE"; explain "$CODE"; }
            continue
        fi
        hash="$(field "$BODY" fleet_hash)"
        if [ -z "$hash" ]; then
            nofinger=$((nofinger + 1))
            continue
        fi
        ok=$((ok + 1))
        build="$(field "$BODY" build)"
        printf '%s\t%s\t%s\t%s\t%s\n' \
            "$(field "$BODY" id)" "$hash" \
            "$(field "$BODY" legacy_supabase_keys)" \
            "$(field "$BODY" deprecated_in_use)" \
            "${build:-unstamped}" >> "$SEEN"
    done

    if [ "$nofinger" -gt 0 ]; then
        echo "$nofinger of $SAMPLES answers carried no config_fingerprint — those nodes"
        echo "are running a build from before it existed. Deploy, then re-run."
        echo ""
    fi
    if [ "$ok" -eq 0 ]; then
        echo "INCONCLUSIVE — no usable answers from $base."
        exit 2
    fi

    printf '%-24s %-5s %-14s %-10s %s\n' \
           "NODE ID" "SEEN" "FLEET HASH" "BUILD" "NOTES"
    printf '%-24s %-5s %-14s %-10s %s\n' "------------------------" "-----" \
           "--------------" "----------" "-----"
    sort "$SEEN" | uniq -c | while read -r count id hash legacy stale build; do
        note=""
        [ "${legacy:-0}" != "0" ] && note="$legacy legacy Supabase key(s)"
        [ "${stale:-0}" != "0" ] && note="$note${note:+; }$stale deprecated setting(s)"
        printf '%-24s %-5s %-14s %-10s %s\n' \
               "$id" "$count" "$hash" "${build:-unstamped}" "$note"
    done

    nodes="$(cut -f1 "$SEEN" | sort -u | wc -l | tr -d ' ')"
    hashes="$(cut -f2 "$SEEN" | sort -u | wc -l | tr -d ' ')"
    only="$(cut -f1 "$SEEN" | head -1)"
    agreed="$(cut -f2 "$SEEN" | head -1)"
    echo ""

    if [ "$hashes" -gt 1 ]; then
        echo "DRIFT — $nodes node(s) answered and they do not agree."
        echo "Which setting: GET /api/v1/config/fingerprint on each (needs a bearer"
        echo "token), then diff the .digests objects. Values are digests, so the"
        echo "output is safe to paste into a ticket."
        exit 1
    fi
    if [ "$nodes" -lt 2 ]; then
        echo "INCONCLUSIVE — all $ok sample(s) reached the same node ($only)."
        echo ""
        echo "Nothing was learned about the others. Either the balancer has session"
        echo "affinity, or every node reports the same ID — which would be its own"
        echo "problem, since ID exists to say which server answered."
        echo "Try: CHECK_FLEET_SAMPLES=$((SAMPLES * 3)) $0 $base"
        exit 2
    fi
    builds="$(cut -f5 "$SEEN" | sort -u | wc -l | tr -d ' ')"
    if [ "$builds" -gt 1 ]; then
        echo "BUILD SKEW - configuration agrees ($agreed), but the nodes are"
        echo "running different images. Normal mid-deploy; a problem if it persists."
        echo ""
        echo "Model chains, prompts and every non-secret default ship INSIDE the"
        echo "image, so two builds can behave differently while reporting identical"
        echo "configuration - which is what makes a bug look intermittent rather"
        echo "than like the deployment difference it is."
        exit 3
    fi
    echo "AGREED — $nodes nodes seen across $ok sample(s), all reporting $agreed"
    echo "         on build $(cut -f5 "$SEEN" | head -1)."
    exit 0
fi

# ── Direct mode ──────────────────────────────────────────────────────────────
first_hash=""
first_build=""
unreachable=0
drift=0
skew=0

printf '%-34s %-14s %-10s %s\n' "NODE" "FLEET HASH" "BUILD" "ID"
printf '%-34s %-14s %-10s %s\n' "----------------------------------" \
       "--------------" "----------" "----------------"

for base in "$@"; do
    if ! fetch "$base"; then
        printf '%-40s %s\n' "$base" "UNREACHABLE"
        sed 's/^/    /' "$ERR" 2>/dev/null | head -2
        unreachable=1
        continue
    fi
    if [ "$CODE" != "200" ]; then
        printf '%-40s %s\n' "$base" "HTTP $CODE"
        explain "$CODE"
        unreachable=1
        continue
    fi

    hash="$(field "$BODY" fleet_hash)"
    if [ -z "$hash" ]; then
        printf '%-40s %s\n' "$base" "NO FINGERPRINT"
        echo "    Answered, but with no config_fingerprint — this node is running a"
        echo "    build from before the fingerprint existed. Deploy, then re-run."
        unreachable=1
        continue
    fi

    # "unstamped" is an image built before CYCLONE_BUILD existed. It is a
    # distinct answer from a build that differs, and reported as one.
    build="$(field "$BODY" build)"
    build="${build:-unstamped}"
    printf '%-34s %-14s %-10s %s\n' "$base" "$hash" "$build" "$(field "$BODY" id)"
    [ -z "$first_hash" ] && first_hash="$hash"
    [ "$hash" != "$first_hash" ] && drift=1
    [ -z "$first_build" ] && first_build="$build"
    [ "$build" != "$first_build" ] && skew=1

    legacy="$(field "$BODY" legacy_supabase_keys)"
    stale="$(field "$BODY" deprecated_in_use)"
    [ "${legacy:-0}" != "0" ] && \
        echo "    ^ $legacy legacy Supabase JWT key(s) — retired end of 2026"
    [ "${stale:-0}" != "0" ] && \
        echo "    ^ $stale deprecated setting(s) still in .env"
done

echo ""
if [ "$unreachable" -eq 1 ]; then
    echo "INCONCLUSIVE — at least one node could not be read."
    echo ""
    echo "If there is no per-node address, sample the load balancer instead:"
    echo "    $0 $1"
    exit 2
fi
if [ "$drift" -eq 1 ]; then
    echo "DRIFT — these nodes do not agree."
    echo "Which setting: GET /api/v1/config/fingerprint on each (needs a bearer token),"
    echo "then diff the .digests objects. Values are digests, so the output is safe"
    echo "to paste into a ticket."
    exit 1
fi
if [ "$skew" -eq 1 ]; then
    echo "BUILD SKEW — configuration agrees ($first_hash), but these nodes"
    echo "are running different images. Normal mid-deploy; a problem if it"
    echo "persists."
    echo ""
    echo "Model chains, prompts and every non-secret default ship INSIDE the"
    echo "image, so two builds can behave differently while reporting identical"
    echo "configuration — which is what makes a bug look intermittent rather"
    echo "than like the deployment difference it is."
    exit 3
fi
echo "AGREED — every node reports $first_hash on build $first_build."
