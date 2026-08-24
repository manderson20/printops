import { redirect } from "next/navigation";

/** Copiers are not created here any more.
 *
 * A copier is one half of a machine that already exists as a printer, so it is
 * created by switching copy tracking on from that printer's Copier tab. This
 * page used to allow a copier with no printer behind it, which left a record
 * that no machine page could reach.
 */
export default function NewMfpDeviceRedirect() {
  redirect("/printers");
}
