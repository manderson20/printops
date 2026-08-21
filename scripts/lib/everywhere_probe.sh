#!/usr/bin/env bash
# Shared guard for `lpadmin -m everywhere`, sourced by scripts/sync_cups_queue.sh
# and scripts/sync_release_queue.sh.
#
# `timeout 30 lpadmin -m everywhere` bounds the *client*, and that is not the
# same thing as bounding the work. `-m everywhere` is a request to cupsd; cupsd
# is what queries the device to build the driverless PPD, on its own thread.
# Killing the lpadmin client leaves that thread running, and against a device
# that never satisfies the request it never finishes — it retries in a tight
# loop, with no backoff, for as long as cupsd lives. Only a cupsd restart
# clears one.
#
# Confirmed live on the LCACTC Kyocera (2026-08-20), which had been switched to
# TLS-only IPP so its configured cleartext address no longer served anything:
# leaked threads accumulated across an afternoon of resyncs and were together
# opening ~600 connections/second to the device, holding its IPP service
# saturated so that every queue pointed at it failed.
#
# This lives in one file because the first version of the fix guarded only
# sync_cups_queue.sh. sync_queue() (app/printers/queue_sync.py) calls *both*
# scripts, so the release queue went on leaking exactly as before and the storm
# came back within two minutes of deploying the fix. Two copies of a guard is
# one copy too many.

# 20s, not the few seconds a reachability check would need: this deliberately
# asks for the whole attribute set, and a healthy device can legitimately be
# slow at it. Measured live — the Canon TM-300 plotter takes 10.9s to serve it
# (1ms away; it's the size of its media-col-database, not the network), while a
# device that refuses outright fails in well under a second. 20s sits clear of
# both, and still leaves room under the 30s -m everywhere timeout and
# queue_sync.py's 90s ceiling.
EVERYWHERE_PROBE_TIMEOUT="${PRINTOPS_EVERYWHERE_PROBE_TIMEOUT:-20}"

# Asks the device, up front and cheaply, whether it can serve the exact request
# `-m everywhere` will make cupsd issue. If it can't, cupsd is never handed that
# request — the only reliable way to not leak the thread, since it cannot be
# cancelled afterwards. Also strictly faster on the failing path: a refusal
# costs one short probe instead of the full 30s timeout.
#
# Returns 0 (allow -m everywhere) or non-zero (skip it).
everywhere_probe_ok() {
    local uri="$1"

    # No ipptool (minimal container, unusual CUPS packaging) means no way to
    # pre-check. Fall through to the old behaviour rather than refusing to
    # build an accurate PPD for every printer on the estate.
    command -v ipptool >/dev/null 2>&1 || return 0

    local test_file
    test_file=$(mktemp)
    # `all` plus `media-col-database` is precisely what CUPS asks for when
    # generating an `everywhere` PPD — verified against cupsd's own traffic.
    # Probing with a smaller attribute set would pass on exactly the devices
    # this is meant to catch: the Kyocera answers targeted requests (this app's
    # capability and status probes) perfectly well and fails only on this one.
    cat > "$test_file" <<'IPPTEST'
{
    OPERATION Get-Printer-Attributes
    GROUP operation-attributes-tag
    ATTR charset attributes-charset utf-8
    ATTR language attributes-natural-language en
    ATTR uri printer-uri $uri
    ATTR keyword requested-attributes all,media-col-database
    STATUS successful-ok
}
IPPTEST

    local rc=0
    timeout "$EVERYWHERE_PROBE_TIMEOUT" ipptool -T "$EVERYWHERE_PROBE_TIMEOUT" -q "$uri" "$test_file" >/dev/null 2>&1 || rc=$?
    rm -f "$test_file"
    return "$rc"
}
