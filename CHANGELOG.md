# Changelog

All notable changes to PrintOps are documented here. Each entry is keyed by
the version in the root `VERSION` file — the in-app Updates page extracts a
version's section from this file to show "what's new" before an admin
schedules an update.

## [0.3.0] - 2026-07-03

- **Print-and-release kiosk.** Printers can now be marked "release
  required" — jobs sent to them are held (spooled, not printed) until
  released at a per-printer kiosk URL (`/release/<token>`, works from any
  iPad, Chromebook, or browser) by entering a Google Workspace Employee
  ID, the same number staff already use at the copier panel. Prevents
  accidental prints and mixed-up output at shared printers/copiers. Held
  jobs auto-expire after an admin-configurable window (default 4 hours).
  Printer detail page gained a Print Release admin card (toggle, kiosk
  link with copy/regenerate).
- **Copier PIN roster.** Google Workspace sync now pulls each staff
  member's Employee ID, exportable as a copier PIN roster CSV, powering
  the print-release kiosk PIN above. The staff org-unit filter used to
  build the roster is admin-configurable rather than hardcoded, so it
  adapts to any district's OU structure.

## [0.2.0] - 2026-07-03

- **Printer status monitoring.** A background check now polls every
  printer's real IPP state every 60 seconds and reports online/error/
  offline (plus a manual "Check Now"), shown on the printers list and
  detail page.
- **Job cancel / queue purge.** Admins can cancel a single stuck job or
  purge a printer's entire CUPS queue when a bad job jams it. The Jobs
  page gained printer/status filters, sortable columns, and a stuck-job
  hint.
- **Print Insights.** A new `/insights` dashboard turns job history into a
  timeline, fun facts, printer/user leaderboards, and environmental/cost
  estimates, with filters, CSV export, a print-friendly summary view, and
  admin-saved snapshots that freeze their numbers even if formulas change
  later. Also extends job capture (going forward only) with document
  name, copy count, color mode, duplex, and paper size.
- **Real toner/paper cost model.** Cost estimates now use each printer's
  actual toner cartridge costs and rated page yields (configurable per
  printer, color printers get separate black/cyan/magenta/yellow rows)
  plus a global paper cost per sheet, instead of one flat org-wide rate —
  falling back to the flat rate for any printer that isn't configured
  yet. A new cost-by-user (or by-printer) breakdown is available from the
  Insights leaderboard.
- **Devices page fix.** The device list no longer renders a full copy of
  the Google Workspace roster per row — with a large roster and device
  count this was creating millions of DOM nodes and freezing the page.

## [0.1.0] - 2026-07-03

- **Device attribution overrides.** Admins can now view every device seen
  via Mosyle or Google Workspace on a new `/devices` page and set/correct
  the email a device's print jobs are attributed to. Setting an override
  immediately backfills that device's already-logged jobs.
- **Google Workspace user roster sync.** Beyond ChromeOS device inventory,
  PrintOps now syncs the full Workspace user directory. This roster
  validates device-override emails and powers the two changes below.
- **Usage report is now roster-driven.** `/usage` lists every synced
  Workspace user — including anyone who hasn't printed yet — instead of
  only whoever happened to submit a job. Print activity that can't be
  matched to a roster address is rolled into a single "Other /
  Unattributed" row instead of being silently mixed in or dropped. The
  page also gained a name/email search box and CSV export.
- **Mosyle/Workspace identity reconciliation.** Job attribution no longer
  trusts Mosyle's reported email outright. It's first confirmed against
  the Workspace roster; if Mosyle's email is a stale alias that doesn't
  match, PrintOps falls back to matching Mosyle's separately-reported
  username against the roster (by email local part) before trusting
  Mosyle's raw value as a last resort. Ambiguous username matches are
  never guessed.
- **Software version + update workflow.** The running version is now shown
  in the UI, with an admin-only Updates page that checks GitHub for a newer
  version and lets an admin schedule when to apply it (git pull, DB
  migration, rebuild, service restart) instead of doing it by hand over SSH.
