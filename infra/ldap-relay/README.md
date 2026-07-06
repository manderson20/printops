# LDAP address-book relay

Lets office copiers do scan-to-email address-book lookups against PrintOps
over LDAP, instead of each copier holding its own direct LDAP connection to
Google Workspace (Secure LDAP). Backs the **Settings → LDAP Relay** page
and each printer's **LDAP Address Book** panel in the web app.

Search results come straight from the Google Workspace roster PrintOps
already syncs every 15 minutes for job attribution
(`GoogleWorkspaceUser` — see `apps/api/app/integrations/google_workspace.py`)
— no live call to Google happens per bind or per search.

## Why a separate service, not part of printops-api?

This is the first raw (non-HTTP) network protocol printops-api serves.
Rather than hand-writing LDAP's BER/ASN.1 wire protocol (real
correctness/security surface to get wrong) or wedging Twisted's reactor
into printops-api's asyncio event loop, this is its own small process,
using [ldaptor](https://github.com/twisted/ldaptor) (a mature, purpose-built
LDAP server library) and its own Twisted reactor — no interaction with
printops-api's asyncio loop at all. `server.py` overrides only
`handle_LDAPBindRequest`/`handle_LDAPSearchRequest` directly (bypassing
ldaptor's `IConnectedLDAPEntry` directory-tree machinery entirely — an
address-book lookup needs no directory tree, just translating two request
types into two HTTP calls) and explicitly rejects Add/Delete/Modify/
ModifyDN/Compare (`LDAPUnwillingToPerform`) — a deliberately narrow
protocol surface, matching what a real address-book client actually needs
(Bind, Search, Unbind).

Every bind and search is a plain HTTP call to printops-api's internal,
backend-token-gated endpoints (`app/routers/internal.py`:
`/api/v1/internal/ldap/bind`, `/ldap/search`, `/ldap/settings`) — same
trust boundary the CUPS backend script already uses
(`app/deps.py:verify_backend_token`). All the actual logic (which printer's
credentials are valid, what roster data to return) lives there, in Python
this repo already tests with pytest; `server.py` is just the protocol
translator.

## Install (one-time, on the box running printops-api)

```bash
cd infra/ldap-relay
uv sync
sudo cp printops-ldap-relay.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now printops-ldap-relay.service
```

Nothing actually serves address-book data until an admin turns the relay on
in **Settings → LDAP Relay** *and* enables it for at least one printer with
bind credentials set on that printer's detail page — both default off.

## Configuring a copier

On the copier's own LDAP address-book settings:
- **Server**: this box's hostname/IP, **port** from Settings → LDAP Relay
  (389 by default — plain LDAP, not LDAPS; see the security note below).
- **Bind DN / username**: the printer's `ldap_bind_username`
  (printer detail page → LDAP Address Book panel).
- **Bind password**: whatever was set alongside it there.
- **Base DN**: the `base_dn` from Settings → LDAP Relay (e.g.
  `dc=yourdistrict,dc=org`) — entries are served under `ou=people,<base_dn>`.
- **Search attributes**: `cn` (name) and `mail` (email address) — the only
  two attributes served (see `_translate_filter` in `server.py`).

## Security note: plain LDAP, not LDAPS

This serves plain LDAP (port 389), matching this system's existing
security posture — IPP itself is optional-TLS per printer on the same
trusted internal network (`Printer.use_tls`). Bind credentials and roster
data (name/email) travel in cleartext on that network as a result. If your
network segmentation isn't trusted enough for that, don't deploy this yet;
StartTLS/LDAPS would be a real follow-up, not implemented here.

## Manual verification

There's no automated test coverage for `server.py` itself (a separate
process from printops-api's pytest suite) — the bind/search *logic* is
fully covered there (`apps/api/tests/test_ldap_internal_api.py`), but the
LDAP protocol translation needs a real client. `uv sync --group dev` pulls
in `ldap3` for exactly this:

```python
from ldap3 import Server, Connection, SUBTREE

server = Server("localhost", port=389)
conn = Connection(server, user="<bind-username>", password="<bind-password>")
print(conn.bind())  # True on success

conn.search("ou=people,<base-dn>", "(cn=*jane*)", SUBTREE, attributes=["cn", "mail"])
for entry in conn.entries:
    print(entry.entry_dn, entry.cn.value, entry.mail.value)
```

## Checking it's running

```bash
systemctl status printops-ldap-relay.service
journalctl -u printops-ldap-relay.service -n 50
```
