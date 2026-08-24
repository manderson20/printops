# Changelog

All notable changes to PrintOps are documented here. Each entry is keyed by
the version in the root `VERSION` file — the in-app Updates page extracts a
version's section from this file to show "what's new" before an admin
schedules an update.

## [0.67.0] - 2026-08-24

- **You can now see which printers speak AirPrint.** A printer with AirPrint on
  can be added directly by anyone on its network, printing around PrintOps
  entirely — no accounting, no quotas, no held-job release. Open a printer's
  row on the Printers page and look at **Printer AirPrint**, or export the
  printer list to CSV to work through the whole fleet at once.
- A printer that doesn't answer the question reads **Not reported**, never
  "Off". Some older printers advertise AirPrint over Bonjour without saying so
  over IPP, and telling you a machine is closed when it is open would be the
  worst way for this to be wrong.
- The old **AirPrint** column is renamed **Queue discovery**. It always
  referred to whether PrintOps' own queue is discoverable, which is a
  different question from what the printer itself does.
- Detection is read-only: PrintOps asks the printer three extra questions
  during the capability check it already performs. Nothing is changed on any
  device and printing is unaffected.

## [0.66.0] - 2026-08-24

- **One list of accounts instead of two.** The copier tab showed the same
  people twice — once from PrintOps' record and once read from the machine —
  under two different numbers, since a person's slot (268) and their copier
  code (26818) are not the same thing. There is now a single list, and
  **Check copier** reads the machine and marks anything the two disagree
  about: an account PrintOps expects that isn't there, or one on the copier
  that PrintOps didn't put there.
- **You can search the staff accounts on a copier**, instead of scrolling a
  list of nearly 300. Search matches a person's name, their username or their
  copier code, and says plainly when nobody matches — so "is this person set up
  here?" is a question you can answer in a second.
- **The list is now ordered by surname**, the way a staff roster is, and shown
  that way too — "Woodard, Madison" — so the order explains itself. The copier
  returns accounts in its own order, which is not one anyone can scan by eye.
- **Each account now shows the person's name**, not just their username. A
  copier only stores the username — `mwoodard` — so looking for "Madison
  Woodard" in that list failed even when her account was there. That is exactly
  how someone who *is* set up gets reported as missing.

## [0.65.0] - 2026-08-24

- **Held jobs are now part of Jobs.** The separate **Held Jobs** menu item is
  gone. A held job is an ordinary job that hasn't printed yet, so it now sits
  in the Jobs list with everything else — choose **Held** in the status filter
  to see just those. Each one shows why it is waiting: over quota, waiting to
  be released at the printer, Follow-Me, or waiting for a printer that is
  switched off.
- **Release** is on the job's own row, in the Actions column, exactly as it was
  before. Follow-Me jobs still ask which printer to release at.
- **Discard** is new. It throws a held job away without printing it, for the
  duplicate sent three times or the document sent to the wrong printer that
  nobody is coming back for. It deletes the document and cannot be undone, so
  it asks first and names the document and who sent it. **The job itself stays
  in the history and in the reports** — what is deleted is the document, not
  the record of it.
- Old **Held Jobs** links and bookmarks now open the Jobs list filtered to
  held, so nothing anyone wrote down stops working.

## [0.64.2] - 2026-08-24

- **Grey pages are no longer counted, or charged, as colour.** A colour job
  sent to a black-and-white printer prints in grey, but the print server
  reports the colour the job *asked* for — so Insights counted those pages as
  colour and the cost formula billed them at the colour rate. Jobs on a printer
  known to be black-and-white are now recorded as monochrome, and the 18 jobs
  already stored that way have been corrected.
- A printer whose capabilities have never been detected is left alone, so a
  colour printer is never wrongly recorded as black-and-white.

## [0.64.1] - 2026-08-24

- **IP address is back on the printer's row** in the Printers list, rather than
  inside the opened row — it is looked up across the whole list, not once a
  printer has been picked.

## [0.64.0] - 2026-08-24

- **The Printers list no longer scrolls sideways.** Ten columns did not fit on
  screen, which pushed the buttons off the right-hand edge — reaching **Test
  Print** meant scrolling to find it. Each row now shows what you actually scan
  down the list for: name, status, location and page count. Click the arrow at
  the start of a row to open everything else underneath it — model, IP address,
  queue, AirPrint, the full capability list, and the Test Print button.
- Warnings stay visible without opening anything. **Jobs waiting**, **Network**
  and **Sync Failed** all show on the collapsed row, since a warning you have
  to expand a row to find is a warning nobody sees.
- The capability list is no longer cut off at four with a "+4 more" link; the
  opened row has room to show all of them.

## [0.63.0] - 2026-08-24

- **A copier is now part of its printer.** Copiers had their own top-level
  menu, which meant every machine that both prints and copies was described
  twice — the same address, model, location and meter, kept in two places that
  could disagree. There is now one page per machine: open the printer and use
  the new **Copier** tab. The first thing on it is a switch. Leave it off and
  the machine is simply a printer; turn it on and the full copier settings
  appear underneath — staff accounts and codes, copy counters, owner
  attribution and recent copier usage, exactly as they were.
- Turning the switch off only stops the tracking. Nothing is deleted: the
  provisioned accounts, counter readings and usage history stay with the
  machine and come back if it is turned on again.
- The old Copiers pages now send you to the machine's own page, so existing
  links and bookmarks keep working.
- **Print Test Page** is now a button on the printer's own settings page.

## [0.62.0] - 2026-08-23

- **Jobs sent to a printer that is switched off now wait for it.** The print
  server queued them by itself, but only for three hours, after which it
  cancelled them outright — so a job sent to a classroom printer at 5pm was
  destroyed at 8pm, with nothing left in the queue by morning and no notice to
  the person who sent it. Observed happening to a real job on 2026-08-23.
  PrintOps now holds those jobs instead: they wait as long as they need to,
  they appear on the **Held Jobs** page marked "Waiting for printer", and they
  are sent automatically, oldest first, the moment the printer answers again.
  Nobody needs to do anything. The print server's own limit has also been
  raised from 3 hours to 72, so a job already queued survives a weekend.

## [0.61.0] - 2026-08-23

