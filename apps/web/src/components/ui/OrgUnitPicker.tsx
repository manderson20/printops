"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/Badge";

type Props = {
  /** Currently selected OU paths. */
  value: string[];
  onChange: (paths: string[]) => void;
  /** Loader for the selectable OUs — pass the staff-scoped or all-OUs client fn. */
  load: () => Promise<string[]>;
  disabled?: boolean;
  /** Wording for the "add" dropdown's placeholder option. */
  addLabel?: string;
  emptyLabel?: string;
};

/**
 * Pick Organizational Units from the ones Google Workspace actually synced,
 * rather than typing paths by hand.
 *
 * A free-text OU path fails silently when mistyped: "/Employees/Elementary"
 * instead of "/Employees/Elementary School" matches nobody, and nothing
 * about the resulting empty filter says why. Every value here comes from
 * GoogleWorkspaceUser.org_unit_path, so a selection is always a real OU.
 *
 * Selected paths that are no longer in the synced list are still rendered
 * (flagged "not synced") instead of being dropped — an OU that was emptied
 * or renamed in Workspace shouldn't silently disappear from a saved filter
 * the next time someone opens the page.
 */
export function OrgUnitPicker({
  value,
  onChange,
  load,
  disabled = false,
  addLabel = "Add an organizational unit…",
  emptyLabel = "None selected.",
}: Props) {
  const [available, setAvailable] = useState<string[] | null>(null);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    load()
      .then((paths) => {
        if (!cancelled) setAvailable(paths);
      })
      .catch(() => {
        if (!cancelled) setLoadError(true);
      });
    return () => {
      cancelled = true;
    };
    // `load` is passed as a stable module-level function by both callers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectable = (available ?? []).filter((path) => !value.includes(path));

  return (
    <div className="flex flex-col gap-2">
      {value.length === 0 ? (
        <span className="text-xs text-zinc-500">{emptyLabel}</span>
      ) : (
        <ul className="flex flex-col gap-1">
          {value.map((path) => {
            const unknown = available !== null && !available.includes(path);
            return (
              <li key={path} className="flex items-center gap-2">
                <code className="text-[11px]">{path}</code>
                {unknown && (
                  <Badge tone="warning" className="font-sans">
                    not synced
                  </Badge>
                )}
                {!disabled && (
                  <button
                    type="button"
                    aria-label={`Remove ${path}`}
                    onClick={() => onChange(value.filter((p) => p !== path))}
                    className="text-xs text-zinc-500 hover:text-red-600"
                  >
                    Remove
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {!disabled && (
        <select
          value=""
          disabled={available === null || selectable.length === 0}
          onChange={(e) => {
            if (e.target.value) onChange([...value, e.target.value]);
          }}
          className="rounded border border-black/[.15] bg-transparent px-2 py-1 text-xs disabled:opacity-60 dark:border-white/[.2]"
        >
          <option value="">
            {loadError
              ? "Could not load organizational units"
              : available === null
                ? "Loading…"
                : selectable.length === 0
                  ? "No more organizational units"
                  : addLabel}
          </option>
          {selectable.map((path) => (
            <option key={path} value={path}>
              {path}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}
