# Konica Minolta bizhub i-Series — admin interface capture

Captured live against a **bizhub 750i** and a **bizhub 950i** on 2026-08-17,
using the device admin password stored in PrintOps.

Device names and addresses are deliberately omitted, and the sample values
below are illustrative — this is a public repository, and a list of which
internal devices run which authentication configuration is useful only to
someone who shouldn't have it. Substitute your own device's address
throughout; `DEVICE` stands for it in every example.

Method note: rather than a browser DevTools capture, the contracts below were
read directly out of the device's own admin SPA bundle
(`GET /wcd/Integrated_main.js`, ~600 KB, unauthenticated) and then **confirmed
against the live device**. That bundle is the authoritative source for request
shapes — it is the code the admin page itself runs. Every request below is
marked verified or unverified.

> `/wcd/api` is an unofficial internal contract. Pin behaviour to this
> document, fail loudly if a shape changes, and keep CSV import as the
> fallback (§9 of the task brief).

---

## 0. Headline finding — Account Track may well be OFF

**verified on the units tested**

```
MFP.AuthSetting.AuthMode.AuthType   = "None"
MFP.AuthSetting.TrackMode.TrackType = "None"
MFP.JobLog.Enable                   = "Off"
```

Both units tested had **no** user authentication and **no** Account Track
enabled — i.e. walk-up copying with no identification. That is why
`AppReqGetTrackSetting` returns `AuthNotTrackMode`: there is no account data
because the feature is switched off. Check your own devices before assuming
either state; this is the default a bizhub ships in, not a finding about any
particular installation.

Consequence: `sync_users_to_device` and `get_user_accounting` cannot do
anything useful on this device until Account Track is enabled on it. Enabling
it is a **user-visible production change** — every person who walks up must
then enter a code. It is a rollout decision, not an implementation detail.

`ManageMode = "Mode2"`, `MarketArea = "NorthAmerica"`.

The two units differed only in `MFP.JobLog.Enable`, which was `"Off"` on one
and `"On"` on the other — worth checking per device rather than assuming.

Two settings that matter before enabling Account Track anywhere:

| Key | Value | Why it matters |
|---|---|---|
| `MFP.AuthSetting.CommonMode.NoAuthPrintOn` | `"true"` | Printing without authentication is **allowed**. Turning on Account Track will not break PrintOps' print path while this stays `true`. Do not change it in the same sitting — and **re-read it afterwards**, because enabling Account Track can leave it `false`. See §3.9.4. |
| `MFP.Security.AuthOperateProhibit.Count` / `.ReleaseTime` | `"3"` / `"5"` | The admin lockout really is 3 failed attempts, 5 minutes. This is why a rejected password must never be retried programmatically. |

---

## 1. Session model

### 1.1 Anonymous session — **verified**

```http
GET /wcd/index.html
```

Sets cookie `ID=<32 chars>` scoped to `/wcd`. This is the "Public" session.
Device-total counters are readable on it with no login at all.

### 1.2 Admin login — **verified**

```http
POST /wcd/login.cgi
Content-Type: application/x-www-form-urlencoded

func=PSL_LP1_LOG&R_ADM=AdminAdmin&username=&password=<admin password>
```

Field names and the `R_ADM` constant come from `Integrated_main.js`:

```js
dAdminMode = {ADMIN_MODE_ADMIN:"AdminAdmin", ADMIN_MODE_USER:"Admin",
              ADMIN_MODE_BOX:"BoxAdmin"}
g = function(t,n){ D("Admin"); e.func="PSL_LP1_LOG";
                   e.R_ADM = dAdminMode.ADMIN_MODE_ADMIN; u(t);
                   r("./login.cgi", n, false) }
u = function(t){ e.username=t.userName; e.password=t.password; ... }
```

The bizhub admin login has **no username** — send `username=` empty.