- **Reports now read in the district's own time, not UTC.** Print times have
  always been recorded in UTC — that is correct and unchanged — but the reports
  were also being *read* that way, and nothing about it looked wrong. The
  busiest-hour chart was five or six hours out, so a mid-morning rush showed up
  as an afternoon one. Anything printed after 7pm was counted toward the next
  day, and toward the next weekday, so every evening's printing quietly moved
  into the following day's total. Exported spreadsheets carried UTC timestamps
  that no one could reconcile with when they were at work. All of that now
  follows the district's time zone, set under **Settings → Server** and
  defaulting to America/Chicago. Daylight saving is handled properly, so the
  numbers stay right on both sides of the changeover. The Jobs page was already
  showing local time and is unaffected.

## [0.60.1] - 2026-08-23

- **Fixed: the new "hard to reach" warning could not see the printer it was
  written for.** PrintOps checks each printer by opening a connection to it,
  and a connection survives a brief network outage by retrying — so on the
  printer this warning was built for, almost every check succeeded a second or
  two late and was recorded as perfectly healthy, while a fifth of the traffic
  to it was being dropped. PrintOps now also notices how *long* each check
  took: one that answers only after retrying is counted as evidence too. A
  printer that is simply slow is not affected — the comparison is against that
  same printer's own quick answers, not a fixed number.
- **Fixed: one print job could be reported as dozens of jobs, and dozens of
  failures.** When the print server retries a job it starts delivery again from
  the beginning, and PrintOps recorded each attempt as a separate job — every
  one of them a failure. One teacher's job in August was retried 51 times and
  appeared in the reports as 51 failed jobs. Attempts at the same job are now
  recognised as one job: the reports count it once, and the failure count means
  what it says. Every attempt is still listed on the Jobs page, where seeing
  that a job took four tries is the point. Jobs that actually printed are never
  altered, and attempts are matched using the print server's own permanent job
  identifier, so a job number that comes round again after the print queue is
  cleared cannot be mistaken for a retry of an older job.

## [0.60.0] - 2026-08-23

- **New: PrintOps now warns when a printer is hard to reach.** Every health
  signal PrintOps has comes from the printer itself, so a printer that answers
  whenever it is reached looks healthy — even when a large share of what is
  sent to it never arrives. That is what a bad switch port, a damaged cable or
  a reused wall port looks like, and it was invisible: the printer reports
  itself fine, because it is fine. A printer that misses several status checks
  in an hour while still answering in between is now marked with an amber
  "Network" badge on the printer list, and its page says what was seen and
  suggests checking the switch port and cable. It is deliberately not shown as
  an error — the printer is working, and it is the path to it that isn't.
  Found on the LCACTC RM 502 after it was moved to a port that hadn't been used
  in a long time. The recent history behind the warning is kept with the
  printer, so updating PrintOps doesn't wipe it and give a struggling printer a
  clean slate.
- **Fixed: a test page could be held with nothing saying so.** A test page is
  sent through the printer's normal queue on purpose, so that it proves the
  same path a real job takes — which means a printer set to hold jobs for
  release holds the test page too. PrintOps reported the submission as a
  success, so an admin would stand at a printer waiting for a page that was
  never going to come out. It now says the page is being held and what will
  release it.
- **New: admins can release any held job, not just quota holds.** Quota Holds
  has become **Held Jobs** and lists everything PrintOps is holding, with a
  badge for why. Previously only quota holds could be released from here, and
  everything else relied on the person who sent the job releasing it at the
  printer with their PIN — which cannot work for anyone who has no PIN yet, or
  for a job sent by the server itself. A Follow-Me job asks which printer to
  release it at, since it was never addressed to a particular one.

## [0.59.8] - 2026-08-23

- **Fixed: a printer that missed one check could have its queue restarted
  anyway.** 0.59.6 stopped PrintOps calling a printer offline on a single
  missed check, which also meant the printer kept reading "online" for that
  one cycle. Two things read that status as "what the check just found": the
  repair that restarts a stopped print queue, and the detector that watches
  for a queue that has stopped moving. So a printer that had just failed to
  answer could have its queue restarted on the strength of a status a minute
  out of date — handing the print server work for a machine it could not
  reach, when the jobs were safely held. Both now use the result of the check
  actually made. This fix was written alongside 0.59.6 but missed that
  release.

## [0.59.7] - 2026-08-23

- **Fixed: a queue restart that failed was reported as a success.** When
  PrintOps finds a printer's queue switched off and restarts it, the restart
  is carried out by a small script on the server. If the server refused the
  command, the script reported success anyway — so PrintOps told the admin the
  queue was running again, cleared the printer's warning, and stopped
  checking, while jobs kept piling up behind a queue that had never started.
  That is the same silent failure the queue restart was built to end, one step
  further up. A refused restart is now reported as a failure, with the
  server's own reason attached, and the printer keeps its warning until the
  queue really is running.
- **Fixed: some printers couldn't be added by IP alone.** A printer that
  answers on the expected port but serves printing from a different address
  path says so when PrintOps first contacts it. PrintOps was treating that
  reply as "the address I already tried" and giving up, so the printer looked
  unreachable even though it had just said exactly where to find it. Those
  printers are now followed to the address they name and added normally.
- **Clearer error when a printer's port is mistyped.** Entering something that
  isn't a number in the Port field failed the entire save — including every
  other change on the form — and reported only "Failed to save changes". It
  now says the port must be a whole number between 1 and 65535, and leaves
  the rest of the edits in place.

## [0.59.6] - 2026-08-23

- **A printer is no longer called offline on one missed check.** PrintOps
  checks every printer once a minute, and a single check that didn't come back
  was enough to show the printer as offline. On a network that drops the odd
  packet that happens to a perfectly healthy printer, which then reads as
  offline for a minute and comes back on the next check — and every one of
  those round trips made PrintOps re-examine the printer and rebuild its print
  queue, for nothing. A printer now has to miss two checks in a row before it
  is reported offline. One that has genuinely been switched off or unplugged
  still shows up as offline within two minutes, and a printer that answers
  when asked a second time is left alone. This was found on the LCACTC RM 502,
  which loses about a fifth of the traffic sent to it in short bursts while
  reporting no fault of its own — that underlying network problem is still
  open, and is being followed up separately.
- **Held documents are stored less permissively.** A print-and-release job's
  document waits on the server until someone releases it at the printer. It
  was being written in a way that let anything running as the print system
  modify it, where only reading it back is ever needed. It is now written
  read-only to that group. No change to how releasing a job works.

