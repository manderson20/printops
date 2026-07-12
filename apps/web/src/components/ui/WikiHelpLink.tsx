const WIKI_BASE_URL = "https://github.com/manderson20/printops/wiki";

type Props = {
  /** Wiki page slug, e.g. "Printers" or "Settings-SNMP". */
  page: string;
  /** Optional heading anchor on that page, e.g. "release-tab" (no leading #). */
  anchor?: string;
  className?: string;
};

export function WikiHelpLink({ page, anchor, className = "" }: Props) {
  const href = `${WIKI_BASE_URL}/${page}${anchor ? `#${anchor}` : ""}`;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={`inline-flex items-center gap-1 text-xs font-medium text-zinc-400 hover:text-zinc-600 dark:text-zinc-500 dark:hover:text-zinc-300 ${className}`}
      title="Open the wiki page for this screen"
    >
      <span
        aria-hidden="true"
        className="flex h-4 w-4 items-center justify-center rounded-full border border-current text-[10px] leading-none"
      >
        ?
      </span>
      Help
    </a>
  );
}
