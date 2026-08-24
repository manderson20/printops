import { redirect } from "next/navigation";

/** Copiers are no longer a place of their own.
 *
 * Every copier in this district is also a printer, and having both meant one
 * machine appeared twice — the same connection, model, location and meter
 * described in two places, with an admin needing to know which page answered
 * which question. A machine's copier side now lives on the machine's own page,
 * behind the "Track copies" toggle.
 *
 * This route stays as a redirect rather than a 404 so old links, bookmarks and
 * anything anyone wrote down still land somewhere useful.
 */
export default function CopiersMoved() {
  redirect("/printers");
}
