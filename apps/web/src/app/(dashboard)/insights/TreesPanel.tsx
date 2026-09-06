"use client";

import { useEffect } from "react";

/** Local rather than imported from explained-ui, which imports *this*
 *  module — a cycle that bundles happily and then resolves to undefined
 *  depending on which side is evaluated first. One small function is
 *  cheaper than that class of bug. */
function formatNumber(value: number): string {
  return Math.round(value).toLocaleString();
}

/** How many sheets one tree yields, derived rather than hardcoded.
 *
 *  The server sends both the sheet count and the tree figure, so the
 *  divisor is `sheets / trees` — which means this panel cannot drift from
 *  the constant the API actually used. The previous caption said "8,300
 *  sheets per tree" while the cost report said 8,333, and neither was
 *  wrong on its own; they were two copies of one number. */
function sheetsPerTree(sheets: number, trees: number): number {
  return trees > 0 ? sheets / trees : 0;
}

/** One stylised tree. Deliberately not a photograph or an emoji: at the
 *  sizes this draws — sixty of them on a lobby screen — a shape reads and
 *  a picture turns to mush, and an emoji renders differently on every
 *  platform the district owns. */
function Tree({ scale = 1, faded = false }: { scale?: number; faded?: boolean }) {
  return (
    <svg
      viewBox="0 0 40 60"
      className="h-full w-full"
      style={{ opacity: faded ? 0.35 : 1 }}
      aria-hidden="true"
    >
      <g transform={`translate(20 60) scale(${scale}) translate(-20 -60)`}>
        <rect x="17.5" y="40" width="5" height="20" rx="1.5" fill="#8b5e3c" />
        <circle cx="20" cy="34" r="11" fill="#2f7a4d" />
        <circle cx="12" cy="28" r="8.5" fill="#3d9160" />
        <circle cx="28" cy="28" r="8.5" fill="#3d9160" />
        <circle cx="20" cy="21" r="9.5" fill="#4aa870" />
      </g>
    </svg>
  );
}

/** A ream drawn as a stack of paper, filled to `fraction`.
 *
 *  This is the personal page's unit, and the reason the two pages differ.
 *  The average person here is at 0.046 trees — four and a half percent of
 *  one — and a panel that opens to reveal a sliver of a tree tells them
 *  nothing they can hold. A ream is a thing they carry from the supply
 *  closet. */
function Ream({ fraction }: { fraction: number }) {
  const filled = Math.max(0, Math.min(1, fraction));
  const sheets = 14;
  const lit = Math.round(filled * sheets);
  return (
    <svg viewBox="0 0 120 90" className="h-full w-full" aria-hidden="true">
      {Array.from({ length: sheets }).map((_, index) => {
        const y = 76 - index * 5;
        const isLit = index < lit;
        return (
          <rect
            key={index}
            x={14 + index * 0.6}
            y={y}
            width="92"
            height="4.2"
            rx="1"
            fill={isLit ? "#4a7bd8" : "#e4e6eb"}
            stroke={isLit ? "#3861ad" : "#cfd3da"}
            strokeWidth="0.6"
          />
        );
      })}
    </svg>
  );
}

export type TreesPanelProps = {
  /** Trees' worth of wood — the equivalency's own value. */
  trees: number;
  /** Sheets behind it, so the arithmetic can be shown rather than asserted. */
  sheets: number;
  /** The district's view draws a stand; a person's draws reams. */
  collective: boolean;
  sourceUrl: string | null;
  onClose: () => void;
};

