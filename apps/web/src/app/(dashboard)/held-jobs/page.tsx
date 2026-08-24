import { redirect } from "next/navigation";

/** Held jobs are not a place of their own any more.
 *
 * A held job is an ordinary job with status "held" — the same row, in the same
 * table, already returned by the same endpoint the Jobs page uses. Two menu
 * items were two views of one list, and an admin had to know which page
 * answered which question about the same job.
 *
 * Kept as a redirect rather than deleted so existing links and bookmarks land
 * on the filtered view instead of a 404.
 */
export default function HeldJobsRedirect() {
  redirect("/jobs?status=held");
}
