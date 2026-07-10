# Changelog

All notable changes to PrintOps are documented here. Each entry is keyed by
the version in the root `VERSION` file — the in-app Updates page extracts a
version's section from this file to show "what's new" before an admin
schedules an update.

## [0.31.0] - 2026-07-10

- **New: Live Dashboard**, now the default landing page and top nav item,
  showing today's print activity — total jobs/pages/color/duplex tiles, an
  hourly bar chart of pages printed so far today, and a recent-jobs feed
  (user, printer, pages, color/mono, duplex/simplex, size) — refreshing on
  its own every 15 seconds with no manual reload, meant to be left up on a
  TV display. Reads as all-zero (empty chart, "no jobs yet" panel) rather
  than erroring when there's genuinely no activity yet. Deliberately built
  on plain polling rather than a WebSocket/SSE push channel: this app has
  zero server-push infrastructure today, and a wall-mounted dashboard
  doesn't need sub-second latency the way a live ticker would. Hour
  buckets are computed from a caller-supplied start/end window (the
  viewer's own local midnight, computed client-side) rather than the
  server's UTC "today," so the bars line up with the actual wall clock in
  the room regardless of the server's own timezone.

## [0.30.0] - 2026-07-10

- **New: idle-based session timeout, admin-adjustable, with a per-user
  "no timeout" exemption.** Previously every session (Google SSO or the
  local admin login) expired on a flat 60-minute timer from login,
  activity or not. Sessions are still plain stateless JWTs — no new
  server-side session store — but the browser now calls a new
  `POST /auth/refresh` endpoint every couple of minutes, only while
  there's been real mouse/keyboard/touch activity, reissuing the token
  with a renewed expiry. Stop using the tab and the last-issued token's
  own expiry simply lapses, triggering the existing expired-session
  redirect — no new "last seen" tracking needed. The timeout duration is
  now admin-configurable (Settings → Session Timeout, default 60
  minutes), and a specific account (e.g. a shared front-desk login) can
  be flagged "No timeout" on Settings → Users — checked fresh from the
  database on every refresh, so revoking it takes effect on that user's
  very next refresh rather than waiting for their token to expire.

## [0.29.0] - 2026-07-10

- **New: "Detect via SNMP" on each printer's Toner Cartridges card.**
  Reads the standard Printer MIB's supplies table (RFC 3805 — not a
  vendor-private MIB like this app's page-counter breakdowns) and parses
  each cartridge's device-reported description for a color and a
  high-capacity ("XL"/"High Yield") hint, so cost calculations can
  eventually account for cheaper-per-page high-capacity cartridges. The
  color/high-capacity read is explicitly best-effort — surfaced next to
  the raw description string so it can be checked against the physical
  cartridge — since it hasn't been verified against this district's full
  fleet the way the existing SNMP counter code was before being trusted.
  Also fixed a latent bug this surfaced: saving cost/yield via the
  existing cartridge form fully deletes and recreates every row, which
  would have silently wiped a detection result on the next manual edit;
  detected fields now carry across that replace.

## [0.28.0] - 2026-07-10

- **New: Archive a printer** instead of deleting it, for when a physical
  printer/copier is being swapped out but its job history needs to stay
  intact. Deleting a printer cascades and deletes every Job row for it —
  archiving instead tears down its CUPS queue (so it stops accepting new
  jobs and drops off AirPrint discovery) while leaving the printer row and
  all its historical jobs untouched. Archived printers are excluded from
  the background status/SNMP poll loops and hidden from the default
  printer list (with a "Show archived" toggle), but stay fully visible in
  Jobs/Usage/Syslog/Insights for historical reporting. Reversible via an
  "Unarchive" button, which re-syncs the CUPS queue.

## [0.27.0] - 2026-07-10

- **Printer detail page reorganized into tabs** (Overview, Connection,
  Release & Quotas, Toner, Syslog, Credentials, Jobs) to cut down the
  scrolling on a page that had accumulated a card per feature over many
  releases. The tab bar is horizontal and sticky (stays visible while
  scrolling a tab's content) rather than the vertical sidebar Settings
  uses, per request. Fixed a real rendering bug along the way: a flex
  `gap` sitting directly against a `sticky` element is a known Safari/iOS
  glitch where scrolled-past content briefly shows through the gap before
  the sticky element's background repaints — fixed with explicit margins
  instead of `gap`, and applied to Settings' own (now also sticky) side
  nav preemptively so it doesn't hit the same bug later.
- **Centered page content app-wide and widened the data-dense list
  pages.** Every page's content column was left-aligned inside its full-
  width container, so on a wide monitor a narrow page (e.g. printer
  detail) left most of the screen blank on one side rather than
  distributing it evenly. All 25 top-level pages are now centered; the
  eight table/list-heavy pages (Printers, MFP Devices, Devices, Copier
  Unmapped, Quota Holds, Copier Imports, Staff Copier Identities,
  Settings) were also widened to match Jobs/Usage/Syslog's existing
  width, since a table benefits from extra width far more than a form
  does.

## [0.26.0] - 2026-07-10

- **Usage page: Duplex/Simplex, Mono/Color, and real per-user cost
  columns, plus pagination and a domain-suffix search.** Cost is the same
  real per-printer-toner-rate calculation Insights' cost-breakdown report
  already uses, not a flat estimate — reused via a small extracted
  `app/reports/cost_rates.py` module instead of duplicating the
  computation. The Size (bytes) column was dropped as low-value for an
  aggregate across many jobs. The user list is now server-paginated
  (50/page) instead of loading the full roster at once, and the search
  box accepts a leading `*` for a domain-suffix filter (e.g.
  `*example.org`) to separate staff from students by domain in one
  district's real roster of 3,500+ synced accounts.
- **Clicking a user on the Usage page** now opens a per-user detail page
  (stats panel — jobs, pages, duplex/simplex, mono/color, estimated cost
  — plus their full print job history with which printer each job went
  to) instead of just showing their row in the aggregate table.
- **Devices page: pagination**, for the same reason as Usage — one real
  district's Google Workspace sync has 2,000+ Chromebooks, all previously
  loaded and rendered in a single unpaginated table.

## [0.25.0] - 2026-07-10

- **New: syslog collection from printers.** Printers/MFPs that support
  exporting their own event log via syslog (most do, over UDP, usually
  configured on the device's own admin page) can now have those messages
  captured and shown per-device and fleet-wide (new Syslog page), useful
  for diagnosing a jam or an offline printer beyond what SNMP counters or
  IPP status already show. Collection runs as its own small systemd
  service (`infra/syslog-relay`, mirroring the existing LDAP relay's
  "separate process for a privileged port" pattern — UDP 514 needs
  `CAP_NET_BIND_SERVICE`) that parses RFC 3164/5424 messages and batches
  them into printops-api rather than one HTTP call per UDP packet. Off by
  default; a configurable severity floor and retention period keep chatty
  device firmware from filling the database. Unmatched-source events
  (from a device not yet registered as a Printer) are kept visible rather
  than dropped, so a misconfigured target IP is easy to spot.

## [0.24.1] - 2026-07-09

- **Fixed Untracked Copy Activity showing zero on the day it's enabled**,
  even with clear SNMP counter growth all day. The report computes its
  window as `max(filters.start, enabled_at)`, so on the enablement day
  itself the boundary reading it needs (a reading strictly before the
  window, but not before `enabled_at`) is impossible to find no matter
  what data exists — not a real gap in polling, an empty query range by
  construction — so the whole day's activity was silently dropped
  instead of just the pre-enablement portion. Confirmed live: this
  recovered 2,234 measured copies and 104 estimated pages for today that
  weren't showing.

## [0.24.0] - 2026-07-09

- **New: Toner Cartridge Model field on each printer's Toner Cartridges
  card.** Reference-only (e.g. "TN-227") so an admin can look up which
  cartridge to order without hunting through a spreadsheet — PrintOps
  doesn't use it for anything itself. Saved alongside the existing
  per-color cost/yield rows in the same card.

## [0.23.1] - 2026-07-09

- **Fixed the PrintOps logo missing from the printed/exported Insights
  report.** The report header sits in a print-only block (hidden on
  screen, shown only via the browser's print media query), and Next.js's
  image component defers loading anything not currently visible — since
  the logo was never visible on screen, it never finished loading before
  `window.print()` fired. Marked as a priority image so it loads
  immediately regardless of that hidden state.

## [0.23.0] - 2026-07-09

- **Jobs list now shows the document name, and Size is actually populated.**
  The document name (already captured on every job) was never displayed.
  Size was worse than undisplayed — it was almost always null: the CUPS
  backend only measured a job's size when CUPS handed it a filename
  directly; when CUPS instead piped the document over stdin (the common
  case for filtered documents), size was never captured at all. The
  backend now proxies stdin through to the real printer backend itself,
  counting bytes as they stream past, so Size is populated for that path
  too — both immediately-forwarded jobs and ones held for release/quota.

## [0.22.0] - 2026-07-09

- **New: iPad AirPrint MDM Profile panel on each printer's detail page.**
  iPadOS can't use the same "paste one IPP URI" queue setup as macOS — it
  needs an AirPrint payload (Host, Resource Path, Port, Force TLS) pushed
  via an MDM profile (in Mosyle: Devices → Printer Management → Add
  AirPrint). This panel shows exactly those four values, pre-filled from
  the printer's own already-configured connection info, with a copy
  button per field, so an admin can push a working iPad printer profile
  without hand-deriving the resource path or guessing the TLS setting.

## [0.21.0] - 2026-07-09

- **New: device-level print tracking.** Staff and students often use the
  same account on both a MacBook and an iPad. Jobs now show which device
  submitted them (resolved from the device's MDM roster name, falling
  back to its raw MAC address), and the Insights "Leaderboard & Cost"
  panel gains a "Devices" toggle alongside Printers/Users so cost and
  volume can be broken out per device, not just per person.

## [0.20.0] - 2026-07-09

- **New: reference-only web login and scan-to-email credentials per
  printer.** A new "Reference Credentials" section on each printer's
  detail page stores its own web admin UI login (username is optional —
  some printers only prompt for a password) and scan-to-email setup (the
  "from" address plus its scan password). PrintOps never uses these to
  log into or configure anything itself — it's just secure storage so an
  admin can look a password up later instead of hunting through a
  spreadsheet. Passwords are encrypted at rest and only ever shown in
  plaintext to an admin viewing that specific printer's own page — never
  on the printer list, and never to a Viewer role at all.

## [0.19.1] - 2026-07-09

- **Untracked Copy Activity now lists each contributing copier
  individually**, not just the org-wide total — sorted largest first,
  showing both its Unattributed Copies and Estimated Untracked Activity.
  A printer that contributes nothing (no copy-capable SNMP data, or zero
  activity in range) doesn't show up as a noisy zero row.

## [0.19.0] - 2026-07-09

- **New: Untracked Copy Activity on Insights.** Estimates walk-up copy
  activity PrintOps otherwise has no visibility into (no badge/PIN
  accounting set up), using each printer's own SNMP counters. For
  printers with a real, vendor-broken-out copy counter (Canon, some
  Konica Minolta), this is a direct measurement — "Unattributed Copies."
  For printers with only a combined total counter, it's an estimate —
  total counter growth minus pages PrintOps actually printed there
  ("Estimated Untracked Activity"), sound specifically because PrintOps
  is the only print path in this architecture. Never attributed to a
  person, never double-counted against a printer already tracked via
  walk-up copier accounting, and never backfilled — only counts from the
  moment it's turned on (off by default, Settings → Insights), not
  retroactively against existing SNMP history.

## [0.18.3] - 2026-07-09

- **Split Insights' "Failed / cancelled" stat tile into two.** It showed
  both counts jammed into one value (e.g. "0 / 2"), reading like a
  fraction rather than two independent counts — now separate "Failed
  jobs" and "Cancelled jobs" tiles, matching every other stat in that row.

## [0.18.2] - 2026-07-09

- **Fixed Mono/Color/Paper cost on Insights' Environmental & Cost Impact
  section.** These three were squeezed into a fine-print sentence below
  the stat tiles instead of getting their own tiles like Sheets of Paper,
  Duplex Sheets Saved, Trees, and CO₂ — now consistent with the rest of
  that section.

## [0.18.1] - 2026-07-09

- **The Insights "Leaderboard & Cost" panel (Users view) now shows names
  instead of email addresses**, same roster-name-with-local-part-fallback
  resolution just added to the Combined Leaderboard, reused here for the
  per-user cost breakdown.

## [0.18.0] - 2026-07-09

- **Combined Leaderboard now shows names, a duplex/color breakdown, and
  estimated cost.** The Insights page's Combined Leaderboard listed raw
  email addresses — it now shows the person's synced Google Workspace
  name, falling back to the email's local part (e.g. "jane.smith") for
  anyone not in the roster yet, such as before an attribution alias is
  merged. Also added Duplex/Simplex and Color/Mono page breakdowns per
  person, and an estimated print cost using the same real per-printer
  toner-rate formula the Cost Breakdown report already uses (print-only —
  walk-up copy usage has no cost model yet).

## [0.17.1] - 2026-07-09

- **Print Release's default hold expiry is now 48 hours, up from 4.**
  4 hours was too tight in practice — an unreleased held job is
  cancelled and its spooled file deleted once this window passes.
  Existing installs keep whatever they already have configured; this
  only changes the starting default for a fresh setup.

## [0.17.0] - 2026-07-09

- **Print Release bypass for specific staff, per printer.** When Print
  Release is on for a printer, an admin can now name individual staff
  (e.g. someone who sits right next to that copier) whose jobs print
  immediately instead of being held for kiosk release — everyone else
  at that printer still releases their own jobs normally. Configured on
  the printer's own detail page, in the same section as the release
  toggle and kiosk link. A bypassed user's job still goes through
  ordinary page-quota holds if those are enabled — the bypass only skips
  the release-required hold specifically, not every hold.

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