## [0.59.5] - 2026-08-23

- **Fixed: filtering Insights to one printer only filtered half the page.**
  Picking a printer (or a department) narrowed every printing number on the
  report, but left every walk-up copying number exactly as it was — the whole
  district's. The Combined Leaderboard was the clearest place to see it: it
  ranked people by their printing on the one printer plus their copying
  everywhere, so someone who had never used that machine still appeared on
  its report, and everyone's totals were too high. Both filters now apply to
  copying as well, through the link between a copier and the printer it also
  is in PrintOps. A printer with no copier attached to it now reports no
  copies, which is the right answer for an ordinary desktop printer.
- **Fixed: a job could be written off when the print server was simply unable
  to answer for it.** When PrintOps checks what became of a job whose delivery
  never reported back, it asks the print server directly. It treated every
  kind of unhelpful reply as "no record of this job" — including replies that
  mean the server declined to answer, such as a permissions error or a
  momentary hiccup — and a job with no record is eventually recorded as
  "outcome unknown" once it is a couple of hours old. So a job that was
  printing perfectly well could lose its history because the question came at
  a bad moment. PrintOps now tells the two apart: only a genuine "I have no
  record of that job" counts, and anything else is asked again on the next
  check.

## [0.59.4] - 2026-08-23

- **Fixed: jobs that stayed "printing" forever.** A job's record was written
  the moment it started printing and only completed when the print server
  reported back — and there were several ordinary ways it never did. CUPS
  signals the delivery process whenever it cancels, holds or restarts a job,
  and restarts happen on their own whenever a printer's queue is rebuilt,
  which PrintOps itself does routinely. The record was then left saying the
  job was still printing, for good. 107 of them had built up since July —
  around one job in forty. They sat on the Jobs page looking like work in
  progress, they were left out of every report, and where CUPS had restarted a
  job the pages it eventually printed were counted against a different record,
  so people's totals came out short. PrintOps now closes these out: the print
  server is asked what actually became of each one, and jobs it can account
  for are recorded as printed — with their page counts — cancelled, or failed,
  as the case may be. The 107 existing ones have been resolved too.
- **A job PrintOps is unsure about is never guessed at.** One still printing
  is left alone however long it has been going — a large job from graphic arts
  genuinely takes hours. One the print server no longer remembers, which is
  most of the older backlog, is recorded as cancelled with "outcome unknown"
  written on it rather than as a failure nobody observed, so the failure count
  on the reports stays a number worth acting on.
- **Fixed: a printer could stay switched off for hours after it came back.**
  The queue restart added in 0.59.3 waits longer between attempts each time
  one doesn't hold, up to four hours, so that a printer that is genuinely sick
  isn't fed a job a minute. But the wait was a plain clock: a printer that had
  been away long enough to run it up to four hours would then sit there, red
  and not printing, for up to four more hours after being wheeled back and
  plugged in — with its users' jobs waiting behind it. PrintOps now watches for
  the printer coming back: once it has been answering normally for five
  minutes, the wait is dropped and the queue starts on the next check. A
  printer that never went away keeps its backoff, which is the case it was
  written for. While a printer is away its queue is deliberately left off and
  still accepting, so jobs sent to it queue up and print when it returns
  rather than failing one at a time.

## [0.59.3] - 2026-08-23

- **Fixed: a printer that was taken away for service never printed again
  after it came back.** When a job fails badly enough mid-delivery — the
  usual cause being that someone unplugged, switched off or wheeled away the
  printer — CUPS switches that printer's queue off on the print server.
  Nothing switched it back on. The queue kept *accepting* jobs the whole
  time, so staff saw no error, work piled up behind it, and the only fix was
  someone running a command on the server by hand. The ES elementary copier
  spent 31 hours like this after a service call, with 19 teachers' jobs
  waiting, while PrintOps and the copier's own panel both said it was fine —
  because the copier genuinely was fine. PrintOps now notices a switched-off
  queue and switches it back on as soon as the printer is answering
  normally, on the next status check. Both the 60-second background check
  and the "Check Status" button do it, so a printer walked back online can
  be fixed on the spot instead of waiting for the next cycle.
- **Fixed: the wrong advice when a queue had stopped moving.** A printer in
  this state was reported as "queue has not moved... check the printer's
  connection settings (port/TLS/IPP path)" — the diagnosis from a different
  fault, and the wrong place to send someone whose printer had simply been
  away. It now says the queue was stopped, quotes the reason CUPS gave, and
  says whether PrintOps has already restarted it.
- **Fixed: PIN-release printers could fail silently in the same way.** The
  internal queue that delivers a job after someone releases it at the panel
  can be switched off by the identical failure, and when it is, releases fail
  with nothing to see. It is now checked and restarted alongside the main
  queue.
- A printer that is still offline or in error is deliberately left alone —
  restarting its queue would only feed CUPS one more job to fail. Repeated
  restarts that don't hold back off, up to four hours, so a genuinely sick
  printer isn't fed a job a minute.

## [0.59.2] - 2026-08-21

- **Fixed: toner levels were being read off some printers and then thrown
  away.** PrintOps worked out which cartridge was which colour by looking
  for the words "cyan", "magenta", "yellow" or "black" in whatever the
  printer called its supplies. Kyocera devices don't use those words — they
  report part numbers like `TK-8802C` — so every cartridge failed to match,
  and the levels the printer was reporting perfectly well were discarded.
  Setting the colours by hand in the printer's settings couldn't fix it,
  because the matching never consulted that setting. Part numbers ending in
  C, M, Y or K are now understood, so those printers report toner again.
- **New: the test page is now a printer identity sheet.** It used to be a
  logo and four lines on a mostly empty page. It now prints what PrintOps
  knows about the device — model, serial, address, firmware, location, queue
  name and status; which features it has (colour, duplex, collation, PIN
  printing, accounting, IPPS); its resolutions, media sizes, loaded trays,
  finishing options and formats; toner levels and page counters as they read
  at the moment you pressed the button. Walk to the printer, pick up the
  page, and everything you'd otherwise go back to a screen for is on it. A
  toner slot that has never been polled says so, rather than "not reported"
  — setting a cartridge's colour labels the slot, but only an SNMP poll
  fills in a level, and the two need different fixes.