**Success response** is an HTML page (HTTP 200) that sets `document.cookie
="adm=AS_COU"` and redirects to `./a_system_counter.xml`. Detect success by
the presence of `a_system_counter` / `top.xml` in the body, *not* by status
code — a rejected login is also HTTP 200.

The session continues on the same `ID=` cookie; the login elevates that
cookie rather than issuing a new one.

### 1.3 Logout — **verified**

```http
POST /wcd/a_user.cgi
func=PSL_ACO_LGO
```

Returns HTTP 200. **Always call this**, including on error paths — the device
permits only one admin session at a time and a held session locks out the
panel and other tools.

---

## 2. The WebAPI transport

**verified**

```js
_getWebAPIData = function(url, data, token){
  var o = "api/" + url, n = data || {};
  n.Token = token || spa_get_token();
  spa_ajax_postJsonRequest(o, JSON.stringify(n), ..., false);
}
```

So every call is:

```http
POST /wcd/api/<Endpoint>
Content-Type: application/json

{ ...params..., "Token": "<rotating token>" }
```

**Token handling (this answers the open question in the task brief):** the
rotating `Token` is echoed on **every** WebAPI call, reads included — not
only writes. Every response carries a fresh `MFP.Token`; keep the latest and
send it on the next call. Seed one from the unauthenticated
`AppReqGetCounterInfo/_Total` call before logging in.

**Result envelope:**

```json
{"MFP":{"Token":"...","Result":{"ResultInfo":"Ack"|"Nack",
  "FaultRequest":"...","ErrorDetails":"...","ErrorDescription":"..."}}}
```

`ResultInfo` is the success flag. Note a `Nack` still returns HTTP 200 — never
treat 200 as success.

**A body is always required.** A bodyless POST returns `411 Length Required`;
send an explicit empty body (`{}` plus Token) when there are no parameters.

---

## 3. Endpoints

### 3.1 Device total counters — **verified, no auth needed**

```http
POST /wcd/api/AppReqGetCounterInfo/_Total
{"Token":"..."}
```

Returns `MFP.UserCounterInfo.TotalCounterList.TotalCounter[]`, 23 entries on
the 750i, each `{"Type":..., "Count":"..."}`. Observed types include
`Total` (763117), `DuplexTotal`, `Document`, `Paper`, `TotalLarge`,
`PrintPageTotal`, `PaperSizeA4`, `PaperSizeLetter`, `PaperSizeLegal`,
`PaperSizeLedger`, `Nin12in1`, `Nin14in1`, `Nin1Other`, `PaperType*`.

`/_BySize` likewise works unauthenticated.

### 3.2 Per-account counters — **verified UNSUPPORTED**

```http
POST /wcd/api/AppReqGetCounterInfo/_Account   ->  Nack "Webapi not supported."
POST /wcd/api/AppReqGetCounterInfo/_User      ->  Nack "Webapi not supported."
```

This is the important negative result: these return `Webapi not supported`
**even with a valid admin session**. It is not a permissions problem. On this
firmware, per-account counters are *not* available through
`AppReqGetCounterInfo`.

`get_user_accounting` therefore needs a different source. **It has one** —
`AppReqGetTrackCounterInfo`, a different endpoint on the same WebAPI. See
§3.9.3. The conclusion to draw from this section is narrower than it first
looks: `AppReqGetCounterInfo` has no per-account variant, not that the
WebAPI has no per-account counters.

### 3.3 Account Track list — **verified reachable, blocked by device mode**

```http
POST /wcd/api/AppReqGetTrackSetting
{"TrackListCondition":{"TrackType":"Private",
  "ObtainCondition":{"Type":"OffsetList","OffsetRange":{"Start":1,"Length":100}}},
 "Token":"..."}
```

Response data path: `MFP.TrackList.Track` (from `WebApiTrackSettingGet`).

Current result: `Nack / AuthNotTrackMode / "invalid track mode"` — see §0.

Verified parameter facts:
- `TrackType` must be `"Private"`. `"Public"` is rejected with
  `GeneralIllegalValue`.
