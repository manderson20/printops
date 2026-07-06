#!/usr/bin/env python3
"""LDAP address-book relay for copiers.

A thin ldaptor-based LDAP server that translates Bind and Search requests
into calls against printops-api's internal /ldap/bind and /ldap/search
endpoints (app/routers/internal.py) — so copiers do scan-to-email
address-book lookups against PrintOps instead of holding their own direct
LDAP connection to Google Workspace. Search results come straight from
PrintOps's already-synced Google Workspace roster (at most ~15 minutes
stale); no live Google call happens per search or per bind.

Runs as its own process/systemd service (printops-ldap-relay.service) with
its own Twisted reactor — deliberately not integrated into printops-api's
asyncio event loop (see the plan this was built from). Talks to
printops-api over plain HTTP via a blocking httpx.Client wrapped in
twisted.internet.threads.deferToThread, the standard Twisted idiom for
calling blocking I/O from inside the reactor — mirrors
infra/cups/backends/printops's own use of a plain synchronous HTTP client
for the same kind of internal API call.

Deliberately narrow protocol surface: only simple Bind, Search, and Unbind
are supported. Add/Delete/Modify/ModifyDN/Compare are explicitly rejected
(LDAPUnwillingToPerform) rather than left to fall through to ldaptor's
IConnectedLDAPEntry directory-tree machinery, which this server never
configures at all — an address-book lookup needs no directory tree, just
translating two request types into two HTTP calls.
"""

import logging
import os

import httpx
from ldaptor.protocols import pureldap
from ldaptor.protocols.ldap import ldapserver, ldaperrors
from twisted.internet import protocol, reactor, threads
from twisted.python import log as twisted_log

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("printops-ldap-relay")

API_BASE = os.environ.get("PRINTOPS_API_BASE", "http://localhost:8000")
ENV_FILE = os.environ.get(
    "PRINTOPS_ENV_FILE", "/home/itadmin/printops/apps/api/.env"
)
# Refreshed on every bind/search — cheap (LdapRelaySettings is a single-row
# lookup) and means a base-DN or enabled-flag change made in Settings ->
# LDAP Relay takes effect on this relay's very next request, no restart of
# this service needed.
LDAP_PORT_ENV_OVERRIDE = os.environ.get("PRINTOPS_LDAP_PORT")


def load_backend_token() -> str:
    with open(ENV_FILE) as f:
        for line in f:
            if line.startswith("PRINTOPS_BACKEND_TOKEN="):
                return line.strip().split("=", 1)[1]
    raise RuntimeError(f"PRINTOPS_BACKEND_TOKEN not found in {ENV_FILE}")


_client = httpx.Client(
    base_url=API_BASE,
    headers={"X-Backend-Token": load_backend_token()},
    timeout=10.0,
)


def _api_post(path: str, json: dict) -> dict:
    response = _client.post(path, json=json)
    response.raise_for_status()
    return response.json()


def _get_relay_settings() -> dict:
    response = _client.get("/api/v1/internal/ldap/settings")
    response.raise_for_status()
    return response.json()


def _decode(value) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else value


def _translate_filter(filter_obj) -> tuple[str | None, str | None, str | None]:
    """Reduces an incoming LDAP filter to (attr, type, value) for the two
    attributes/kinds the internal search endpoint understands (equality or
    substring on cn/mail) — anything else (other attributes, OR, NOT, a
    substrings filter with multiple parts) returns (None, None, None),
    which the caller treats as "no matches" rather than an error. A real
    copier address-book search is realistically always one of:
    `(mail=exact@value)`, `(cn=*typed-so-far*)`, or one of those wrapped in
    `(&(objectClass=...)(...))` — this covers all three."""
    if isinstance(filter_obj, pureldap.LDAPFilter_and):
        for child in filter_obj:
            attr, ftype, value = _translate_filter(child)
            if attr is not None:
                return attr, ftype, value
        return None, None, None

    if isinstance(filter_obj, pureldap.LDAPFilter_equalityMatch):
        attr = _decode(filter_obj.attributeDesc.value).lower()
        if attr not in ("cn", "mail"):
            return None, None, None
        return attr, "equality", _decode(filter_obj.assertionValue.value)

    if isinstance(filter_obj, pureldap.LDAPFilter_substrings):
        attr = _decode(filter_obj.type).lower()
        if attr not in ("cn", "mail"):
            return None, None, None
        # Collapse initial/any/final into one "contains" value — matches
        # the ILIKE %value% substring search the internal endpoint does.
        parts = [_decode(part.value) for part in filter_obj.substrings]
        return attr, "substring", "".join(parts)

    return None, None, None


