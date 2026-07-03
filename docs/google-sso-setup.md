# Setting up Google Sign-In (SSO)

PrintOps can let staff log in with their Google Workspace account instead of
(or alongside) the local admin/password fallback. It's entirely configured
through the app itself — Settings → Integrations → Google Sign-In — there
are no environment variables to set. This doc covers the one-time Google
Cloud Console setup and the fields on that page.

## 1. Know your domain first

Before touching Google Cloud Console, open PrintOps in a browser and note
the exact URL you use to reach it, e.g. `https://print.example.org`. You'll
register that as the redirect URI in the next step, and PrintOps will
prefill the same value on the settings page (see step 3) — if the two ever
disagree, sign-in fails with `redirect_uri_mismatch` on Google's screen.

## 2. Create an OAuth client in Google Cloud Console

1. In [Google Cloud Console](https://console.cloud.google.com/), select (or
   create) the project for your organization.
2. Go to **APIs & Services → Credentials → Create Credentials → OAuth
   client ID**.
3. Application type: **Web application**. Use a separate client from any
   service account PrintOps already uses for MDM device sync
   (Mosyle/ClassGuard/Google Workspace) — different credential, different
   purpose.
4. Under **Authorized redirect URIs**, add your domain from step 1 *plus*
   `/auth/google/callback` — e.g. `https://print.example.org/auth/google/callback`.
   This is the one place the full callback path belongs; everywhere else in
   PrintOps you only enter the bare domain.
5. Save, then copy the generated **Client ID** and **Client Secret** — you'll
   paste those into PrintOps next.

## 3. Configure PrintOps

As an admin, go to **Settings → Integrations → Google Sign-In**:

- **Client ID** / **Client Secret** — from step 2.
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

## 4. Troubleshooting

- **Error on Google's own screen (`redirect_uri_mismatch`)** — the Redirect
  Base URL in PrintOps doesn't match what's registered in Google Cloud
  Console, or the field has a path/trailing slash on it. It should be just
  the scheme + domain, nothing after.
- **Redirected back into PrintOps with an error** — PrintOps' own checks
  failed: the account's email isn't verified, isn't on the configured
  Workspace domain, or the account was deactivated on the Users page.
- **"Google sign-in is not configured"** — the settings row is missing a
  Client ID, Client Secret, Redirect Base URL, or `enabled` is off.
- The local admin/password login (tucked under "Use a local account
  instead" on the login page) always stays available as a break-glass
  fallback — Google Sign-In can never lock you out entirely.