- **New: the test page actually tests the print.** Alongside the details it
  carries colour patches, an eleven-step greyscale ramp, hairlines from one
  to four pixels, a 5-to-10 point type ladder and corner registration marks
  — plus a short checklist of what a good one looks like, so the targets
  mean something to whoever is holding the sheet. A printer that has never
  been discovered still gets a page; the parts PrintOps doesn't know are
  simply blank rather than blocking the print.
- **Fixed: the test page now prints in your own time, not UTC.** Every time
  PrintOps shows you is in the timezone of the computer you're reading it on
  — except the test page, which is composed on the server and carried the
  server's UTC clock. Comparing a freshly printed page against the job list
  meant mentally subtracting five hours. The test page now prints the time
  where you are, labelled with the zone you'd expect ("CDT", "CST"), and
  follows daylight saving on its own with nothing to set. It reads as a
  12-hour clock with AM/PM, and the date sits in the top-right of the
  header rather than the footer.

## [0.59.1] - 2026-08-21

A printer that PrintOps could not print to had been reporting itself online
for six hours. This release is the fixes that came out of finding out why.

- **Fixed: a printer that redirects its IPP port is now reported as broken,
  because it is.** The LCACTC Kyocera was switched to accept IPP only over
  TLS, so its normal port began answering with a redirect to the secure one.
  PrintOps' status check quietly followed that redirect and got a healthy
  answer back; CUPS, which actually delivers the jobs, does not follow
  redirects and failed every one. The dashboard read "online / Ready." the
  whole time. PrintOps no longer follows a redirect the printing path can't,
  and says which address the printer pointed at so the port/TLS/IPP path can
  be corrected.
- **Fixed: a cancelled job no longer leaves a process hammering the
  printer.** When CUPS cancelled or restarted a job, PrintOps' backend exited
  without stopping the delivery process it had started. That process kept its
  connection to the printer and kept retrying — several hundred times a second
  — and each cancelled job added another. Enough of them will take a printer
  off the network entirely.
- **Fixed: a resync no longer restarts jobs the printer just gave up on.**
  Modifying a queue makes CUPS restart everything on it. A job that stalled,
  got cancelled at its three-hour limit, and was then revived by the next
  automatic resync could repeat that cycle all day while nothing behind it
  printed. Queues with work waiting are now left alone until they drain.
- **Fixed: an unresponsive printer no longer leaves CUPS retrying it
  forever.** Building an accurate driver asks the printer for its full
  capability list. Some devices never answer that particular question, and the
  timeout only stopped PrintOps waiting — CUPS itself carried on asking,
  permanently. The printer is now asked once, cheaply, whether it can answer
  before the real request is made. Devices that can't get a generic driver
  instead, which is what already happened, only now without the retry storm.
- **New: a queue that stops moving is now noticed.** If jobs sit on a printer
  that claims to be reachable, PrintOps says so rather than waiting for
  someone to report that a print never arrived. Large jobs get proportionally
  longer before this triggers — a 26 MB Photoshop file is ordinary traffic in
  a graphic-arts lab and should not be mistaken for a fault.
- **New: adding a printer no longer means knowing its IPP path, port or
  scheme.** A device that has been switched to require TLS answers its old
  address with a redirect naming exactly where it now lives. PrintOps now
  reads that, checks the new address actually answers, and reconfigures the
  printer onto it — so a printer added with nothing but an IP sorts out its
  own port, TLS and path. It is adopted only after the new address responds:
  CUPS can't follow redirects, so taking one on trust would just move the
  printer somewhere that also doesn't print.
- **Fixed: Rediscover now rebuilds the print queue when it needs to.** It
  refreshes what PrintOps knows about a printer, and can now change the
  address that printer is reached at — but it left the CUPS queue pointing at
  the old one. The page reported success while the only part that actually
  prints was still misconfigured.
- **Changed: IPP Path now distinguishes "detected" from "set by you".** They
  were the same field, so once detection filled it in there was no way — in
  the interface or in the database — to tell a deliberate choice from a guess.
  The two need opposite handling: a choice must survive, a guess must be
  refreshable when the device changes. The box now shows the detected value as
  greyed placeholder text and anything you type in solid, and clearing it hands
  the printer back to detection. Existing paths were moved to the detected side
  on upgrade, so the whole fleet can now follow its devices; type a path in if
  you want one pinned.
- **Fixed: leaving IPP Path blank now sticks.** Blank means "work it out", and
  the probe has always tried the common paths — but it only recorded the
  answer when the stored value was null, and clearing the field in the
  interface stores an empty string. A cleared path re-probed on every cycle
  and never remembered what it found.
- **New: a printer's port and IPP path can now be edited.** They never could
  be. When a printer is switched to require TLS it usually also moves from
  port 631 to 443, and the only connection control on the page was the TLS
  checkbox — which on its own cannot work, because the device is still
  speaking cleartext on 631 and the TLS handshake simply fails. There was no
  way to fix such a printer from the interface at all.
- **Fixed: enabling TLS on the wrong port now says so.** That combination
  previously reported "Error occurred while communicating with IPP server",
  which reads exactly like a printer that is switched off. It now names the
  port, explains that the port is answering in cleartext, and points at 443 —
  and a genuine certificate problem is reported as a certificate problem
  rather than sending you to change the port.
- **Fixed: the print backend is now installed by setup and by updates.** It
  was copied into place by hand, and had drifted six weeks behind the code in
  the repository — including a spool-permission fix that had been written,
  reviewed and never actually deployed.

## [0.59.0] - 2026-08-20

Copy accounting goes from "the copiers are configured" to "you can see what
copying costs, per person, beside their printing".

- **New: copies now cost money in reports.** A copy is priced at the same
  cartridge rates as a print on the same machine, since it is the same
  machine and the same toner. The Combined Leaderboard splits **Print $**,
  **Copy $** and **Total $** rather than showing a print-only figure
  labelled as the total.
- **New: click a person to see what their number is made of.** A row on the
  Combined Leaderboard expands into a full breakdown — every printer they
  used (jobs, colour/mono, duplex/simplex, sheets, toner, paper, cost) and
  every copier (copies, colour/mono, scans, faxes, cost). "Open full
  report" opens the same breakdown on their Usage page, carrying the date
  range with it so a shared link means the same thing.
