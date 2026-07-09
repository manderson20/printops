# Changelog

All notable changes to PrintOps are documented here. Each entry is keyed by
the version in the root `VERSION` file — the in-app Updates page extracts a
version's section from this file to show "what's new" before an admin
schedules an update.

## [0.16.2] - 2026-07-09

- **Bounded the MDM Printer Resync script's `-m everywhere` probe with a
  timeout.** The server-side sync script has always bounded this same
  probe to 30s (confirmed live: some printers hang on it entirely), but
  the client-side script omitted that protection — a single unresponsive
  printer could hang the whole script indefinitely, unattended, across
  the fleet. macOS doesn't ship GNU coreutils' `timeout(1)`, so this uses
  a portable background-job watchdog instead; verified it actually kills
  a simulated hung probe and moves on rather than stalling.

## [0.16.1] - 2026-07-08

- **Fixed the MDM Printer Resync script silently matching zero queues.**
  It required a printer queue's device-uri to contain the exact hostname
  string typed into the settings page — if an MDM's printer profile
  pointed at the server by IP address while the page had a DNS name (or
  vice versa), nothing matched, with no error at all: the script ran,
  reported success, and touched nothing. Confirmed live on MS - Cletus
  Copier, whose Mac-side queue was never actually refreshed by an earlier
  run, still showing its original stale capabilities (3x5 default paper
  size, a color option in Word) despite the script "completing." It now
  identifies PrintOps-managed queues purely by their device-uri path (an
  unmistakable signature no other queue would have) and reads each one's
  own host straight out of its own already-configured URI — no server
  hostname needs to be typed in or match anything, so the same script
  works unmodified on any PrintOps install regardless of how an MDM
  profile happens to address the server.

## [0.16.0] - 2026-07-08

- **New Settings tab: MDM Printer Resync.** A Mac only checks a printer's
  capabilities once, when it's first added — it never re-verifies against
  the server afterward, so a server-side fix (like the Cletus PPD repair
  above) doesn't reach Macs that already have the printer configured. This
  tab generates a self-contained shell script, prefilled with this
  install's own hostname, to push out via Mosyle's Custom Command Profiles
  (scheduled from Mosyle itself, not the script). It re-probes each
  PrintOps-managed queue already on the Mac in place — never deleting and
  recreating one — so the default printer, any app's saved printer
  preference, and jobs on other queues are all left alone. It skips a
  queue with a job pending right now, and exits untouched if this
  PrintOps server isn't reachable when it runs. No credentials are
  embedded in the script at all.

## [0.15.3] - 2026-07-08

- **Fixed pixelated, slightly-dark print output on a printer that was
  offline when its queue was first created.** MS - Cletus Copier (a Konica
  Minolta bizhub 651i, monochrome only) had its CUPS queue built while it
  was unreachable, so the `-m everywhere` probe in `sync_cups_queue.sh`
  timed out and fell back to CUPS's generic PWG-Raster PPD — which
  advertises RGB color support and a continuous-tone default regardless of
  the real device. Every job was then dithered down to black-only on the
  print engine, showing up as pixelated text and a Color option in print
  dialogs the printer doesn't actually have. Manually resyncing that queue
  fixed it directly (confirmed with a physical test print). To keep this
  from silently recurring on any other printer added while offline:
  reconnecting from offline to online now also retries the CUPS queue sync,
  not just the capability/status refresh it already did, so a printer gets
  its real driverless PPD as soon as it's actually reachable instead of
  needing someone to notice and click "Resync Queue" manually. Also closed
  a related gap in both sync scripts: a transient `-m everywhere` failure
  used to unconditionally reapply the generic fallback PPD even when a
  queue already had a real, working one from an earlier successful sync —
  a resync retry (including the new automatic one above) could have
  regressed an already-fine printer. The generic fallback is now only
  applied when a queue has never had a real PPD to begin with.

## [0.15.2] - 2026-07-06

- **Fixed color copiers silently defaulting to grayscale for some apps.**
  Word, Adobe, and similar apps that don't explicitly request a color mode
  inherit whatever a printer's queue declares as its default — four color
  copiers (CO Danica Copier, IT Department Color Copier, ES Room 102 Color
  Printer, ES Principal Color Copier) had a stored `print-color-mode`
  default of monochrome, so those apps printed grayscale despite the user
  selecting Color, while apps that set their own explicit color preference
  (Chrome) were unaffected. Corrected the default on all four printers'
  queues directly; `scripts/sync_cups_queue.sh` and
  `scripts/sync_release_queue.sh` now also detect color-capable printers
  during every future sync and force this default to color automatically,
  so this can't silently regress or recur on newly-added printers.
