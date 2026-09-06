"use client";

import { useEffect, useRef } from "react";

/** Local rather than imported from explained-ui, which imports *this*
 *  module — a cycle that bundles happily and then resolves to undefined
 *  depending on which side is evaluated first. One small function is
 *  cheaper than that class of bug. */
function formatNumber(value: number): string {
  return Math.round(value).toLocaleString();
}

/** Deterministic pseudo-randomness.
 *
 *  A forest that rearranges itself every time the period picker moves is
 *  a distraction, and one that reshuffles on a lobby screen every poll is
 *  worse. Same index, same tree, always. */
function jitter(index: number, salt: number, lo: number, hi: number): number {
  const h = (index * 2654435761 + salt * 40503) % 4294967296;
  return lo + (h / 4294967296) * (hi - lo);
}

type Placed = {
  index: number;
  kind: number;
  x: number;
  base: number;
  scale: number;
  depth: number;
};

const GROUND_Y = 258;
const CANVAS_W = 900;
const CANVAS_H = 300;

/** Where each tree stands.
 *
 *  Depth does the work. A tree further back sits higher on the canvas, is
 *  smaller, and is drawn first and more faintly — so a tree in front of it
 *  simply covers it. The first version outlined every tree in the
 *  background colour to separate them, which carved slices out of
 *  whatever was behind; contrast separates them without eating anything.
 *
 *  Horizontal positions are jittered rather than evenly spaced. Even
 *  spacing is an avenue; a forest is trees that grew where they landed. */
function place(count: number, remainder: number): Placed[] {
  const trees: Placed[] = [];
  // A few trees on a canvas built for sixty is a field with something in
  // the corner, so they grow to fill it.
  const roominess = count < 12 ? 1 + (12 - count) * 0.05 : 1;

  for (let i = 0; i < count; i += 1) {
    const depth = jitter(i, 7, 0, 1);
    // Each tree gets its own band, and wanders only inside it.
    //
    // Two bugs live here, one on each end. `i / (count - 1)` puts the
    // first tree at exactly zero and jitter then pushes it off the left
    // edge — at three trees the scene drew two. Clamping the result
    // instead collapses bands onto the margin at the other end: at sixty
    // trees indices 0, 1 and 2 all landed on the same x, and the larger
    // one drawn later hid the others, losing trees from a scene whose
    // whole job is that you can count them.
    //
    // Band centres are never 0 or 1, and the wander is a fraction of a
    // band rather than a fraction of the canvas — so it shrinks as the
    // forest thickens, and no tree can reach its neighbour's centre or
    // leave the canvas. No clamp needed.
    const band = (i + 0.5) / count;
    const wander = 0.32 / count;
    const span = band + jitter(i, 3, -wander, wander);
    trees.push({
      index: i,
      kind: i % 6,
      x: 48 + (CANVAS_W - 96) * span,
      base: GROUND_Y - 72 * (1 - depth),
      scale: (0.52 + 0.6 * depth) * roominess,
      depth,
    });
  }
  if (remainder > 0.02) {
    // The part of another tree, as a sapling at the very front. Drawn
    // small rather than clipped: a tree cut off at 38% of its height
    // reads as a rendering fault, a young one reads as what is left over.
    trees.push({
      index: count,
      kind: 0,
      x: CANVAS_W - 52,
      base: GROUND_Y + 2,
      scale: 0.26 + 0.22 * remainder,
      depth: 1,
    });
  }
  return trees.sort((a, b) => a.base - b.base);
}

function Broadleaf({ scale: s }: { scale: number }) {
  const h = 126 * s;
  const w = Math.max(2.5, 6 * s);
  const r = 35 * s;
  const crown: [number, number, number, string][] = [
    [-r * 0.66, -h * 0.44, r * 0.8, "#266848"],
    [r * 0.66, -h * 0.44, r * 0.8, "#266848"],
    [0, -h * 0.38, r * 0.98, "#266848"],
    [-r * 0.34, -h * 0.66, r * 0.7, "#409260"],
    [r * 0.38, -h * 0.64, r * 0.66, "#409260"],
    [0, -h * 0.76, r * 0.64, "#409260"],
  ];
  return (
    <>
      <polygon
        points={`${-w},0 ${w},0 ${w * 0.55},${-h * 0.3} ${-w * 0.55},${-h * 0.3}`}
        fill="#68482e"
      />
      {crown.map(([cx, cy, rr, fill], i) => (
        <circle key={i} cx={cx} cy={cy} r={rr} fill={fill} />
      ))}
    </>
  );
}

