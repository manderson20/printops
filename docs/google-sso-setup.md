# Setting up Google Sign-In (SSO)

PrintOps can let staff log in with their Google Workspace account instead of
(or alongside) the local admin/password fallback. It's entirely configured
through the app itself — Settings → Integrations → Google Sign-In — there
are no environment variables to set. This doc is a click-by-click walkthrough
of the Google Cloud Console side, since that's where it's easy to miss a
step, plus the PrintOps-side fields.

## 1. Know your domain first

Before touching Google Cloud Console, open PrintOps in a browser and note
the exact URL you use to reach it, e.g. `https://print.example.org` — no
trailing slash, no path. You'll register that (with a path appended) as the
redirect URI in step 3, and PrintOps will separately prefill the bare domain
on its own settings page (step 4). If the two ever disagree — different
subdomain, `http` vs `https`, a stray trailing slash — sign-in fails with
`Error 400: redirect_uri_mismatch` on Google's screen, before it ever
reaches PrintOps. Google matches this string byte-for-byte; there's no
fuzzy matching.

## 2. Configure the OAuth consent screen (first time only)

Google Cloud requires this before it will let you create OAuth credentials
at all. In [Google Cloud Console](https://console.cloud.google.com/),
select (or create) the project for your organization, then:

1. Go to **APIs & Services → OAuth consent screen**.
2. **User type**: choose **Internal** if this project is inside a Google
   Workspace organization (restricts login to your org automatically, at
   the Google layer, on top of the domain check PrintOps does on its own).
   Pick **External** only if the project isn't tied to a Workspace org —
   in that case, publish the app (**Publishing status → Publish App**) or
   sign-in will fail for anyone not explicitly added as a test user.
3. Fill in the required fields (app name, user support email, developer
   contact email). You do not need to add any scopes here beyond the
   defaults — PrintOps only requests `openid email profile`, which are
   non-sensitive and don't trigger Google's verification review.
4. Save.

## 3. Create the OAuth client

1. Go to **APIs & Services → Credentials → Create Credentials → OAuth
   client ID**.
2. **Application type: Web application** — this matters; other types
   (Desktop app, Android, etc.) don't expose the redirect-URI field
   PrintOps needs. Use a separate client from any service account
   PrintOps already uses for MDM device sync (Mosyle/ClassGuard/Google
   Workspace) — different credential, different purpose.
3. You'll see two separate URL list fields — **use the second one, not the
   first**:
   - ~~Authorized JavaScript origins~~ — leave this empty. It's for
     browser-side JS OAuth flows and doesn't take a path; PrintOps doesn't
     use it. Putting your domain here instead of below is the single most
     common cause of `redirect_uri_mismatch`.
   - **Authorized redirect URIs** — click **+ Add URI** and enter your
     domain from step 1 *plus* `/auth/google/callback`, e.g.
     `https://print.example.org/auth/google/callback`. This is the one
     place in this whole setup where the full callback path belongs —
     everywhere on the PrintOps side you only enter the bare domain (step
     4).
4. Click **Create** (or **Save**, if editing an existing client). Google
   Cloud Console does not warn you if you navigate away without saving —
   after saving, reload the credentials page and re-open the client to
   confirm the URI is actually listed under Authorized redirect URIs.
5. Copy the generated **Client ID** and **Client Secret** — you'll paste
   those into PrintOps next.

## 4. Configure PrintOps

As an admin, go to **Settings → Integrations → Google Sign-In**:

- **Client ID** / **Client Secret** — from step 3.
- **Redirect Base URL** — prefilled from the address you're viewing the page
  at. Leave it alone unless that's not the domain staff actually sign in
  from. Do **not** append `/auth/google/callback` here — PrintOps adds that
  itself when it builds the URL Google redirects back to; adding it in this
  field produces a URL with the path doubled and sign-in fails.
- **Workspace Domain** — your Google Workspace domain (e.g. `example.org`).
  Only Google accounts on this domain can sign in; anyone else is rejected,
  even with a valid Google account.
- **Initial Admin Emails** — comma-separated addresses that become Admin the
  first time they sign in. Everyone else starts as Viewer; promote/demote
  afterward from the Users page.
- **Enabled** — turn on once the above is filled in. Off means the "Sign in
  with Google" button on the login page won't do anything.

Save. There's no separate test/dry-run step — try "Sign in with Google" from
the login page (use an incognito window if you're already signed in) and
confirm you land back in PrintOps signed in as the right role.

## 5. Troubleshooting

- **`Error 400: redirect_uri_mismatch` on Google's own screen** — this
  happens before PrintOps sees anything back from Google, so it's purely a
  Google Cloud Console vs. PrintOps mismatch. In order of likelihood:
  1. The URI was added under **Authorized JavaScript origins** instead of
     **Authorized redirect URIs** (step 3) — Google will silently accept
     it there without complaint, but it doesn't count for this flow.
  2. It's registered on a *different* OAuth client than the one whose
     Client ID is actually pasted into PrintOps — easy to do if you
     created more than one while testing.
  3. A mismatch in scheme (`http` vs `https`), subdomain, or a trailing
     slash between what's registered and PrintOps' Redirect Base URL
     setting.
  4. The client type isn't **Web application**.

  To debug directly: visit `https://<your-domain>/auth/google/login` and
  look at the URL you get redirected to — it has a `redirect_uri=` query
  parameter showing exactly what PrintOps sent. Compare that string
  character-for-character against what's listed in Google Cloud Console.
- **Redirected back into PrintOps with an error (not Google's screen)** —
  PrintOps' own checks failed: the account's email isn't verified, isn't on
  the configured Workspace domain, or the account was deactivated on the
  Users page.
- **"Google sign-in is not configured"** — the settings row is missing a
  Client ID, Client Secret, Redirect Base URL, or `enabled` is off.
- **Sign-in works for you but fails for everyone else ("access blocked" on
  Google's screen)** — the OAuth consent screen (step 2) is still in
  Testing status with an External user type; either switch to Internal (if
  this is a Workspace org project) or publish the app.
- The local admin/password login (tucked under "Use a local account
  instead" on the login page) always stays available as a break-glass
  fallback — Google Sign-In can never lock you out entirely.