- **Fixed ES-MS Library Printer not printing PDFs correctly.** This older
  HP LaserJet 4250 doesn't support IPP Everywhere, so its queue had
  silently fallen back to CUPS's generic PWG-Raster PPD — a format this
  printer can't interpret at all, since it only accepts PostScript, PCL,
  and plain text. Reassigned both its client-facing and internal release
  queues to CUPS's Generic PostScript PPD, restoring the standard PDF
  filter chain.

## [0.15.1] - 2026-07-06

- **Fixed the "Log out" button drifting to the bottom of the page.** On a
  long page (e.g. the printer list with many rows), the sidebar stretched
  to match the page's full scrollable height instead of staying pinned to
  the viewport — the sidebar is now capped to the visible screen height,
  with just the main content scrolling underneath it.

## [0.15.0] - 2026-07-06

- **Fixed a false "update available" notice.** The Updates page compared
  versions with a plain inequality, so it reported an update whenever
  origin/main's version merely *differed* from what's running — including
  when origin was actually behind (commits made/deployed directly on this
  box, not yet pushed). It now only flags a real update when origin's
  version is genuinely newer.

## [0.14.0] - 2026-07-06

- **Per-printer, per-user page quotas.** Cap how many pages a user can
  print at a specific printer over a period you choose (daily/weekly/
  monthly/quarterly/yearly), configurable on each printer's own detail
  page. A user already at or over their limit gets their next job held
  instead of forwarded — release requires an admin (new "Quota Holds"
  admin page), not the submitter's own PIN. Off by default org-wide
  (Settings → Quotas) until you turn it on, even if printers already have
  limits configured.
- **LDAP address-book relay for copiers.** Lets office copiers do
  scan-to-email address-book lookups against PrintOps over LDAP instead of
  each one holding its own direct connection to Google Workspace — served
  entirely from the Google Workspace roster PrintOps already syncs, no
  live Google call per search. New `infra/ldap-relay/` service (its own
  process), Settings → LDAP Relay for the org-wide switch/base DN, and a
  per-printer bind-credential panel. Off by default.

## [0.13.0] - 2026-07-06

- **Insights is now the landing page.** Signing in (Google SSO or the local
  admin account) goes straight to Print Insights instead of the printer
  list, and it's the first link in the nav.
- **Redesigned Insights filters.** Moved from a fixed left sidebar into a
  collapsible bar at the top of the page, so charts and tables get the
  full page width instead of sharing it with a filter column.
- **Print Summary actually looks like a report now.** The left nav and
  filter panel no longer leak into the printed output; a compact one-line
  filter summary and a PrintOps-branded header (logo + generated
  timestamp) replace them, and charts render at full width instead of
  the narrow on-screen size they were stuck at before.
- **Report Formulas moved to Settings → Insights**, out of the bottom of
  the Insights report page itself — the report page now only shows
  report content, not admin configuration.

## [0.12.0] - 2026-07-06

- **Consolidated Settings section.** User accounts, attribution aliases, and
  global SNMP defaults now live under one `/settings` area with tabbed
  navigation instead of being scattered across the Devices page and a
  standalone Users page.
- **Pagination and search for Users and Attribution Aliases.** Both list
  endpoints now page results (50 per page) and support a `search` filter
  (name/email, or alias/resolved-email), so these lists stay usable as the
  roster and alias table grow.

## [0.11.0] - 2026-07-05