- **New: a copier nobody logs in to can belong to one person.** A desk
  copier with one regular user has no login and no code to match anyone to,
  so its copies have until now landed nowhere at all. You can now name its
  owner, and every copy its own meter records from that moment on is
  credited to them. Counting starts when you save — never from the meter's
  lifetime total — so nobody is handed the copies made before they were
  named, and changing the owner restarts it for the same reason. A copier
  that already reads per-account counters is left alone — the two would
  count the same copies twice — and the device page says so.
- **Changed: scans and faxes are no longer counted as copied pages.** They
  were previously summed in with copies. A scan puts no toner on paper, and
  once these numbers carry a cost, charging for one would be wrong. They
  are now reported beside copying instead of inside it, so copy totals may
  read slightly lower than before.
- **Two honest limits, stated in the interface rather than left to be
  discovered.** Copiers don't report duplex per copy, so copy paper is
  counted one sheet per page — an over-estimate wherever people duplex. And
  a whole-device meter reports a copy total with no colour breakdown, so
  those pages are priced at the mono rate, which under-states a colour
  copier. Both are shown on the breakdown that relies on them.
- **New: staff accounts can be provisioned onto Konica copiers**, by button
  or on a schedule, with progress shown while it runs and a record of who
  owns which account number afterwards.
- **New: per-account copy counts are read straight off the copier.** The
  device only reports lifetime running totals, so usage is the difference
  between two reads — which also means the hourly read interval is how
  precisely a copy can be dated. A first read of a copier records only a
  starting point and reports no usage, which is said plainly rather than
  looking like nobody used it. A counter cleared on the device is detected
  and the pages since the clear are kept rather than lost.
- **New: Tracked Copy Activity**, the counterpart to Untracked Copy
  Activity — what the copiers can put a name to, what they can't, and
  therefore how much of the picture the accounting actually covers. Copies
  against an account PrintOps didn't create are counted in their own
  number rather than hidden or rolled into the tracked total.
- **Fixed: a copier code held by two people was still being pushed to one
  of them.** The intent was always that a shared code goes to nobody, since
  its pages can't be attributed either way — but only the second holder was
  being left out, so both people could still log in with it while every
  page went on one person's report. Now neither holder gets it.
- **Fixed: a partly-failed account rewrite could orphan people's copies.**
  When a rewrite couldn't write every account, PrintOps forgot who owned
  *all* of them — including the accounts it never managed to change, which
  still held their original code on the copier. Those people's copies then
  arrived as unmatched activity. Only the accounts actually rewritten are
  reassigned now.
- Fixed: background printer queue resyncs could starve the CUPS scheduler,
  stalling print clients when one flapping printer resynced repeatedly.
- Fixed: browser autofill could overwrite a copier's stored admin
  credentials, and the device account list is now paged.

## [0.58.0] - 2026-08-17

- **Fixed: copy tracking was registering students as staff.** When
  "Automatically use Employee ID as a copier login" was on, *everyone* with
  an Employee ID set in Google Workspace was added to Staff Copier
  Identities — including students, who often have one. The staff
  Organizational Unit setting was only being applied to the Copier PIN
  Roster export, not to the identities themselves. In one district that
  meant 2,325 identities where only 269 were real staff. The staff OU is
  now applied to both, so the two can never disagree about who counts as
  staff.
- **New: Excluded Organizational Units for copy tracking.** People who have
  left are usually moved to an OU *inside* the staff OU (e.g.
  `/Employees/Inactive Employees`), so the staff OU setting on its own
  can't remove them. You can now list OUs to leave out, and excluding wins
  over including. The Copier PIN Roster card shows the effective filter in
  plain words, so you can see who is being tracked without working it out
  from two fields.
- **New: copiers can be scoped to their own building.** Each copier can
  name which staff Organizational Units get provisioned onto it. Device
  limits are per-device — a Lexmark XM3350 holds 250 local accounts and a
  Konica bizhub 1,000 — so a district that fits comfortably overall can
  still overflow one machine.
- **New: copiers can store their own admin login.** Needed by connectors
  that log into the device to read per-user counts or push user accounts.
  Stored encrypted and never shown again after saving. This is separate
  from the reference-only web admin password on a printer record, which
  PrintOps never logs in with.
- Added `docs/copier-capture-konica.md` and `docs/copier-capture-lexmark.md`
  documenting the Konica Web Connection and Lexmark EWS admin interfaces,
  captured against real devices.

## [0.57.0] - 2026-08-14

- **New: page quotas can now cap everyone *except* named people.** Each
  printer gets a "Who is limited here" toggle with two modes. **Only these
  people** is the previous behaviour — just the users you list have a
  limit. **Everyone except these people** is the new one: you set a single
  limit for the printer and name the handful of staff who are exempt from
  it, instead of entering a row for every person who should be capped.
  Which mode a printer is in is per-printer, so a library colour printer
  can cap everyone with a couple of staff let out while a plotter caps
  only the two classes that overuse it.
- Switching a printer's mode never deletes anything — the same rows are
  read the other way round, and the toggle spells out what they'll mean
  before you confirm, so switching back restores exactly what was there.
  Rows a mode isn't reading (a shared limit while in "only these people"
  mode, or an exemption with no shared limit to be exempt from) are shown
  greyed out and marked "Not enforced" rather than displaying a limit
  nothing is applying.
- Note for anyone already using the blank-user "default" row: that row is
  what now supplies the shared limit in "everyone except" mode, and is
  ignored in "only these people" mode. Every printer defaults to "only
  these people", so nothing starts enforcing differently on upgrade.

## [0.56.0] - 2026-08-13