function Conifer({ scale: s }: { scale: number }) {
  const h = 148 * s;
  const w = Math.max(2.5, 5 * s);
  const tiers: [number, number, number, string][] = [
    [0.62, 0.14, 0.52, "#1e5a3c"],
    [0.48, 0.4, 0.76, "#2e744c"],
    [0.32, 0.66, 1.0, "#1e5a3c"],
  ];
  return (
    <>
      <polygon
        points={`${-w},0 ${w},0 ${w * 0.55},${-h * 0.16} ${-w * 0.55},${-h * 0.16}`}
        fill="#5e422a"
      />
      {tiers.map(([width, bot, top, fill], i) => {
        const half = (h * width) / 2;
        return (
          <polygon
            key={i}
            points={`0,${-h * top} ${-half},${-h * bot} ${half},${-h * bot}`}
            fill={fill}
          />
        );
      })}
    </>
  );
}

function Slender({ scale: s }: { scale: number }) {
  const h = 136 * s;
  const w = Math.max(2.5, 4.5 * s);
  const rw = 25 * s;
  const rh = 46 * s;
  return (
    <>
      <polygon
        points={`${-w},0 ${w},0 ${w * 0.55},${-h * 0.34} ${-w * 0.55},${-h * 0.34}`}
        fill="#847058"
      />
      <ellipse cx={0} cy={-h * 0.4 - rh * 0.25} rx={rw} ry={rh * 0.75} fill="#307850" />
      <ellipse
        cx={0}
        cy={-h * 0.6 - rh * 0.15}
        rx={rw * 0.62}
        ry={rh * 0.45}
        fill="#469664"
      />
    </>
  );
}

/** The stand, drawn as one scene rather than a row of identical icons.
 *
 *  The first version laid clones out in a flex-wrap grid: same tree, same
 *  size, same spacing, thin brown trunks in tidy rows. It read as a bar
 *  chart, which is what it was. */
function Forest({ count, remainder }: { count: number; remainder: number }) {
  const trees = place(count, remainder);
  return (
    <svg
      viewBox={`0 0 ${CANVAS_W} ${CANVAS_H}`}
      className="h-auto w-full"
      role="img"
      aria-label={`${count} trees${remainder > 0.02 ? " and part of another" : ""}`}
    >
      <ellipse cx={CANVAS_W / 2} cy={GROUND_Y + 66} rx={CANVAS_W * 0.8} ry={104} fill="#d8efde" />
      <ellipse cx={CANVAS_W * 0.3} cy={GROUND_Y + 98} rx={CANVAS_W * 0.45} ry={84} fill="#cde8d4" />
      {trees.map((tree) => (
        <g
          key={tree.index}
          transform={`translate(${tree.x} ${tree.base})`}
          // Depth as opacity rather than as a colour mixed toward the
          // panel background: the panel has two backgrounds, and a tree
          // hazed toward the light one looks like a smudge on the dark.
          opacity={0.45 + 0.55 * tree.depth}
        >
          {tree.kind === 1 || tree.kind === 4 ? (
            <Conifer scale={tree.scale} />
          ) : tree.kind === 3 ? (
            <Slender scale={tree.scale} />
          ) : (
            <Broadleaf scale={tree.scale} />
          )}
        </g>
      ))}
    </svg>
  );
}

/** A ream drawn as a block of paper, filled from the bottom.
 *
 *  This is the personal page's unit, and the reason the two pages differ.
 *  The average person here is at 0.046 trees — four and a half percent of
 *  one — and a panel that opens to reveal a sliver of a tree tells them
 *  nothing they can hold. A ream is a thing they carry from the supply
 *  closet.
 *
 *  Drawn as a block seen slightly from above rather than as a stack of
 *  horizontal lines, which was the first attempt and read as a progress
 *  bar — the same complaint the trees got. */