- `ObtainCondition` offsets are **1-based**. `Start: 0` is rejected with
  `GeneralRangeIllegal`; `Start: 1` passes validation.
- Both integer and string forms of `Start`/`Length` are accepted.
- The SPA's default page length is 100.

For "Password Only" Account Track, the mode value to look for is
`MFP.AuthSetting.TrackMode.TrackType == "Password"` (the SPA compares against
that literal).

### 3.4 User Authentication list — **verified working**

```http
POST /wcd/api/AppReqGetUserAuthSetting
{"UserListCondition":{"UserType":"Public",
  "ObtainCondition":{"Type":"OffsetList","OffsetRange":{"Start":1,"Length":100}},
  "BackUp":"false"},
 "Token":"..."}
```

Response data path: `MFP.AuthUserSettingList.AuthUserSetting`.

Live result: `Ack`, `ArraySize: 1` — only the built-in Public account
(`AuthNo: 16776961`). No real users are registered.

Per-user record shape (fields that matter for accounting):

| Field | Example | Note |
|---|---|---|
| `AuthNo` | `"16776961"` | device-side user id |
| `UserType` | `"Public"` | |
| `PINCodeExist` | `"false"` | whether a PIN is set |
| `CardAuthData` | `"NoExist"` | badge data present |
| `FunctionLimit.EnableCopy2` / `EnablePrint2` / `EnableScan2` | `"All"` | per-function permission |
| `TotalPrint.Limit` / `.LimitOn` | `"0"` / `"false"` | **built-in page quota** |
| `BoxNumberLimit`, `ReferLicence` | | not accounting-relevant |

Note `TotalPrint.Limit` — the device has its own per-user page cap, which
overlaps with PrintOps' own quota feature. Worth deciding which owns the
limit before both do.

`ObtainCondition` also accepts `{"Type":"IndexList","IndexRange":{"Start":n,
"End":m}}` (`getSendDatabyRange`).

### 3.5 Device configuration blob — **verified**

```http
GET /wcd/api/AppReqGetCustomData/_A-00-00001
```

~40 KB JSON, admin session required. This is what the SPA loads as
`commonData`, and it is the cheapest way to read the device's auth posture.
Keys used in §0 above. Saved sample: not committed (contains device config).

### 3.6 Job history — **verified UNSUPPORTED**

`AppReqGetJobList/_<type>` (data path `MFP.JobHistoryList.JobHistory`) looked
like the remaining lead for per-user activity. It is not available:

```
POST /wcd/api/AppReqGetJobList/_Print    ->  Nack "Webapi not supported."
                          _Send, _Save, _Receive, _All   ->  same
```

Tested on the 950i, which had `MFP.JobLog.Enable = "On"`
— so this is a firmware limitation, not an empty-log artifact.

Taken with §3.2, the conclusion for this firmware generation is: the WebAPI
exposes **device totals, user-auth settings and track settings only**. There
is no per-user counter or per-user job data on `/wcd/api` at all.
`get_user_accounting` must come from the classic `a_*.xml` admin export, and
that remains uncaptured.

### 3.7 Other endpoints present in the bundle — **unverified**

Also present, not relevant here: `AppReqGetAbbr`, `AppReqGetGroupAbbr`,
`AppReqGetUserBoxList`, `AppReqGetBulletinBoardBoxList`, `AppReqGetProgramKey`,
`AppReqGetFileInfo`, `AppReqCheckPasswordStrength`, `AppReqSingleSignOn`,
`AppReqSetCustomMessage`, `AppReqGetFaxAddressRegistrationRestricSetting`.

Login-related `func` codes: `PSL_LP1_LOG` (admin login), `PSL_LP0_TOP` (public),
`PSL_ACO_LGO` (logout), `PSL_GET_MFPINFO`, `PSL_GET_TRC`, `PSL_SAVE_MFPINFO`,
`PSL_IDP_TOP`, `PSL_SMB_FIL_LST`, `PSL_SMB_FIL_DWN`.