- **Fix capability detection on printers that require IPPS.** Some devices
  refuse cleartext IPP outright, answering every request on port 631 with
  HTTP 426 Upgrade Required instead of serving it — confirmed live against
  an Epson ET-3950 Series, which advertises both `ipps://` and `ipp://` on
  631 but 426s all cleartext traffic. pyipp surfaces that as an error
  rather than upgrading (CUPS' own `ipptool` upgrades transparently), so
  every candidate path failed and an otherwise-healthy printer looked
  completely unreachable: no capabilities, and "offline" from the status
  poll. Such a device is now retried over TLS on the same path/port, and
  the successful upgrade is persisted to the printer's "Use TLS" setting
  so later polls skip the wasted cleartext attempt. Note this also
  repoints that printer's CUPS queue to an `ipps://` device URI on the
  next queue sync.

## [0.55.0] - 2026-07-18

- **Lock down default viewer permissions.** Fleet-wide Syslog and Jobs
  listing had no role scoping — any authenticated viewer could already see
  every user's print jobs and every printer's syslog events, not just
  their own. Both are now admin-only server-side, and the default-role
  nav is trimmed to just Print + Insights (their own scoped data), with a
  hard redirect if a viewer hits another page's URL directly.
- **New: admin "View as" impersonation** (Settings > Users), to verify
  the above going forward — mints a short-lived (20 min), non-refreshable
  token scoped exactly as the target user, strictly read-only (a new
  central guard 403s any mutating request carrying an impersonation
  claim, regardless of endpoint, matching the auth dependency's
  case-insensitive Bearer-scheme check). Every session is logged
  (admin, target, timestamps).

## [0.54.0] - 2026-07-12

- **Security hardening pass, prompted by GitHub's code-scanning alerts**
  after making the repo public: validated the ClassGuard/Mosyle base_url
  and Google Workspace customer_id integration settings (partial-SSRF
  findings), tightened the held-print-job spool directory from
  world-writable to a dedicated `lp` group (`scripts/setup.sh` now adds
  the service user to it), hardened `ci.yml` (explicit `permissions:`,
  pinned `pnpm/action-setup` to a commit SHA), and added explanatory
  comments to a few intentionally-empty `except` blocks. Also dismissed
  ~200 false-positive CodeQL alerts on Alembic's revision-variable
  convention and two already-safe log lines.

## [0.53.0] - 2026-07-12

- **Add a "Help" link to every admin screen**, pointing straight at that
  screen's new wiki page (new `WikiHelpLink` component). Printer detail tabs
  share one link via the tab layout, with an anchor matching the active tab.
- **Populated the wiki with a page for every app screen** — Printers, Jobs,
  Live Dashboard, Insights, Copier Accounting, every Settings tab, and more
  — written in plain language for non-technical installers, plus a fully
  rewritten Getting Started page with a real step-by-step install
  walkthrough (previously just a pointer at `setup.sh`).

## [0.52.1] - 2026-07-12

- **Docs: updated README and ARCHITECTURE to reflect what's actually
  shipped**, instead of describing PrintOps as an early scaffold with
  quotas/cost-accounting/RBAC/attribution/policy-enforcement still
  "just direction, not code." Ahead of making the repo public.

## [0.52.0] - 2026-07-12

- **New: Settings > Toner Cartridges — a fleet-wide cost/yield/model
  editor.** Every printer's cartridges in one place, grouped by printer,
  with a bulk-apply control to price a whole printer's colors at once,
  a search filter, and CSV/PDF export. Editing a cartridge's model here
  is the same field the per-printer Toner tab shows — always in sync.

## [0.51.0] - 2026-07-12

- **New: auto-learned toner cartridge model numbers.** HP and Canon
  cartridges now get their real orderable part number filled in
  automatically from SNMP (e.g. "CF226A", "Canon 054") the first time a
  printer is polled, without an admin typing it in. Never overwrites a
  value you've already entered.

## [0.50.1] - 2026-07-12

- **Fix: printer detail pages were noticeably narrower than the rest of
  the app.** Widened to match (max-w-2xl → max-w-6xl).

## [0.50.0] - 2026-07-12

- **New: live toner level polling + low-toner warning.** Each color
  cartridge's real percentage remaining is now polled over SNMP every 30
  minutes (piggybacking on the existing counter poll) and on-demand via
  "Detect via SNMP." Set a per-color warning threshold (defaults to 15%)
  and a badge flags the cartridge once it drops below it.
- **New: toner level history chart.** A bar chart per printer showing each
  color's level over the last 7/30/90/180 days, bars colored to match the
  actual toner color.

## [0.49.0] - 2026-07-12

- **New: cartridge model number is now per-color.** Color printers take a
  different part number per color cartridge, so the Toner Cartridges card
  now has a Model field on each color's row (black/cyan/magenta/yellow)
  instead of one generic field for the whole printer. Existing generic
  values were automatically moved onto the Black row.

## [0.48.2] - 2026-07-12

- **Fix: the file picker on Print and Copier Import upload looked like
  plain text, not a button.** The native file input had no visible
  clickable affordance under this app's Tailwind styling — now rendered
  as a proper pill-shaped button matching the rest of the UI.

## [0.48.1] - 2026-07-12

- **Fix: the fleet-wide capability rediscovery loop (0.48.0) only kept
  PrintOps's own display fresh** — the actual CUPS queue PPD an end
  user's print dialog reads as its default page size wasn't touched,
  so it could silently drift out of sync with the live device between
  queue resyncs. The loop now re-syncs a printer's CUPS queue whenever
  it detects the probed default page size or tray contents actually
  changed, so the two stay in sync without needing a manual Resync
  Queue click or an offline/online reconnect.

## [0.48.0] - 2026-07-12

- **New: paper size visibility on the Discovered Capabilities card.**
  Shows a printer's actual reported default page size and, for
  copiers/MFPs, what's currently loaded in each tray — no more walking to
  each device to check why one might be defaulting to an unexpected size.
  Admins also get an on-demand "CUPS Queue Default" check comparing the
  device's own reported default against what the CUPS-generated queue's
  PPD currently has set, flagging a mismatch — the same signal that
  previously identified the *DefaultColorModel bug, now available without
  needing to dig through a queue's PPD by hand.
- **New: capabilities now refresh every 30 minutes across the fleet**, not
  just on printer creation, manual Rediscover, or an offline->online
  reconnect — so a same-day tray reload or capability change shows up in
  PrintOps without an admin needing to notice and click Rediscover.

## [0.47.0] - 2026-07-12

- **New: self-service web upload printing, restricted by OU.** A logged-in
  user can now upload a PDF and print it from a new "Print" page, without
  needing a client-configured CUPS/AirPrint queue — delivered through the
  target printer's normal queue via the same `lp` submission path test
  prints and Print Release already use, so it's logged and attributed like
  any other job. Admins can optionally restrict which printers a given
  user may target, by Google Workspace org unit, from a new "Self-Service
  Print Access" card on each printer's detail page — a printer with no
  restrictions configured stays open to everyone, matching this app's
  existing permissive-by-default convention. This restriction only applies
  to the new upload path; normal AirPrint/MDM printing is untouched.

## [0.46.1] - 2026-07-12

- **Fix: Settings > Server's Sync Now gave no visible feedback.** Clicking
  it did something (a real cupsd restart) but showed nothing either way —
  added a green success message (new `SuccessState` component) alongside
  the existing red error one, for both Save and Sync Now. Also fixed: a
  200 response that still recorded a non-fatal sync failure (the same
  "saved, but the sync itself failed" case `Printer.queue_sync_error`
  already has) now shows as a visible error instead of looking identical
  to a real success.

## [0.46.0] - 2026-07-12

- **New: Settings > Server (hostname + TLS for the CUPS server itself).**
  Fixed a real bug: the print server's own configured domain (the one
  this box's own Caddy already holds a valid Let's Encrypt cert for) got
  a flat `400` from CUPS, since `cupsd.conf` had no `ServerName`/
  `ServerAlias` for anything but its auto-detected hostname. The hostname is now
  admin-editable and synced to `cupsd.conf` + a real certificate
  automatically — on save, on a daily background timer, or via a manual
  "Sync Now" button. Two CUPS 2.x quirks confirmed live along the way:
  TLS cert selection is keyed off the OS-level hostname, not
  `cupsd.conf`'s `ServerName`, so the managed cert has to overwrite the
  file CUPS already auto-generated for its system hostname; and only a
  full `cups.service` restart (not a config reload) picks up a changed
  cert or `ServerName`. `Require encrypted client connections` and the
  secure AirPrint (`_ipps._tcp`) advertisement both stay off by
  default — only the hostname fix and real-cert swap are always-applied,
  since those are pure improvements with no failure mode for an existing
  plaintext client. Also fixed: MDM connection info (Printers > a
  printer > Connection) was still reading the static env-only
  `print_server_host` instead of this new setting, so newly-configured
  queues kept advertising the raw IP after the domain was set.

