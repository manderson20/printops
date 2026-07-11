#!/usr/bin/env bash
# Creates (or updates) a CUPS queue that routes through PrintOps's custom
# backend (infra/cups/backends/printops) for a given registered printer.
#
# Usage: ./scripts/sync_cups_queue.sh <printer-id>
#
# Invoked automatically by the API (app/printers/queue_sync.py) on printer
# create/update — see that module for the non-fatal error-handling contract
# (Printer.queue_sync_error). Safe to also run manually/standalone, e.g. to
# recover after fixing whatever caused a sync failure without needing
# another edit through the UI. See also scripts/remove_cups_queue.sh.

set -euo pipefail

PRINTER_ID="${1:?Usage: sync_cups_queue.sh <printer-id>}"
API_BASE="${PRINTOPS_API_BASE:-http://localhost:8000}"
ENV_FILE="${PRINTOPS_ENV_FILE:-/home/itadmin/printops/apps/api/.env}"

TOKEN=$(grep '^PRINTOPS_BACKEND_TOKEN=' "$ENV_FILE" | cut -d= -f2)

PRINTER_JSON=$(curl -sf -H "X-Backend-Token: $TOKEN" "$API_BASE/api/v1/internal/printers/$PRINTER_ID/connection")

PRINTER_NAME=$(python3 -c "import json,sys; print(json.load(sys.stdin)['name'])" <<<"$PRINTER_JSON")
IS_VIRTUAL=$(python3 -c "import json,sys; print('true' if json.load(sys.stdin)['is_virtual'] else 'false')" <<<"$PRINTER_JSON")
AIRPRINT_ENABLED=$(python3 -c "import json,sys; print('true' if json.load(sys.stdin)['airprint_enabled'] else 'false')" <<<"$PRINTER_JSON")

if [ "$IS_VIRTUAL" = false ]; then
    REAL_IP=$(python3 -c "import json,sys; print(json.load(sys.stdin)['ip_address'])" <<<"$PRINTER_JSON")
    REAL_PORT=$(python3 -c "import json,sys; print(json.load(sys.stdin)['port'])" <<<"$PRINTER_JSON")
    REAL_SCHEME=$(python3 -c "import json,sys; print('ipps' if json.load(sys.stdin)['use_tls'] else 'ipp')" <<<"$PRINTER_JSON")
    REAL_PATH=$(python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('ipp_path') or '/ipp/print')" <<<"$PRINTER_JSON")
    REAL_URI="${REAL_SCHEME}://${REAL_IP}:${REAL_PORT}${REAL_PATH}"
fi

# CUPS queue names are restricted to alnum/-/_; the printer UUID guarantees
# uniqueness, with the human name set separately via printer-info (-D).
QUEUE_NAME="printops-${PRINTER_ID}"

# This queue may already have a real, accurate PPD from a prior successful
# sync — e.g. this same script running again after the printer reconnects
# (app/printers/status.py's offline->online trigger), or an unrelated edit
# that happened to re-trigger a sync while the printer was momentarily slow.
# Recorded *before* attempting -m everywhere below, so a failure this time
# doesn't wrongly downgrade an already-working queue.
PPD_FILE="/etc/cups/ppd/${QUEUE_NAME}.ppd"
HAD_REAL_PPD=false
if [ -f "$PPD_FILE" ] && ! sudo grep -q '^\*NickName: "Generic IPP Everywhere Printer"' "$PPD_FILE"; then
    HAD_REAL_PPD=true
fi

# Step 1: probe the REAL printer once via `-m everywhere` so CUPS builds an
# accurate driverless PPD (correct document-format/pdl advertisement, needed
# for AirPrint clients to recognize this as a usable destination) from what
# the device actually reports — not a guess.
#
# `-m everywhere` requests the device's full attribute set (all,
# media-col-database internally) — some devices can't handle that and
# hang or drop the connection outright (confirmed live: a Kyocera ECOSYS
# returns 0 bytes/service-unavailable specifically on that request, even
# though it answers smaller targeted IPP requests, like this app's own
# capability/status probes, just fine). Bounded timeout + fall back to a
# generic driverless PPD rather than let the whole sync hang the full
# Python-level timeout (queue_sync.py) with nothing to show for it —
# loses precise media-size/capability advertisement for that one queue,
# but the printer actually becomes usable instead of stuck unsynced.
#
# That fallback is only safe to apply when there's nothing to lose, though
# (confirmed live: MS - Cletus Copier's monochrome engine got dithered,
# pixelated output the whole time its queue ran on this generic PPD's
# RGB-default, color-advertising PPD). If this queue already had a real PPD
# from a prior successful probe, a transient failure this time around
# should leave that working config alone rather than regress it.
#
# A virtual Follow-Me queue (IS_VIRTUAL) has no real device at all — there's
# nothing to probe, so it always goes straight to the generic driverless PPD
# branch below rather than attempting -m everywhere first. This generic PPD
# advertises full color support by default (see the color-supported check
# further down), which is exactly what's wanted here: real delivery happens
# later at whichever physical printer the job is released to, so this queue
# shouldn't be the thing silently downgrading Word/Adobe jobs to grayscale
# (the same failure mode fixed for real printers in the Danica investigation).
if [ "$IS_VIRTUAL" = true ] || ! timeout 30 sudo lpadmin -p "$QUEUE_NAME" -v "$REAL_URI" -m everywhere -D "$PRINTER_NAME"; then
    if [ "$IS_VIRTUAL" = true ]; then
        sudo lpadmin -p "$QUEUE_NAME" -v "ipp://virtual.printops.internal/" -m "drv:///cupsfilters.drv/pwgrast.ppd" -D "$PRINTER_NAME"
    elif [ "$HAD_REAL_PPD" = true ]; then
        echo "WARNING: -m everywhere failed/timed out for $REAL_URI — keeping this queue's existing real PPD from a prior successful sync instead of regressing it to the generic fallback." >&2
        sudo lpadmin -p "$QUEUE_NAME" -D "$PRINTER_NAME"
    else
        echo "WARNING: -m everywhere failed/timed out for $REAL_URI — falling back to a generic IPP Everywhere PPD (reduced capability accuracy for this printer)." >&2
        sudo lpadmin -p "$QUEUE_NAME" -v "$REAL_URI" -m "drv:///cupsfilters.drv/pwgrast.ppd" -D "$PRINTER_NAME"
    fi