- **Kyocera, Ricoh, and Xerox copier support.** These three join Canon
  and Konica Minolta as real connectors with setup guidance for their
  actual device features (Kyocera Job Accounting/User Login, Ricoh User
  Code Authentication, Xerox Standard Accounting) — not just generic CSV
  import. Same honesty as the others: per-user accounting retrieval and
  remote provisioning aren't available over a network API for any of
  these, so CSV import (from each device's own admin page) remains how
  usage data comes in.

## [0.10.0] - 2026-07-05

- **Placeholder connectors for Lexmark, HP, Ricoh, Kyocera, Sharp, and
  Xerox copiers.** These are now selectable when adding an MFP device,
  with honest, vendor-specific setup notes about what's actually
  supported today (SNMP page totals and CSV import) versus what isn't
  (per-user accounting retrieval and remote provisioning, none of which
  have a confirmed network API for any of these six yet). Meant to make
  it clear these are on the roadmap without pretending they already work
  more than they do.

## [0.9.0] - 2026-07-05

- **Konica Minolta bizhub support.** Devices using Konica's Account Track
  or User Authentication get real meter reads (reusing PrintOps's
  already-verified Konica SNMP logic) and setup guidance for enabling it
  on the device. Same as Canon: per-user accounting retrieval and remote
  provisioning aren't available over a network API, so the connector says
  so plainly, and CSV import (from the device's own PageScope Web
  Connection admin page) is the way to bring that data in.

## [0.8.0] - 2026-07-05

- **Walk-up copier accounting.** PrintOps can now track copies made
  directly at a shared copier — not just print jobs it proxies — and
  attribute them back to the same staff member. Admins register MFP
  devices, map staff to their copier login (staff ID, PIN, badge, or
  vendor code), and bring in usage data via a CSV import wizard (upload,
  map columns, preview, commit) or SNMP meter reads. Unresolved logins
  show up on a dedicated screen where mapping one immediately re-processes
  every past record that used it. Print Insights now shows combined
  print + copy totals per staff member alongside the existing print-only
  numbers.
- **Canon Department ID Management support.** Devices using Canon's
  Department ID Management get real meter reads (reusing PrintOps's
  already-verified Canon SNMP logic) and setup guidance for enabling it
  on the device. Per-user accounting retrieval and remote provisioning
  aren't available over Canon's own API — the connector says so plainly
  rather than pretending, and CSV import remains the way to bring that
  data in.
- **Merge duplicate staff identities for print attribution.** If a
  computer reports a bare local username (e.g. "matt") instead of a real
  email, or someone's address changed, an admin can now merge it to the
  correct staff member from the Devices page — instantly correcting every
  past job that used it, not just future ones. Google Workspace's own
  account aliases (created automatically when an address changes) merge
  in the same way with no manual step. A new opt-in setting can also
  mirror each staff member's Employee ID into a copier login
  automatically.
- **Fix:** a device reporting its status message as multiple values
  instead of one was crashing the background status check for that
  printer every minute.

## [0.7.0] - 2026-07-04

- **Failed jobs are cleaned up automatically.** A job that ends in
  "failed" now gets deleted 48 hours after it failed, instead of sitting
  in the Jobs list forever. Also closes a related gap: a failed
  print-release attempt was leaving its spooled document behind
  indefinitely — that file is now cleaned up too. Note this trades some
  historical accuracy in Print Insights' failure counts for date ranges
  older than 48 hours, in exchange for not growing the jobs table
  unboundedly.

## [0.6.0] - 2026-07-04

- **A jammed print job no longer blocks the rest of the queue.** Every
  printer queue now cancels a failing job automatically instead of
  retrying it forever in place — CUPS's default behavior kept retrying
  the same stuck job, which meant everyone else's jobs sent to that
  printer piled up behind it until an admin noticed and manually
  intervened. The failed job is still recorded with its error on the Jobs
  page as before; it just no longer holds up the printer for anyone else.

## [0.5.0] - 2026-07-04

- **Automatic printer rediscovery on reconnect.** When a printer that was
  offline/erroring comes back online, PrintOps now automatically re-probes
  its IPP capabilities too, not just its reachability — the same probe the
  manual "Rediscover" button runs. Covers a printer that gets physically
  swapped, or gains/loses a module (finisher, extra tray), while it's down
  for maintenance, without an admin needing to remember to click
  Rediscover afterward.
- **More resilient CUPS queue sync.** `-m everywhere`'s full attribute
  probe can hang or get refused outright by some devices (confirmed on a
  Kyocera ECOSYS) even though they answer PrintOps's own smaller IPP
  probes fine. The sync scripts now bound that call to 30s and fall back
  to a generic driverless PPD (reduced capability accuracy for that queue,
  but it becomes usable instead of stuck unsynced), and explicitly enable/
  accept the queue afterward since the fallback can otherwise leave it
  disabled by default.
- **Fix:** a printer legitimately deleted while its queue sync was still
  in flight (now up to ~90s worst case with the new fallback) could 500
  the request instead of a clean no-op.
- **Fix:** the printers list is now horizontally scrollable instead of
  squeezing/clipping columns on narrower screens.
- **Fix:** printers requiring IPP/1.1 (confirmed on an HP LaserJet 4250)
  failed to add at all — probes now retry at 1.1 when a device rejects
  2.0's version.
- **Fix:** a printer reporting a multi-value firmware string (confirmed
  on a Lexmark XM3350 and Kyocera ECOSYS) could 500 the entire printers
  list, not just that one printer.

## [0.4.0] - 2026-07-03

- **SNMP page/copy/print counter polling.** Printers are now polled over
  SNMP for their real lifetime page counts, independent of anything
  PrintOps sees as a digital print job — the standard total works on
  every vendor, with a verified copy-vs-print breakdown for Canon and a
  best-effort breakdown for Konica Minolta (other vendors show total
  only until confirmed against real hardware). Configurable per-printer
  or globally (community string, version, port), off by default until an
  admin opts in.
- **Per-printer usage history chart.** A new "Usage Over Time" card on
  each printer's detail page graphs daily page/copy/print deltas
  computed from the SNMP counter history, with a 7/30/90/180-day range
  selector — kept per-printer rather than added to the shared Insights
  dashboard, which isn't built to scale across a large fleet with
  separate per-printer values. History is retained for a configurable
  window (default 180 days) and pruned automatically.

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