---

## 3.8 Admin UI menu map — **verified**

Read from the device's own menu resources rather than from vendor
documentation: the menu tree is in `Integrated_main.js`
(`underscore_template_Menu`) and the English labels are in
`GET /wcd/lang_menu_En.json` (48 KB) and `GET /wcd/lang_co_En.json` (22 KB),
both fetchable with only the anonymous cookie.

Account Track lives under its **own top-level tab**, not under Security:

**Tab: "User Auth/Account Track"** (`ID_Menu_Authentication`,
`Common_TabName_Authentication`), described as "User Auth. and Account Track
Sett." Its pages, in template order:

| Menu id | Label |
|---|---|
| `Authentication_AuthForm` | **Authentication Method** |
| ↳ `…_UserAuthSetting` | User Authentication Setting |
| ↳ `…_UserRegist` | User Registration |
| ↳ `…_PublicUserAuthSetting` | Public User |
| ↳ `…_UserCounter` | User Counter |
| `Authentication_TrackSetting` | **Account Track Settings** |
| ↳ `…_TrackRegist` | **Account Track Registration** |
| ↳ `…_TrackCounter` | **Account Track Counter** |
| `Authentication_LoginLimitFunction` | Prohibit Functions (lockout) |
| `Authentication_NoAuthPrintOn` | **Print without Authentication** |
| `Authentication_SimpleAuthSetting` | Simple Authentication setting |

Also relevant: `Synchronize User Auth. / Account Track`
(`Common_ScreenName_ChangeSynchronizedTrack`), `User/Account Common Setting`
(`Common_ScreenName_UserAndTrackCommon`), and `Account Name`
(`Common_TrackName`) as the per-account field label.

**Account Track Counter** (`Auth_DepartmentCounter`) is the most likely
source for `get_user_accounting` given §3.2 and §3.6 ruled out the WebAPI —
its underlying request is **not yet captured**, because the device has no
account data to show while Account Track is off.

## 3.9 Account Track, with the feature ENABLED — **verified**

Re-captured on a 950i after Account Track was switched on
(`TrackMode.TrackType = "Password"`, i.e. Password Only; User
Authentication left `"None"`).

**`AppReqGetTrackSetting` starts working.** Same request as §3.3; with the
feature on it returns `Ack` instead of `AuthNotTrackMode`:

```json
{"MFP":{"Result":{"ResultInfo":"Ack"},"TrackList":{"ArraySize":"0"}}}
```

`ArraySize` is the account count — this is the endpoint to poll to read the
device's current account list, and to diff against before provisioning.

**These four endpoints stay unsupported**, with Account Track enabled and
a valid admin session:

```
AppReqGetCounterInfo/_Account      AppReqGetTrackCounter
AppReqGetCounterInfo/_Track        AppReqGetTrackCounter  (+TrackListCondition)
```

That was read at the time as "per-account counters are not on the WebAPI".
It is not: the working endpoint is **`AppReqGetTrackCounterInfo`** (§3.9.3)
— `AppReqGetTrackCounter` above is the same name one word short. Guessing
endpoint names is what produced the wrong conclusion; §3.9.3 came from
reading the device's own screen JS instead.

### 3.9.4 Enabling Account Track can silently kill all printing — **observed in production**

On a 750i, Account Track was enabled on 2026-08-19 and
`NoAuthPrintOn` came out `false`. The device then **accepted every print
job and deleted it at the panel**, because a job arriving without an
account code is unauthenticated and nothing exempts printing any more.
Walk-up copying stopped at the same moment for the same reason.

It is silent from the server's side. CUPS hands the job off, marks it
completed and logs no error — the job dies after the handoff. The three
other bizhubs enabled in the same rollout kept `NoAuthPrintOn = "true"`
and were unaffected, so "the other copiers work" does not clear this.

**How to detect it:** the device's own meters stop. `page_count_print` and
`page_count_copy` in `printer_counter_readings` freeze at the moment of
the change and stay frozen while jobs keep arriving:

```
2026-08-19 17:51   print 227548   copy 540545
2026-08-19 18:21   print 227549   copy 540545
2026-08-20 15:36   print 227549   copy 540545   <- ~20 hours, nothing
```

A copier whose counters have not moved all day, on a queue that is still
accepting jobs, is this until proven otherwise. Reading
`NoAuthPrintOn` out of the §3.5 config blob confirms it in one call.

**The fix is device-side and manual.** No write contract for this setting
is captured here yet. Web Connection → Administrator → **User
Auth/Account Track** → **Print without Authentication** → **Allow**
(`#ID_SubMenu_Authentication_NoAuthPrintOn`, screen `003_004_APO000`).
Jobs already deleted are gone — there is no spool to replay.

### 3.9.1 The admin pages behind Account Track — **verified**

The SPA loads per-page templates by code, fetchable unauthenticated:

```
GET /wcd/spa_<code>.tmpl.html
```

| Code | Page |
|---|---|
| `003_000_AUT000` | Authentication Method |
| `003_001_USR000` | User Registration |
| `003_002_TRA000` | Account Track Registration (list) |
| `003_002_TRA001` | Account Track account — **add/edit form** |
| `003_002_TRA002` | Account Track account — delete confirm |
| `003_002_TCR000` | Account Track Counter (list) |
| `003_002_TCR001` | Account Track Counter — per-account detail |

These are classic form posts carrying `func` and the rotating `h_token`,
distinct from the JSON WebAPI in §2. Admin-scoped funcs post to
`a_user.cgi` (the same endpoint as the `PSL_ACO_LGO` logout); public ones
post to `user.cgi`.

**Account list paging** (`003_002_TRA000`):

```
func=PSL_AA_TRA_PAG   h_token=<token>
H_SRT=<start>  H_END=<end>  AA_TRA_H_BOX=Public  H_FLAG=Delete
```

**Add / edit an account** (`003_002_TRA001`) — `func=PSL_AA_TRA_TRA`,
`AA_TRA_H_NUM=new` for a new account (an existing index to edit):

| Field | Max | Meaning |
|---|---|---|
| `AA_TRA_T_NUM` | 4 | account number |
| `AA_TRA_T_NAM` | — | account name |
| `AA_TRA_P_UP` | 64 | password — **the 5-digit staff ID in Password Only mode** |
| `AA_TRA_P_CMP` | 64 | password confirmation (must match) |
| `AA_TRA_T_INF` | 20 | free-text info |
| `AA_TRA_S_ACS`, `_ASA`, `_COP`, `_CCP`, `_SCP`, `_SFP`, `_UPA`, `_FUA`, `_FCP`, `_FSC`, `_FFA`, `_FPR`, `_FPS` | — | per-function permission flags |
| `AA_TRA_C_TPL` / `AA_TRA_T_TPL` | — | total page limit: enable flag / value |
| `AA_TRA_C_CPL` / `AA_TRA_T_CPL` | 7 | colour page limit: enable / value |
| `AA_TRA_C_BPL` / `AA_TRA_T_BPL` | 7 | black page limit: enable / value |
| `AA_TRA_C_BNL` / `AA_TRA_T_BNL` | 4 | (limit pair, units unconfirmed) |

Note `AA_TRA_P_UP` accepts 64 characters, so the commonly-cited 8-character
Account Track password cap is not a limit this firmware imposes on the
field itself. A 5-digit ID fits regardless.

The per-account limit pairs overlap PrintOps' own page-quota feature —
decide which system owns the limit rather than setting both.

**Still unverified:** the exact success/failure response of
`PSL_AA_TRA_TRA`, the defaults required for the permission/limit fields on
a minimal create, the Account Track Counter read (`003_002_TCR000/TCR001`
field semantics), and the bulk Authentication-Information import. All
need at least one real account to exist.

### 3.9.2 Creating an account — **verified end to end**

