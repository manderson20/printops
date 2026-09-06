#!/usr/bin/env bash
# Shared color-default decision, sourced by scripts/sync_cups_queue.sh and
# scripts/sync_release_queue.sh.
#
# Both directions of this go wrong, and both are silent. Apps that name a
# color mode explicitly (Chrome) are unaffected either way, but apps that
# submit without one (Word, Adobe, confirmed live) inherit the queue default —
# so a color printer defaulting to monochrome prints grey no matter what the
# user picked in the print dialog, and a mono printer defaulting to color
# offers a choice that does nothing but waste the user's time and make the job
# look like a color job.
#
# This used to ask `ipp://localhost/printers/$QUEUE_NAME` — the queue the
# calling script had just built — which is the bug in #94. A printer too old to
# answer the driverless attribute request lands on the generic cupsfilters PPD,
# and that PPD hardcodes `*ColorDevice: True` because a generic PPD has to
# claim everything. Asking the queue therefore read the fallback's own guess
# back, believed it, and set color as the default on monochrome hardware. The
# device was never consulted.
#
# So consult the device — or rather, consult what PrintOps already learned from
# it. app/printers/discovery.py probes each printer directly and stores
# capabilities.color_supported, and its targeted request succeeds on printers
# where `-m everywhere` fails (confirmed live: all three misreporting printers
# are probed, and all three report false). The API hands it over in the
# connection payload both scripts already fetch.
#
# This lives in one file for the same reason lib/everywhere_probe.sh does. The
# first version of that fix guarded only sync_cups_queue.sh; queue_sync.py runs
# both scripts, so the leak survived untouched and the storm came back within
# two minutes of the deploy. The release queue delivers held jobs with `lp -d`
# and inherits its own default exactly like the client-facing one, so a color
# rule applied to one script and not the other is half a fix. Two copies of
# this decision is one copy too many.

# Echoes true / false / unknown for what the *device* supports.
#
# Three states, and the third is the important one. A printer PrintOps has
# never successfully probed reports no capabilities at all, and must not be
# guessed at in either direction: assuming mono there would downgrade a color
# printer to grey, which is #94 with the sign flipped.
probed_color_support() {
    local printer_json="$1"
    python3 -c "
import json, sys
d = json.load(sys.stdin)
caps = d.get('capabilities')
if caps is None or caps.get('color_supported') is None:
    print('unknown')
else:
    print('true' if caps['color_supported'] else 'false')
" <<<"$printer_json"
}

# Sets both color defaults on a queue, in whichever direction the device says.
#
# print-color-mode-default covers the modern IPP attribute. The driverless PPD
# carries its OWN, separate default — *DefaultColorModel, exposed as the
# "ColorModel" option — which CUPS's classic PPD-based print path reads
# instead, and `-m everywhere` rewrites it on every sync regardless of the
# device's real capability (confirmed live). Both have to be set, every run, or
# a queue fixed once silently reverts the next time the printer reconnects and
# re-triggers a sync. RGB and Gray are consistently the PPD's choice labels
# across the current fleet — tolerated on failure in case a future device's PPD
# names them differently, since print-color-mode-default still covers the apps
# that read it.
#
# Setting it in *both* directions is itself part of the #94 fix. The old code
# only ever forced color; there was no branch that corrected a wrong
# monochrome, so a color queue that landed on mono stayed there permanently
# through every resync (confirmed live on IT Department Color Copier).
apply_color_default() {
    local queue_name="$1"
    local printer_json="$2"
    local is_virtual="$3"
    local printer_name="$4"

    local color
    color=$(probed_color_support "$printer_json")

    # A virtual Follow-Me queue has no device to have probed, so it reports
    # unknown — but its answer is not in doubt. Real delivery happens later at
    # whichever physical printer the job is released to, and this queue must
    # not be the thing that quietly strips color on the way through.
    if [ "$is_virtual" = true ]; then
        color=true
    fi

    if [ "$color" = "unknown" ]; then
        local queue_claims
        queue_claims=$(ipptool -X "ipp://localhost/printers/$queue_name" /dev/stdin <<IPPTOOL_EOF 2>/dev/null | grep -A1 "<key>color-supported</key>" | grep -c "<true" || true
{
    OPERATION Get-Printer-Attributes
    GROUP operation-attributes-tag
    ATTR charset attributes-charset utf-8
    ATTR language attributes-natural-language en
    ATTR uri printer-uri ipp://localhost/printers/$queue_name
    ATTR keyword requested-attributes color-supported
}
IPPTOOL_EOF
)
        if [ "$queue_claims" -ge 1 ]; then
            color=true
        else
            color=false
        fi
        echo "NOTE: $printer_name has no probed color capability — falling back to what the queue claims (color=$color). Run a capability detection against this printer to settle it." >&2
    fi

    if [ "$color" = true ]; then
        sudo lpadmin -p "$queue_name" -o print-color-mode-default=color
        sudo lpadmin -p "$queue_name" -o ColorModel=RGB || true
    else
        sudo lpadmin -p "$queue_name" -o print-color-mode-default=monochrome
        sudo lpadmin -p "$queue_name" -o ColorModel=Gray || true
    fi
}