## [0.45.0] - 2026-07-11

- **New: TLS (IPPS) toggle + auto-detection.** `Printer.use_tls` was
  already fully wired through the backend (CUPS queue resync, scheme
  selection when delivering to the real printer) but had no control
  anywhere in the UI — added a checkbox to both Add Printer and the
  printer detail page. Capability discovery now also requests the IPP
  attribute that reveals whether a device advertises IPPS support at all
  (`uri-security-supported`, no extra network round-trip), shown as an
  "IPPS Supported" badge with a nudge to turn the toggle on when a
  printer advertises it but isn't using it yet.

## [0.44.1] - 2026-07-11

- **Document Print Release & Follow-Me Printing in settings/printer help
  text.** Settings > Quotas — the nearest existing global settings screen
  — didn't mention either feature at all, so there was no way to discover
  they exist from Settings; added a pointer that both are configured per
  printer. The printer detail page's own Release & Quotas tab now
  introduces both mechanisms together up front instead of leaving
  Follow-Me as a secondary blurb next to its own checkbox.

## [0.44.0] - 2026-07-11

- **New: Virtual Follow-Me queue.** Printers > Add Follow-Me Queue creates
  a queue with no real device behind it — clients can select it (and it
  can be pushed via MDM) just like a physical printer, but every job sent
  to it is always held and only ever bound to a real device at release
  time, at whichever Follow-Me-enabled printer the person walks up to.
  CUPS queue sync skips the real-device probe for these (there's nothing
  to reach) and uses a generic driverless PPD that already defaults to
  full color, avoiding a virtual-queue version of the earlier color-copier
  grayscale-default bug. Background status/SNMP polling and the manual
  rediscover/check-status/release-bypass actions all correctly no-op or
  reject for a virtual printer, since none of that applies to something
  unreachable.

## [0.43.0] - 2026-07-11

- **New: Follow-Me Printing.** A per-printer opt-in (`follow_me_enabled`)
  that sits alongside the existing Print Release toggle rather than
  replacing it — a job held because a printer has this on becomes
  releasable at *any* other printer that also has it enabled, not just
  the one it was originally sent to, via the same PIN kiosk. Useful for a
  bank of shared printers where staff end up releasing wherever they're
  standing rather than walking back to the exact device they printed to.

## [0.42.0] - 2026-07-10

- **New: Zabbix integration.** Settings > Integrations > Zabbix lets an
  external Zabbix server poll PrintOps for fleet-wide print stats
  (rolling 24h job/page counts, by color/duplex) and per-printer health
  (status, queue sync errors, page counts) — an alternative way to view
  the same numbers Live Dashboard and Insights show. Fully UI-driven: an
  admin-rotatable API token, a "Download Template" button for a generic
  Zabbix template (works unmodified across any PrintOps install — actual
  server URL/token are Zabbix host macros, not baked into the file), and
  a two-sided setup guide (PrintOps side + Zabbix side). Zabbix's Low-Level
  Discovery pulls in printers automatically. No CLI/SSH steps required on
  the PrintOps side.

## [0.41.0] - 2026-07-10

- **Duplicate printer detection on Add Printer.** As you fill in the
  form, a fixed "Duplicate Printer Possible Match" banner in the
  top-right corner warns if the name, IP address, hostname, or serial
  number exactly matches an existing active printer, with a link to the
  match — a non-blocking heads-up, not a hard stop, since a handful of
  legitimate reasons for an intentional duplicate exist. Archived
  printers are excluded from the check, since re-adding a printer with
  the same IP/name after archiving the old one is the expected
  "replaced this device" pattern, not a duplicate.

## [0.40.1] - 2026-07-10

- **Fix jobs showing the raw email instead of the resolved name.** The
  printer detail page's Jobs tab, the main Jobs page, and Quota Holds all
  showed `submitted_by` (email) directly instead of the already-resolved
  `submitted_by_name` (Google Workspace display name), unlike Live
  Dashboard's recent-jobs feed. All three now match that fallback:
  resolved name, else email, else a placeholder.

## [0.40.0] - 2026-07-10

- **Add Copier: pick an existing Printer to prefill the form.** The
  "Linked Printer" selector on Add MFP Device now also fills in
  name/model/serial number/IP/hostname/building/room/department (and a
  best-guess vendor) from the picked printer instead of just linking the
  two — fields stay editable after, so nothing has to be retyped for a
  device that's already set up as a Printer.

## [0.39.2] - 2026-07-10