```http
POST /wcd/a_user.cgi
Content-Type: application/x-www-form-urlencoded

func=PSL_AA_TRA_TRA
h_token=<token from the login response's id="h_token">
AA_TRA_H_NUM=new          # "new", or an existing index to edit
trackType=Password        # matches TrackMode.TrackType
AA_TRA_R_RNM=Direct       # "Direct" = use the number below; "Space" = next free
AA_TRA_T_MAX=1000         # the device's registration maximum
AA_TRA_T_NUM=999          # account number (1..max)
AA_TRA_T_NAM=POTEST       # account name — MAX 8 CHARACTERS
AA_TRA_P_UP=99999         # password = the staff ID in Password Only mode
AA_TRA_P_CMP=99999        # must match
```

**Success** (HTTP 200, XML):

```xml
<Message><Item Code="Ok_1" Param="999" SubCode="Record">Ok_1</Item></Message>
<Redirect>a_authentication_track.xml</Redirect>
```

`Param` echoes the account number created. **Validation failure** uses the
same envelope with an error code and the offending field named:

```xml
<Message><Item Code="Err_1">GeneralIllegalValue</Item>
<ErrorDescription>TrackName</ErrorDescription></Message>
```

So success/failure is `Item Code` = `Ok_*` vs `Err_*`, **not** the HTTP
status, which is 200 either way. `ErrorDescription` names the bad field,
which is good enough to report per-account failures precisely during a bulk
sync.

**The 8-character limit is on the account NAME (`AA_TRA_T_NAM`), not the
password.** `AA_TRA_P_UP` accepts 64. This is the opposite of what is
usually assumed, and it matters: staff IDs go in the password, so their
length is unconstrained in practice, while any human-readable account name
must be squeezed into 8 characters.

The minimal accepted field set is the one above — the `AA_TRA_S_*`
permission flags and `AA_TRA_C_*`/`AA_TRA_T_*` limit pairs may all be
omitted, and the device applies its defaults (verified in the created
record: `FunctionLimit` all `"All"`, `TotalPrint.LimitOn "false"`,
`AccountStop "Off"`).

The created account reads back through `AppReqGetTrackSetting` as:

```json
{"TrackID":"999","TrackType":"Private","TrackPasswordExist":"true",
 "TotalPrint":{"LimitOn":"false","Limit":"0"},"AccountStop":"Off",
 "FunctionLimit":{"EnablePrint":"All","EnableCopy":"All","EnableScan":"All",
                  "EnablePrintSend":"All","EnableFaxSend":"All"}}
```

Note `TrackPasswordExist` is a boolean — the password is never read back,
so a sync cannot diff passwords, only presence. Re-pushing an account is
the only way to change its code.

`AccountStop` is the per-account disable switch — the natural target for
"suspended" in a lifecycle sync, as distinct from deleting the account.

**Deleting is NOT yet captured.** `func=PSL_AA_TRA_PAG` with
`H_FLAG=Delete`, `AA_TrackID=<id>`, `AA_TRA_H_BOX=Public` and the paging
fields returns a `waitmove` envelope (`RedirectUrl` + `Interval`) but the
account survives, including after following the redirect. The delete func
is not present in `Integrated_main.js` and `a_authentication_track.xml` is
a data document rather than a form, so it needs another route — most
likely the bulk Authentication-Information import with replace semantics,
which is the preferred provisioning path anyway.

### 3.9.3 Per-account counters — **verified working**

The endpoint the Account Track Counter screen actually uses:

```http
POST /wcd/api/AppReqGetTrackCounterInfo
Content-Type: application/json

{"TrackCounterListCondition":{
   "TrackType":"Private",
   "ObtainCondition":{"Type":"IndexList","IndexRange":{"Start":1,"End":50}},
   "BackUp":"false"},
 "Token":"<rotating token>"}
```

