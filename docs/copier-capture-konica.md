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
| `MFP.AuthSetting.CommonMode.NoAuthPrintOn` | `"true"` | Printing without authentication is **allowed**. Turning on Account Track will not break PrintOps' print path while this stays `true`. Do not change it in the same sitting. |
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

`get_user_accounting` therefore needs a different source — the counter
export from the classic `a_*.xml` admin pages, or the Track Report — which
is **not yet captured** (the device has no account data to export while
Account Track is off).

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

## 4. Not yet captured

These items from §5.1 of the task brief remain open, all blocked by Account
Track being off (§0) — there is nothing to list, count, or export yet:

- **Add an account** (the write call + token handling).
- **Bulk import** of Authentication Information (the multipart upload and its
  CSV column format) — the intended bulk-provisioning path.
- **Export** of the same category, to learn the exact columns.
- **Per-account counter read** — needs a source other than
  `AppReqGetCounterInfo` (§3.2).
- **Counter reset** request shape (capture only; never execute on a
  production counter).

No test account was created and no counter was reset during this capture.