- **Fix intermittent bounce back to login after a successful Google
  sign-in.** Live Dashboard's admin-only guard (added alongside the OU
  Viewer role) redirected to `/login` on `currentUser === null`, which
  raced against the same hook's own fetch settling right after
  `/login/callback` sets the token — confirmed live: repeated login
  attempts landed at inconsistent points (some stopped right after the
  auth check, others loaded fully) rather than failing consistently, the
  signature of a race rather than a real auth failure. The "no token"
  case is already handled reliably by `useAuthGuard` one level up; removed
  the redundant, racy check.

## [0.39.1] - 2026-07-10

- **Security fix: PostCSS XSS (GHSA-qx2v-qp2m-jg93, CVE-2026-41305).**
  Next.js pinned a transitive `postcss@8.4.31`, below the patched 8.5.10 —
  added a pnpm override forcing `postcss` to `^8.5.16` repo-wide (Tailwind's
  own postcss dependency already resolved there) so both consumers use the
  patched version.

## [0.39.0] - 2026-07-10

- **Printers: CSV export.** Export the (optionally search-filtered)
  Printers list as a CSV — name, status, manufacturer, model, serial
  number, IP address, hostname, building/room/department, page count,
  AirPrint status, and archived flag.

## [0.38.0] - 2026-07-10

- **Users & Permissions merged into one page.** OU grants for "OU Viewer"
  accounts are now edited inline (an expandable org-unit picker per row)
  instead of living on a separate Permissions page — one place to manage
  an account's role and, if applicable, what it can see.
- **Real org-unit picker, scoped to staff.** Granting OUs is now checkboxes
  populated from your synced Google Workspace directory instead of typing
  a path blind — and scoped to `staff_org_unit_path` (same setting the
  copier PIN roster already uses), so it shows ~20 real staff org units
  instead of all 70+ directory OUs (student grade levels, device OUs,
  admin housekeeping OUs, etc. included).
- **Email autocomplete when adding a user.** The Add User email field now
  suggests matches from your synced directory as you type, instead of
  requiring the exact address.
- **Integrations moved under Settings.** Google Sign-In, Mosyle, Google
  Workspace, and ClassGuard now live at Settings > Integrations instead of
  their own top-level nav item.

## [0.37.0] - 2026-07-10

- **Pre-provision accounts before first sign-in.** Admins can now add a
  user by email + role from Settings > Users instead of waiting for that
  person's first Google sign-in and promoting them afterward — the role
  (and any OU grants) take effect the moment they actually sign in.
- Added a staff-scoped org-units lookup (`/settings/google-workspace/org-units`)
  and a `role` filter on the users list, both powering the Settings UX
  changes below.

## [0.36.0] - 2026-07-10

- **Printers: search box.** Filter the Printers list by name, IP address,
  hostname, manufacturer, model, serial number, or building/room/department
  — matches all typed words across any of those fields. Client-side, so it
  applies instantly with no extra loading.

## [0.35.1] - 2026-07-10

- **Fix color copiers reverting to grayscale for Word/Adobe (again).** The
  v0.15.2 fix only forced CUPS's `print-color-mode-default` IPP attribute;
  the driverless PPD's own, separate `ColorModel` default got reset to
  Gray every time a queue resynced (including automatic ones on a
  printer's offline→online reconnect), silently undoing the fix for apps
  that go through the classic PPD-based print path. Both sync scripts now
  also force `ColorModel=RGB` on every sync for color-capable printers, so
  it can't drift back. Applied live to the 3 queues that had reverted (CO
  Danica Copier, SS - Director Color Printer, LCACTC - RM 502 Color
  Printer).

## [0.35.0] - 2026-07-10

- **New "OU Viewer" role.** A read-only account type scoped to Insights
  only, filtered to a set of granted Google Workspace org-unit paths (e.g.
  a building or department) rather than the whole org or just one person's
  own history. Grant OU paths per account from Settings > Users. Existing
  admin/viewer accounts are unaffected.
- Live Dashboard's org-wide hourly view is now admin-only (it was never
  self- or OU-scoped, unlike every other report) — non-admins are routed
  to Insights instead.

## [0.34.0] - 2026-07-10

- **Live Dashboard: window-length preference.** A "Window" dropdown in the
  header lets you pick 3, 6, 12, or 24 hours instead of always showing the
  last 24. The choice is saved per-browser and sticks until changed again
  — useful for a TV display that only needs to show the last few hours of
  activity rather than a full rolling day.

## [0.33.0] - 2026-07-10

- **Live Dashboard: 30-minute bucket granularity.** The hourly chart now
  buckets by 30-minute interval instead of a full hour (48 bars across the
  rolling 24h window instead of 24), giving finer resolution on when
  activity actually happened. Print jobs and tracked copies both have
  precise per-event timestamps, so this doesn't introduce any of the
  noise a shorter window would on the SNMP-counter-delta-based untracked
  copy estimate (which stays daily-only, unaffected by this change).
- **Live Dashboard: clearer date/time axis.** Only on-the-hour bars and
  the single bar where the calendar date changes get an axis label now
  (the 24 half-hour bars in between stay unlabeled to avoid clutter). The
  date-change bar renders as a two-line tick (time on top, date below)
  with a dashed vertical divider through the chart, so it's obvious at a
  glance which bars are "yesterday" vs. "today."

## [0.32.0] - 2026-07-10

- **Live Dashboard: true rolling 24-hour window.** The hourly bar chart no
  longer resets at local midnight — it now always shows the last 24 hours
  with the current (in-progress) hour as the rightmost bar, sliding forward
  continuously as time passes and aging the oldest hour off the left edge,
  like a classic strip-chart. Stat tiles and copy relabeled "(24h)"
  accordingly.
- **Live Dashboard: full screen toggle.** A button in the header (four
  arrows out to expand, four arrows in to collapse) puts just the dashboard
  content into the browser's native full screen mode — the sidebar nav
  drops away, which is the point for a wall-mounted TV display. Click again,
  press Escape, or use the browser's own exit-fullscreen control to return
  to normal.
- **Live Dashboard: tracked copies on the hourly chart.** The bar chart now
  stacks tracked walk-up copy pages (from the copier accounting connectors)
  on top of print pages per hour, plus a new "Copy Pages (24h)" stat tile.
  Untracked/estimated copy volume (SNMP counter-delta based) is only ever
  computed at daily granularity and stays on the existing Untracked Copy
  Activity report rather than being forced into an hourly view that would
  overstate its precision.

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
  `*example.com`) to separate staff from students by domain in one
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
