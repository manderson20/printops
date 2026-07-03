# Changelog

All notable changes to PrintOps are documented here. Each entry is keyed by
the version in the root `VERSION` file — the in-app Updates page extracts a
version's section from this file to show "what's new" before an admin
schedules an update.

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
