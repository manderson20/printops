# Lexmark XM3350 — admin interface capture

Attempted live against a **Lexmark XM3350** on 2026-08-17.

Device names and addresses are deliberately omitted — this is a public
repository. `DEVICE` stands for your own device's address throughout.

**Status: incomplete — admin login was rejected.** Everything below the
session section is unverified. See §3.

Method note: contracts were read out of the device's own EWS bundle
(`GET /js/source.js`, ~350 KB, unauthenticated) and confirmed against the
device where possible.

> `/webglue` is an unofficial internal contract. Pin behaviour to this
> document, fail loudly if a shape changes, and keep CSV import as the
> fallback (§9 of the task brief).

---

## 0. Headline finding — no PIN login method exists

**verified, unauthenticated**

```http
GET /webglue/rawcontent?c=LoginMethod&lang=en
```

```json
"LOGIN_METHODS_WITH_CREDS": {
  "lms": [{"credentials":["password"], "lmid":-1, "lmname":"Password",
           "lmtype":2, "selected":1, "text_id":65919}],
  "val": {"lmid":-1, "lmname":"Password", "lmtype":2}
}
```

The device offers exactly **one** login method: the built-in admin Password.
There is **no PIN method and no Local Accounts login method configured**.

This is the Lexmark counterpart to the Konica finding: the accounting/auth
infrastructure the connectors are meant to drive does not exist on the device
yet. Local Accounts (PIN type) must be created before `sync_users_to_device`
has anywhere to write, and PIN login must be enabled before staff can
identify themselves at the panel.

---

## 1. Endpoint map

**verified** (constants read from `source.js`)

```js
MAIN_CONTENT_PATH   = "/webglue/content"
RAW_CONTENT         = "/webglue/rawcontent"
DO_ACTION_PATH      = "/webglue/do_action"
DO_DELETE_PATH      = "/webglue/delete"
SESSION_CREATE_PATH = "/webglue/session/create"
SESSION_DESTROY_PATH= "/webglue/session/destroy"
SPNEGO_PATH         = "/webglue/session/spnego"
AUTO_LOGIN_PATH     = "/webglue/session/autologin"
NODE_DATA_PATH      = "/webglue/webui/nodedata/"
APPLICATIONS_PATH   = "/webglue/applications/"
WEBSERVICES_PATH    = "/webservices/"
```

Reads are `GET /webglue/rawcontent?c=<Component>&lang=en`. Writes go through
`/webglue/do_action` and `/webglue/delete` — **shapes not yet captured.**

## 2. Session model

### 2.1 Login request — **verified shape, rejected credentials**

From `source.js`:

```js
function login(d){
  var e={}, f={};
  addLoginData(e);                       // e.authtype = lmtype, e.authId = lmid
  ...each visible input... f[$(this).data("cred")] = val;
  e.creds = f;
  a.post(SESSION_CREATE_PATH, {data: JSON.stringify(e)});
}
```

So, for this device's single Password method (`lmtype:2, lmid:-1`):

```http
POST /webglue/session/create
Content-Type: application/x-www-form-urlencoded

data={"authtype":2,"authId":-1,"creds":{"password":"<admin password>"}}
```

(The task brief's PIN example — `{"authtype":3,"authId":<id>,
"creds":{"pin":"..."}}` — is the same envelope with a PIN-type method's
`lmtype`/`lmid`. No such method exists on this device today, see §0.)

### 2.2 Response — **verified**

Success sets three cookies from the response body: `sessionId`, `sessionKey`,
`sessionName` (`createSession()` in `source.js`). The client treats
`status == 0` as success.

**Observed on 2026-08-17 with the stored admin password:**

```json
{"status": 0, "sessionId": "J78qenyr7RfU3rYX", "sessionName": "",
 "error": "Unexpected login error. Contact your system administrator."}
```

Note the contradiction: `status: 0` (nominal success) but an error string,
an empty `sessionName`, and **no `sessionKey`**. Every subsequent
`rawcontent` read returned **HTTP 401**, and `session/destroy` returned 401.
So the device issued an unprivileged session — the login did not authenticate.

**Do not interpret `status == 0` alone as success.** A correct check is
`status == 0 && sessionKey present && no error`.

Cause is most likely a wrong/unset admin password rather than a wrong request
shape (the request matches the device's own JS exactly). Not retried with
variations — Lexmark also locks out after repeated failures.

### 2.3 Logout — **unverified**

```http
POST /webglue/session/destroy      (body: the session object)
```

## 3. Not yet captured

Blocked on a working admin login (§2.2). All of §5.2 of the task brief
remains open:

- **Is Device Quotas installed?** Not yet confirmed on any unit.
  `rawcontent?c=DeviceQuotas` returns 200 with `guestSession:1` and empty
  nodes when unauthenticated — that is *not* evidence the app is present, it
  is what every component returns to a guest. Components probed
  unauthenticated, all identical guest stubs: `Apps`, `AppsMgmt`,
  `DeviceQuotas`, `Accounting`, `UsageCounters`, `Security`.
  Only `Reports` returns real guest-readable content (2.4 KB).
- **Device Quotas per-user usage CSV export** — the request that produces it,
  and the column list.
- **Add a Local Account (PIN type)** — the `do_action` write shape.
- **The 250-account cap** and per-building scoping behaviour.

No test account was created during this attempt.

## 4. Credential note

The XM3350's sole login method declares `credentials: ["password"]`, so its
admin login has **no username field at all** — `web_login_username` is
correctly left blank for these devices, and supplying a username cannot fix
a rejected login. The password is the only variable.
