# CUPS / IPP (placeholder)

This service currently just proves a CUPS container boots and IPP (port 631) is
reachable — no printers, AirPrint sharing, or Avahi/mDNS configuration exist yet.

Future work here (see ARCHITECTURE.md §3-4): make this container (or its
replacement) the client-facing AirPrint/IPP-proxy endpoint, which likely
requires `network_mode: host` for Avahi/Bonjour advertisement — not yet
configured, since no discovery/proxy code exists in this pass.