fi

# The generic-PPD fallback above can leave a newly-created queue disabled/
# rejecting jobs by default (confirmed live) — -m everywhere queues don't
# need this, but ensure both explicitly either way; enabling an
# already-enabled queue is a harmless no-op.
sudo cupsenable "$QUEUE_NAME"
sudo cupsaccept "$QUEUE_NAME"

# CUPS's own driverless-PPD generation (or, on this box, at least one past
# manual misconfiguration — confirmed live on 4 color copiers) can leave a
# genuinely color-capable printer defaulting to print-color-mode=monochrome.
# Apps that explicitly request a color mode (Chrome) are unaffected either
# way, but apps that submit a job without an explicit color option (Word,
# Adobe, confirmed live) silently inherit whatever this queue's default is —
# so a color printer defaulting to monochrome here means those apps print
# monochrome despite the user picking Color in their print dialog. Force the
# default to color for any printer that actually supports it, rather than
# leaving it to chance.
COLOR_SUPPORTED=$(ipptool -X "ipp://localhost/printers/$QUEUE_NAME" /dev/stdin <<IPPTOOL_EOF 2>/dev/null | grep -A1 "<key>color-supported</key>" | grep -c "<true" || true
{
    OPERATION Get-Printer-Attributes
    GROUP operation-attributes-tag
    ATTR charset attributes-charset utf-8
    ATTR language attributes-natural-language en
    ATTR uri printer-uri ipp://localhost/printers/$QUEUE_NAME
    ATTR keyword requested-attributes color-supported
}
IPPTOOL_EOF
)
if [ "$COLOR_SUPPORTED" -ge 1 ]; then
    sudo lpadmin -p "$QUEUE_NAME" -o print-color-mode-default=color
    # print-color-mode-default above only covers the modern IPP attribute.
    # The driverless PPD `-m everywhere` just (re)generated above carries
    # its OWN, separate color default — *DefaultColorModel, exposed here as
    # the "ColorModel" option — which CUPS's classic PPD-based print path
    # reads instead. `-m everywhere` always sets that to Gray regardless of
    # the device's real color capability (confirmed live), so every time
    # this script reruns for a color printer (e.g. an offline->online
    # reconnect re-triggering the sync, not just an intentional edit), a
    # queue previously fixed by the print-color-mode-default line above
    # silently reverts to monochrome for these apps even though this script
    # ran again and "should" have kept it fixed. RGB is consistently the
    # PPD's "Color" choice label (confirmed live across all current color
    # queues) — tolerate failure in case a future device's PPD names it
    # differently, since print-color-mode-default above still covers the
    # apps that use it.
    sudo lpadmin -p "$QUEUE_NAME" -o ColorModel=RGB || true
fi

# cupsd.conf's global ErrorPolicy is retry-job, which keeps retrying the
# SAME failed job rather than skipping to the next one — a single bad job
# (corrupt file, printer momentarily rejecting it, etc.) then jams every
# other job queued behind it on this printer until someone notices and
# cancels it manually. abort-job cancels just the failing job and lets the
# queue keep moving; app/printers/queue_sync.py's custom backend already
# records the failure on the Job row (visible on the Jobs page) regardless
# of this policy, so nothing is lost by not stopping to retry.
sudo lpadmin -p "$QUEUE_NAME" -o printer-error-policy=abort-job

# Step 2: repoint device-uri only, back to our backend, so jobs still route
# through PrintOps for logging + forwarding. The PPD/capabilities CUPS just
# derived from the real printer in step 1 are kept.
sudo lpadmin -p "$QUEUE_NAME" -v "printops://${PRINTER_ID}"

# printer-is-shared is always true — it's what allows ANY network client
# (discovered via mDNS or explicitly configured, e.g. an MDM-pushed printer
# profile) to reach the queue at all; CUPS refuses network job submission
# entirely when a queue isn't shared, regardless of how the client found it.
# airprint_enabled below controls *discovery* only, not this.
sudo lpadmin -p "$QUEUE_NAME" -o printer-is-shared=true -E

# mDNS/AirPrint *discoverability* is controlled per-printer via PrintOps.
# Off (airprint_enabled=false) means the queue won't show up in automatic
# "Add Printer" pickers, but still accepts jobs from explicitly-configured
# clients (e.g. MDM) since sharing itself (above) is unaffected by this.
# cupsd's own DNS-SD publishing doesn't work on this box (confirmed via
# debug logging — see infra/cups/README.md), so we publish the AirPrint
# advertisement ourselves via a static Avahi service file instead.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
sudo python3 "${SCRIPT_DIR}/../infra/cups/generate_avahi_service.py" "$PRINTER_ID"

echo "Queue '$QUEUE_NAME' -> printops://${PRINTER_ID} (${PRINTER_NAME}), airprint_enabled=${AIRPRINT_ENABLED}"