export function TreesPanel({
  trees,
  sheets,
  collective,
  sourceUrl,
  onClose,
}: TreesPanelProps) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const perTree = sheetsPerTree(sheets, trees);
  const whole = Math.floor(trees);
  const remainder = trees - whole;
  // Past this many the picture stops being countable and starts being
  // wallpaper, which is the opposite of the point.
  const drawn = Math.min(whole, 60);
  const undrawn = whole - drawn;

  // How many people printing at this rate it takes to reach one tree.
  // Only meaningful below a whole tree, which on the personal page is
  // almost everybody.
  const peoplePerTree = trees > 0 ? Math.round(1 / trees) : 0;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="What that is in trees"
      onClick={onClose}
    >
      <div
        className="max-h-full w-full max-w-3xl overflow-y-auto rounded-2xl bg-white p-6 shadow-xl dark:bg-zinc-950"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold tracking-tight">
              {collective ? "Our paper, as trees" : "Your paper, as reams"}
            </h2>
            <p className="mt-1 text-sm text-zinc-500">
              {formatNumber(sheets)} sheets ÷ {formatNumber(Math.round(perTree))} sheets a
              tree = <strong>{trees.toFixed(2)} trees</strong>&rsquo; worth of wood.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 rounded-full border border-black/[.12] px-3 py-1.5 text-sm font-medium transition-colors hover:bg-black/[.04] dark:border-white/[.2] dark:hover:bg-white/[.06]"
          >
            Close
          </button>
        </div>

        {collective ? (
          <>
            <div className="flex flex-wrap items-end gap-1 rounded-xl bg-emerald-50/60 p-4 dark:bg-emerald-950/20">
              {Array.from({ length: drawn }).map((_, index) => (
                <div key={index} className="h-20 w-12">
                  <Tree />
                </div>
              ))}
              {remainder > 0.02 && (
                <div className="h-20 w-12" title="the part of another tree">
                  {/* Drawn small rather than clipped: a tree cut off at
                      40% of its height reads as a drawing error, where a
                      smaller tree reads as what is left over. */}
                  <Tree scale={Math.max(0.35, remainder)} faded />
                </div>
              )}
            </div>
            <p className="mt-3 text-sm text-zinc-600 dark:text-zinc-400">
              {whole === 0
                ? "Not yet a whole tree."
                : `${whole} whole ${whole === 1 ? "tree" : "trees"}`}
              {remainder > 0.02 && whole > 0 && `, and ${Math.round(remainder * 100)}% of another`}
              {undrawn > 0 && ` — ${formatNumber(undrawn)} more than can be drawn here`}.
            </p>
          </>
        ) : (
          <>
            <div className="flex items-end gap-6 rounded-xl bg-sky-50/60 p-4 dark:bg-sky-950/20">
              <div className="h-32 w-44 shrink-0">
                {/* Filled to the whole amount when it is under one ream,
                    and full above that — the number beside it carries the
                    count. Drawing the remainder of the *last* ream would
                    show 40% next to a figure reading 6.40, which invites
                    exactly the wrong reading. */}
                <Ream fraction={Math.min(sheets / 500, 1)} />
              </div>
              <div className="min-w-0">
                <p className="text-3xl font-semibold tabular-nums tracking-tight">
                  {(sheets / 500).toFixed(2)}
                </p>
                <p className="text-sm text-zinc-600 dark:text-zinc-400">
                  reams of paper — the packs in the supply closet, 500 sheets each.
                </p>
              </div>
            </div>
            <p className="mt-3 text-sm text-zinc-600 dark:text-zinc-400">
              {trees >= 1
                ? `That is ${trees.toFixed(2)} trees' worth of wood.`
                : `That is ${(trees * 100).toFixed(1)}% of one tree — it takes about ${formatNumber(
                    peoplePerTree,
                  )} people printing like you to use a whole one.`}
            </p>
          </>
        )}

        <div className="mt-5 rounded-xl border border-black/[.08] p-4 text-xs leading-relaxed text-zinc-500 dark:border-white/[.145]">
          <p>
            <strong>Wood used, not trees felled.</strong> Most office paper comes from
            managed timber plantations and recycled content, so this is a way of
            expressing an amount of wood in units people can picture — not a count of
            trees that were standing and now aren&rsquo;t.
          </p>
          <p className="mt-2">
            One ton of virgin uncoated office paper takes about 24 trees 40 ft tall and
            6&ndash;8 inches across. A ton is roughly 400 reams, so a tree is about{" "}
            {formatNumber(Math.round(perTree))} sheets. Tree size moves this a long way:
            one large pine has been put at 119,100 sheets, fourteen times as many.
          </p>
          {sourceUrl && (
            <p className="mt-2">
              <a
                href={sourceUrl}
                target="_blank"
                rel="noreferrer noopener"
                className="underline underline-offset-2 hover:text-zinc-700 dark:hover:text-zinc-300"
              >
                Source: Conservatree, trees per ton of paper
              </a>
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
