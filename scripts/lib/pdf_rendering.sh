#!/usr/bin/env bash
# Shared "who renders a PDF" decision, sourced by scripts/sync_cups_queue.sh and
# scripts/sync_release_queue.sh.
#
# `-m everywhere` builds a PPD that hands PDFs straight to any device listing
# application/pdf:
#
#   *cupsFilter2: "application/vnd.cups-pdf application/pdf 10 -"
#
# so the device's own PDF interpreter renders every page. That is usually the
# best choice, and on some printers it is the worst one. An older interpreter
# can fail on one particular document while printing the next hundred fine, and
# it fails in ways nothing upstream can see:
#
# - An HP LaserJet 600 M601 crashed with firmware error 49.4A.04 on a single
#   Chrome-on-macOS PDF. CUPS kept retrying the job, so every power-cycle
#   brought the printer up just long enough to receive the same file and crash
#   again. It was down for three days before anyone traced it to the job.
# - An HP LaserJet M607 accepted some PDFs, reported job-completed-successfully
#   and printed zero sheets.
#
# Printer.render_pdf_on_server removes the passthrough line, so CUPS renders the
# document on this server and sends the printer finished raster pages (URF or
# PWG) instead. The device never parses the PDF at all.
#
# Re-applied on every sync, because `-m everywhere` writes the passthrough line
# back each time. Callers must run this before setting any PPD-level default
# (ColorModel, the cutter): it installs an edited copy of the PPD with
# `lpadmin -P`, and those defaults are stored in the PPD itself.
#
# One file for the same reason as lib/everywhere_probe.sh and
# lib/color_default.sh. A held or Follow-Me job is delivered through the
# release queue, so a PDF rule applied to the client-facing queue alone would
# send exactly the jobs that waited for a crashed printer back through its PDF
# interpreter.

# Any cost: queues built by `-m everywhere` use 10, older cups-filters
# driverless PPDs on the same server use 0. Both hand the device the PDF.
PDF_PASSTHROUGH_PATTERN='^\*cupsFilter2: "[^" ]+ application/pdf [0-9]+ -"'
PDF_PASSTHROUGH_LINE='*cupsFilter2: "application/vnd.cups-pdf application/pdf 10 -"'

# Formats CUPS can render a PDF into for a driverless printer. JPEG is not one:
# a queue left with only JPEG passthrough has no way to print a PDF at all.
RASTER_OUTPUT_PATTERN='^\*cupsFilter2: "[^" ]+ (image/urf|image/pwg-raster|application/PCLm) [0-9]+ -"'

_printer_json_flag() {
    local printer_json="$1"
    local expression="$2"
    python3 -c "
import json, sys
d = json.load(sys.stdin)
print('true' if ($expression) else 'false')
" <<<"$printer_json"
}

apply_pdf_rendering() {
    local queue_name="$1"
    local ppd_file="$2"
    local printer_json="$3"
    local printer_name="$4"

    local wanted
    wanted=$(_printer_json_flag "$printer_json" "d.get('render_pdf_on_server')")

    if ! sudo grep -q '^\*cupsFilter2:' "$ppd_file" 2>/dev/null; then
        if [ "$wanted" = true ]; then
            echo "WARNING: render_pdf_on_server is on for $printer_name but $ppd_file has no document formats to change." >&2
        fi
        return 0
    fi

    local has_passthrough=false
    if sudo grep -qE "$PDF_PASSTHROUGH_PATTERN" "$ppd_file"; then
        has_passthrough=true
    fi

    local edited
    if [ "$wanted" = true ]; then
        if [ "$has_passthrough" = false ]; then
            return 0
        fi
        edited=$(mktemp)
        sudo grep -vE "$PDF_PASSTHROUGH_PATTERN" "$ppd_file" >"$edited"
        # Removing the only way to print is worse than a printer that fails on
        # some PDFs, so a queue with nothing to render into keeps passthrough.
        if ! grep -qE "$RASTER_OUTPUT_PATTERN" "$edited"; then
            rm -f "$edited"
            echo "WARNING: render_pdf_on_server is on for $printer_name, but its queue '$queue_name' offers no raster format to render into — leaving PDFs passed through to the printer." >&2
            return 0
        fi
        sudo lpadmin -p "$queue_name" -P "$edited"
        rm -f "$edited"
        echo "PDFs for '$queue_name' are rendered on this server (${printer_name})"
        return 0
    fi

    # Turned off, or never on. A queue with passthrough is the ordinary case
    # for nearly every printer, and must be left byte-identical.
    if [ "$has_passthrough" = true ]; then
        return 0
    fi

    # No passthrough line, and not wanted. Usually that is correct as it
    # stands: the generic fallback PPD renders everything, and a device that
    # never listed PDF was never given one. The case to undo is a PPD an
    # earlier run edited, kept because -m everywhere failed this time (see
    # HAD_REAL_PPD in the callers). Without this, turning the setting off
    # would say it had and change nothing until the printer next answered a
    # full probe.
    if sudo grep -q '^\*NickName: "Generic IPP Everywhere Printer"' "$ppd_file"; then
        return 0
    fi
    local device_takes_pdf
    device_takes_pdf=$(_printer_json_flag "$printer_json" \
        "'application/pdf' in ((d.get('capabilities') or {}).get('document_formats') or [])")
    if [ "$device_takes_pdf" = false ]; then
        return 0
    fi

    edited=$(mktemp)
    # grep '' rather than cat: every read of a queue's PPD in these scripts
    # goes through `sudo grep`.
    sudo grep '' "$ppd_file" | awk -v line="$PDF_PASSTHROUGH_LINE" \
        '!done && /^\*cupsFilter2:/ { print line; done = 1 } { print }' >"$edited"
    sudo lpadmin -p "$queue_name" -P "$edited"
    rm -f "$edited"
    echo "PDFs for '$queue_name' are passed through to the printer again (${printer_name})"
}