class AddressBookLDAPServer(ldapserver.LDAPServer):
    debug = False

    def handle_LDAPBindRequest(self, request, controls, reply):
        self.checkControls(controls)
        if request.version != 3:
            raise ldaperrors.LDAPProtocolError(f"Version {request.version} not supported")

        dn = _decode(request.dn)
        auth = request.auth
        password = _decode(auth) if isinstance(auth, (bytes, str)) else None
        if not dn or not password:
            # Anonymous bind (blank DN/password) isn't supported — this
            # relay only serves bound, per-printer-credentialed clients.
            raise ldaperrors.LDAPInvalidCredentials()

        def _bind():
            return _api_post(
                "/api/v1/internal/ldap/bind",
                {"bind_identifier": dn, "password": password},
            )

        d = threads.deferToThread(_bind)

        def _handle(result):
            if not result.get("success"):
                raise ldaperrors.LDAPInvalidCredentials()
            self.boundUser = result.get("printer_id")
            return pureldap.LDAPBindResponse(resultCode=ldaperrors.Success.resultCode, matchedDN=dn)

        d.addCallback(_handle)
        return d

    def handle_LDAPSearchRequest(self, request, controls, reply):
        self.checkControls(controls)

        if (
            request.baseObject == b""
            and request.scope == pureldap.LDAP_SCOPE_baseObject
            and request.filter == pureldap.LDAPFilter_present("objectClass")
        ):

            def _root_dse():
                return _get_relay_settings()

            d = threads.deferToThread(_root_dse)

            def _reply_root_dse(settings):
                reply(
                    pureldap.LDAPSearchResultEntry(
                        objectName="",
                        attributes=[
                            ("supportedLDAPVersion", ["3"]),
                            ("namingContexts", [settings["base_dn"]]),
                        ],
                    )
                )
                return pureldap.LDAPSearchResultDone(resultCode=ldaperrors.Success.resultCode)

            d.addCallback(_reply_root_dse)
            return d

        if self.boundUser is None:
            raise ldaperrors.LDAPInsufficientAccessRights("Bind required before search.")

        filter_attr, filter_type, filter_value = _translate_filter(request.filter)
        if filter_attr is None:
            return pureldap.LDAPSearchResultDone(resultCode=ldaperrors.Success.resultCode)

        def _search():
            return _api_post(
                "/api/v1/internal/ldap/search",
                {"filter_attr": filter_attr, "filter_type": filter_type, "filter_value": filter_value},
            )

        d = threads.deferToThread(_search)

        def _handle(result):
            for entry in result.get("entries", []):
                attributes = [
                    (b"objectClass", [b"inetOrgPerson", b"person"]),
                    (b"cn", [entry["cn"].encode()]),
                    (b"mail", [entry["mail"].encode()]),
                ]
                if entry.get("employee_number"):
                    attributes.append((b"employeeNumber", [entry["employee_number"].encode()]))
                reply(pureldap.LDAPSearchResultEntry(objectName=entry["dn"], attributes=attributes))
            return pureldap.LDAPSearchResultDone(resultCode=ldaperrors.Success.resultCode)

        d.addCallback(_handle)
        return d

    def _reject(self, request, controls, reply):
        raise ldaperrors.LDAPUnwillingToPerform("Not supported by the address-book relay.")

    handle_LDAPAddRequest = _reject
    handle_LDAPDelRequest = _reject
    handle_LDAPModifyRequest = _reject
    handle_LDAPModifyDNRequest = _reject
    handle_LDAPCompareRequest = _reject


class AddressBookLDAPServerFactory(protocol.ServerFactory):
    protocol = AddressBookLDAPServer


def main() -> None:
    twisted_log.PythonLoggingObserver(loggerName="printops-ldap-relay").start()
    settings = _get_relay_settings()
    port = int(LDAP_PORT_ENV_OVERRIDE) if LDAP_PORT_ENV_OVERRIDE else settings["port"]
    # Empty string (default) binds every interface, as a real deployment
    # needs (copiers reach this from other hosts/VLANs). Set
    # PRINTOPS_LDAP_BIND_INTERFACE=127.0.0.1 for a loopback-only manual
    # test that never actually exposes the service to the network.
    interface = os.environ.get("PRINTOPS_LDAP_BIND_INTERFACE", "")
    if not settings["enabled"]:
        logger.warning(
            "LdapRelaySettings.enabled is False — listening anyway (per-request bind/search "
            "still checks this and will refuse everything), but nothing will actually work "
            "until an admin turns it on in Settings -> LDAP Relay."
        )
    reactor.listenTCP(port, AddressBookLDAPServerFactory(), interface=interface)
    logger.info(
        "printops-ldap-relay listening on %s:%d (base DN %r)",
        interface or "0.0.0.0", port, settings["base_dn"],
    )
    reactor.run()


if __name__ == "__main__":
    main()