**How it was found, because the method matters more than the result:**
every SPA screen loads its data from `api/AppReqGetCustomData/_<screenId>`
(`Integrated_content.js:spa_ajax_contentsData`), and each screen's own JS
is appended to its `spa_<screenId>.tmpl.html`, *after* the underscore
template. Reading `spa_003_002_TCR001.tmpl.html` gives the request, the
response paths, and the counter type vocabulary in one file, with no login
and no browser. That is where every "not supported" answer above should
have been checked first — the four dead endpoints were guesses at names,
and one of them was one word off.

**Windows are account-number ranges, not offsets.** `Start`/`End` are
account numbers; only accounts that exist within the range come back, and
a range past the last account returns `ArraySize 0` rather than an error —
so paging stops on an empty window, not on a short one. 51 accounts in one
call is accepted and 266 is refused with `GeneralRangeIllegal`; the
device's own screen asks for 50, which is the number to use.

**Response**, per account:

```json
{"TrackCounterList":{"ArraySize":"1","TrackCounter":{
  "TrackType":"Private","TrackID":"1",
  "TotalCounterList":{"ArraySize":"7","Counter":[
     {"Type":"Bw","Count":"0"},{"Type":"BwLarge","Count":"0"},
     {"Type":"Document","Count":"0"},{"Type":"Paper","Count":"0"},
     {"Type":"DuplexTotal","Count":"0"},{"Type":"PrintPageTotal","Count":"0"},
     {"Type":"BlackPrintPaper","Count":"0"}]},
  "CopyCounterList":{...},"PrintCounterList":{...},
  "ScanFaxCounterList":{...},"OtherTrackCounterList":{...},
  "TotalCounterData":{"Nin1TotalRate":"0.000","DuplexTotalRate":"0.000"}}}}
```

Single-element arrays collapse to an object, same trap as `TrackList`.

| List | Types seen on the 950i (mono) | Types the screen JS also handles |
|---|---|---|
| `TotalCounterList` | Bw, BwLarge, Document, Paper, DuplexTotal, PrintPageTotal, BlackPrintPaper | Total, TotalLarge, FullColor, BiColor, MonoColor (+`*Large`) |
| `CopyCounterList` | Bw, BwLarge, BlackPrintPaper | FullColor, BiColor, MonoColor (+`*Large`) |
| `PrintCounterList` | Bw, BwLarge, BlackPrintPaper | FullColor, BiColor (+`*Large`) |
| `ScanFaxCounterList` | PrintTotalBw, PrintLargeBw, FaxSend, DocumentReadTotal, DocumentReadLarge, BlackPrintPaper | PrintTotalColor, PrintLargeColor |
| `OtherTrackCounterList` | Nin12in1, Nin14in1, Nin1Other | — |

Two things about these numbers that decide how they can be used:

- **They are lifetime totals and only ever climb.** Read once, an account
  reports its whole history. Usage is the difference between two reads —
  see `app/copiers/account_counters.py`. The only thing that lowers one is
  the Counter Clear button on this screen (`func` posted by `ID_AA_CLR_CNT`
  — **not captured, and never to be run against a production counter**).
- **The colour modes are disjoint, and `*Large` is not.** Bw, FullColor,
  BiColor and MonoColor each count a page once; `BwLarge` re-counts
  large-format pages already in `Bw`, and `Paper`/`Document` count sheets
  and originals rather than pages. Summing everything double-counts.

Counters carry no timestamps, so the read interval is the resolution of
any usage derived from them: all that can honestly be said is that the
pages happened between two reads.

## 4. Not yet captured

These items from §5.1 of the task brief remain open, all blocked by Account
Track being off (§0) — there is nothing to list, count, or export yet:

- **Add an account** — form contract now captured (§3.9.1); the response
  shape and minimal-field set are not.
- **Bulk import** of Authentication Information (the multipart upload and its
  CSV column format) — the intended bulk-provisioning path.
- **Export** of the same category, to learn the exact columns.
- **Counter reset** request shape (capture only; never execute on a
  production counter).

No test account was created and no counter was reset during this capture.