function Ream({ fraction }: { fraction: number }) {
  const filled = Math.max(0, Math.min(1, fraction));
  const x = 20;
  const y = 190;
  const w = 190;
  const h = 150;
  const d = 46;
  const fillTop = y - h * filled;
  const sheets = Math.max(1, Math.round(26 * filled));

  return (
    <svg viewBox="0 0 300 230" className="h-auto w-full" role="img" aria-label="a ream of paper">
      {/* top and side faces, so it reads as a physical block */}
      <polygon
        points={`${x},${y - h} ${x + w},${y - h} ${x + w + d},${y - h - d * 0.55} ${x + d},${y - h - d * 0.55}`}
        fill="#d6deec"
      />
      <polygon
        points={`${x + w},${y - h} ${x + w + d},${y - h - d * 0.55} ${x + w + d},${y - d * 0.55} ${x + w},${y}`}
        fill="#c4cee0"
      />
      <rect x={x} y={y - h} width={w} height={h} fill="#e8ecf4" />
      <rect x={x} y={fillTop} width={w} height={y - fillTop} fill="#4a7bd8" />
      {Array.from({ length: sheets }).map((_, i) => {
        const lineY = y - ((i + 0.5) * (h * filled)) / sheets;
        return (
          <line
            key={i}
            x1={x + 3}
            x2={x + w - 3}
            y1={lineY}
            y2={lineY}
            stroke="#608ee2"
            strokeWidth="1"
          />
        );
      })}
      {filled < 0.99 && (
        <line
          x1={x}
          x2={x + w}
          y1={fillTop}
          y2={fillTop}
          stroke="#8b9bbd"
          strokeWidth="2"
          strokeDasharray="6 6"
        />
      )}
      <rect
        x={x}
        y={y - h}
        width={w}
        height={h}
        fill="none"
        stroke="#96a4be"
        strokeWidth="2"
      />
    </svg>
  );
}

export type TreesPanelProps = {
  /** Sheets behind it, so the arithmetic can be shown rather than asserted. */
  sheets: number;
  /** The divisor, straight from the API.
   *
   *  Deriving it as `sheets / trees` was the first attempt and it was
   *  wrong twice over: equivalency values are rounded to two decimals, so
   *  700 sheets arrives as 0.08 trees and implies 8,750 sheets a tree —
   *  and below 0.05 the trees fact is dropped entirely, which is most
   *  people, leaving nothing to divide by. */
  sheetsPerTree: number;
  /** The district's view draws a stand; a person's draws reams. */
  collective: boolean;
  sourceUrl: string | null;
  onClose: () => void;
};

export function TreesPanel({
  sheets,
  sheetsPerTree,
  collective,
  sourceUrl,
  onClose,
}: TreesPanelProps) {
  const dialog = useRef<HTMLDivElement | null>(null);
  const closeButton = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      // `role="dialog"` and `aria-modal` describe a modal; they do not
      // make one. Without this, Tab walks out of the panel and through
      // the card grid behind the overlay — the content is covered, still
      // focusable, and unreachable by eye.
      if (event.key !== "Tab" || !dialog.current) return;
      const focusable = dialog.current.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    // Opened from a card, so focus is on the trigger behind the overlay.
    // Moved in on open and handed back on close, which is what lets
    // somebody working by keyboard carry on where they left off.
    const trigger = document.activeElement as HTMLElement | null;
    closeButton.current?.focus();
    return () => trigger?.focus?.();
  }, []);

  const perTree = sheetsPerTree;
  const trees = perTree > 0 ? sheets / perTree : 0;
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
        ref={dialog}
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
            ref={closeButton}
            type="button"
            onClick={onClose}
            className="shrink-0 rounded-full border border-black/[.12] px-3 py-1.5 text-sm font-medium transition-colors hover:bg-black/[.04] dark:border-white/[.2] dark:hover:bg-white/[.06]"
          >
            Close
          </button>
        </div>

        {collective ? (
          <>
            <div className="overflow-hidden rounded-xl bg-emerald-50/60 dark:bg-emerald-950/20">
              <Forest count={drawn} remainder={remainder} />
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
              <div className="w-44 shrink-0">
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
