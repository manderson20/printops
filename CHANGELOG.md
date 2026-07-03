# Changelog

Notable changes to PrintOps, dated by when they shipped to production. Not
every commit gets an entry — this tracks user- and admin-facing changes,
not internal refactors.

## 2026-07-03

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
