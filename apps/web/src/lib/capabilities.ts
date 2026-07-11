import type { Capabilities } from "@/lib/api";

export function capabilityBadges(caps: Capabilities | null): string[] {
  if (!caps) return [];
  const badges: string[] = [];
  if (caps.duplex_supported) badges.push("Duplex");
  if (caps.color_supported) badges.push("Color");
  if (caps.pin_printing_supported) badges.push("PIN Release");
  if (caps.accounting_supported) badges.push("Accounting Codes");
  if (caps.tls_supported) badges.push("IPPS Supported");
  for (const finishing of caps.finishings) {
    badges.push(
      finishing
        .split("-")
        .map((word) => word[0].toUpperCase() + word.slice(1))
        .join(" "),
    );
  }
  return badges;
}
