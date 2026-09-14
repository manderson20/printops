#!/usr/bin/env bash
# Keeps cups-browsed switched off on a PrintOps server.
#
# Ubuntu installs cups-browsed alongside CUPS and enables it, with
# `BrowseRemoteProtocols dnssd`. Its job is to find printers other servers
# advertise and make local queues for them. On a PrintOps server the printers
# it finds are PrintOps's own: every queue with queue discovery on is advertised
# over DNS-SD (infra/cups/generate_avahi_service.py), so cups-browsed builds a
# local copy of each one, pointed back at the same cupsd.
#
# Building a copy means asking cupsd about the queue it copies, and cupsd has a
# fixed pool of client slots (MaxClients, 100 by default). After every CUPS
# restart cups-browsed rebuilds all of its copies at once. Measured on a
# 107-queue install (2026-09-14): it held about 100 connections, cupsd logged
# "Max clients reached, holding new connections" for 10–15 minutes after each
# restart, the web interface on 631 stopped answering, and teachers' jobs
# waited for a free slot. cups-browsed's own log showed each queue creation
# timing out in turn. Nothing had printed to any of its 53 copies in two days.
#
# Idempotent: does nothing when cups-browsed is absent or already off.
# PRINTOPS_KEEP_CUPS_BROWSED=1 skips it, for a server that also needs to use
# printers advertised by a different machine.
set -euo pipefail

UNIT=cups-browsed.service

if [ "${PRINTOPS_KEEP_CUPS_BROWSED:-}" = "1" ]; then
    echo "PRINTOPS_KEEP_CUPS_BROWSED=1 — leaving cups-browsed as it is."
    exit 0
fi

if ! systemctl list-unit-files "$UNIT" --no-legend 2>/dev/null | grep -q "^${UNIT}"; then
    echo "cups-browsed is not installed — nothing to do."
    exit 0
fi

enabled="$(systemctl is-enabled "$UNIT" 2>/dev/null || true)"
active="$(systemctl is-active "$UNIT" 2>/dev/null || true)"

case "$enabled" in
    enabled|enabled-runtime) want_disable=true ;;
    *) want_disable=false ;;
esac
case "$active" in
    active|activating|reloading) want_stop=true ;;
    *) want_stop=false ;;
esac

if [ "$want_disable" = false ] && [ "$want_stop" = false ]; then
    echo "cups-browsed is already off (${enabled:-unknown}, ${active:-unknown})."
    exit 0
fi

# Stopping it removes the queues it created, so nothing is left pointing at
# its implicitclass backend.
sudo systemctl disable --now "$UNIT"
echo "Turned off cups-browsed (was ${enabled:-unknown}, ${active:-unknown})."
