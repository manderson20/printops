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

const GROUND_Y = 262;
const CANVAS_W = 900;
const CANVAS_H = 300;

/** Every tree is drawn up to here; past it they stand for several.
 *
 *  At this district's rate — a tree every four days — a full school year
 *  is about 97 trees, so the scene has to keep working well past the
 *  point where each one is a comfortable size. Below the limit they
 *  shrink and the forest thickens, which is what a forest does. Above it,
 *  shrinking further would turn the picture into moss. */
const DRAW_EVERY_TREE_UP_TO = 120;

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
  // How big each tree is drawn. A handful on a canvas built for a hundred
  // is a field with something in the corner, so they grow; a hundred at
  // that size would be a single green mass, so they shrink. Square-rooted
  // because the canvas grows in two dimensions as trees fill it — halving
  // the size of each makes room for four, not two.
  const roominess =
    count < 12 ? 1 + (12 - count) * 0.05 : count <= 24 ? 1 : Math.sqrt(24 / count);

  // And the band they stand in deepens as the forest thickens, so a
  // hundred trees fill the canvas rather than lining its bottom edge.
  const spread = 72 + Math.min(78, Math.max(0, (count - 24) * 0.85));

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
      base: GROUND_Y - spread * (1 - depth),
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
      scale: (0.26 + 0.22 * remainder) * Math.max(roominess, 0.6),
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
function Forest({
  count,
  remainder,
  represents,
  perTree,
}: {
  /** How many trees are drawn. */
  count: number;
  remainder: number;
  /** How many the picture is *about* — the same as `count` until grouping
   *  starts, and the number a screen reader has to hear. Announcing the
   *  drawn count would have said "61 trees" a moment before the text
   *  beneath said 121. */
  represents: number;
  perTree: number;
}) {
  const trees = place(count, remainder);
  const label =
    perTree > 1
      ? `${represents} trees, drawn as ${count} groups of ${perTree}`
      : `${count} trees${remainder > 0.02 ? " and part of another" : ""}`;
  return (
    <svg
      viewBox={`0 0 ${CANVAS_W} ${CANVAS_H}`}
      className="h-auto w-full"
      role="img"
      aria-label={label}
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

/** One tree's worth of paper, drawn as the packs it would fill.
 *
 *  This is the picture below one whole tree, and it is the third attempt
 *  at that. The two before it are worth recording because each failed in
 *  a way that pointed at this one.
 *
 *  Drawing a tree and filling it to the fraction used was the obvious
 *  idea and the worst one. The average person here is at 0.046 trees, so
 *  the fill is a sliver of trunk and the other 95% is a grey ghost — the
 *  eye lands on the part you *haven't* used, and a tree drawn mostly in
 *  grey reads as a dying tree rather than as a measurement.
 *
 *  A single pack with a fill level was better but was still a gauge
 *  wearing a pack's clothes: the pack was a container being filled, when
 *  a ream is a thing you count.
 *
 *  So count them. One tree is about seventeen packs of the paper in the
 *  supply closet, and that is a whole fact on its own — most people have
 *  never been told it. The pile is drawn at that height, yours coloured
 *  in from the bottom, and the tree stands beside it at the same height
 *  as the other half of the equation. Nothing is ghosted that a reader
 *  should not be looking at: the pale packs are paper nobody has used,
 *  which is exactly what they are.
 *
 *  Every pack is drawn while the count is reasonable. Past that each one
 *  stands for several, the same bargain Forest makes above 120 trees, and
 *  the caption says so — a picture that quietly draws 30 of 200 packs is
 *  a picture that lies about the number printed beside it. */
const MAX_DRAWN_PACKS = 30;
const PILE_HEIGHT = 200;
const PILE_GROUND = 270;

function PaperPile({ reams, packsPerTree }: { reams: number; packsPerTree: number }) {
  // At least one slot even if a mis-set sheets_per_tree puts a whole tree
  // inside a single pack: a pile of zero packs is not a drawing.
  const slots = Math.max(1, Math.min(MAX_DRAWN_PACKS, Math.round(packsPerTree)));
  const perSlot = packsPerTree / slots;
  const ph = PILE_HEIGHT / slots;
  const pw = 96;
  const depth = 13;
  const x = 60;
  const y0 = PILE_GROUND - 7;

  return (
    <svg
      viewBox="0 0 560 300"
      className="h-auto w-full"
      role="img"
      aria-label={`${reams.toFixed(2)} packs of paper out of the ${Math.round(
        packsPerTree,
      )} that make one tree`}
    >
      <rect x="0" y={PILE_GROUND} width="560" height={300 - PILE_GROUND} fill="#e3f1e8" />

      {Array.from({ length: slots }).map((_, i) => {
        // How much of *this* pack is used, in drawn-slot units, so the
        // partial pack lands on the right one whether or not slots were
        // grouped.
        const used = Math.max(0, Math.min(1, reams / perSlot - i));
        const top = y0 - i * ph - Math.max(3, ph - 1.6);
        const h = Math.max(3, ph - 1.6);
        const bottom = top + h;
        const full = used >= 0.999;
        return (
          <g key={i}>
            <polygon
              points={`${x},${top} ${x + pw},${top} ${x + pw + depth},${top - depth * 0.55} ${x + depth},${top - depth * 0.55}`}
              fill={full ? "#7aa2ea" : "#f0f4fa"}
            />
            <polygon
              points={`${x + pw},${top} ${x + pw + depth},${top - depth * 0.55} ${x + pw + depth},${bottom - depth * 0.55} ${x + pw},${bottom}`}
              fill={full ? "#3864bc" : "#d4dceb"}
            />
            <rect x={x} y={top} width={pw} height={h} fill={full ? "#4a7bd8" : "#e6ecf5"} />
            {used > 0 && !full && (
              <rect x={x} y={top} width={pw * used} height={h} fill="#4a7bd8" />
            )}
            <rect
              x={x}
              y={top}
              width={pw}
              height={h}
              fill="none"
              stroke="#92a0be"
              strokeWidth="1.2"
            />
          </g>
        );
      })}

      <text
        x="250"
        y={PILE_GROUND - PILE_HEIGHT / 2 + 12}
        fontSize="34"
        fontWeight="600"
        fill="#8aa294"
      >
        =
      </text>

      {/* Same tree as the forest, at the pile's height — the equation is
          the point, so the two sides have to measure the same. */}
      <g transform={`translate(400 ${PILE_GROUND})`}>
        <Broadleaf scale={1.55} />
      </g>
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
  // Above the limit each drawn tree stands for several, rather than the
  // scene silently dropping the rest. Grouping is stated on the page: a
  // picture that quietly represents 97 trees with 60 is a picture that
  // lies about the number printed beneath it.
  const perDrawnTree = whole > DRAW_EVERY_TREE_UP_TO ? Math.ceil(whole / DRAW_EVERY_TREE_UP_TO) : 1;
  // Rounded up, so the groups always cover the count rather than coming
  // up one short. The exact number is printed beneath the picture either
  // way, so the drawing rounding is a drawing decision, not a claim.
  const drawn = perDrawnTree > 1 ? Math.ceil(whole / perDrawnTree) : whole;
  const remainder = perDrawnTree > 1 ? 0 : trees - whole;

  // How many people printing at this rate it takes to reach one tree.
  // Only meaningful below a whole tree, which on the personal page is
  // almost everybody.
  const peoplePerTree = trees > 0 ? Math.round(1 / trees) : 0;

  // Mirrors PaperPile's own clamp, so the caption can say when a
  // drawn pack stands for more than one rather than leaving the
  // reader to notice the pile is short.
  const drawnPacks = Math.max(1, Math.min(MAX_DRAWN_PACKS, Math.round(perTree / 500)));

  return (
    <div
      // Above Leaflet, whose controls reach z-index 1000. The map is also
      // isolated at its own end (RouteMap), so either fix alone would do
      // — but a modal that can be covered by a map is the kind of thing
      // that comes back, and only one of the two travels with the dialog.
      className="fixed inset-0 z-[1100] flex items-center justify-center bg-black/50 p-4"
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
              {trees >= 1
                ? collective
                  ? "Our paper, as trees"
                  : "Your paper, as trees"
                : collective
                  ? "Our paper, as packs"
                  : "Your paper, as packs"}
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

        {trees >= 1 ? (
          <>
            <div className="overflow-hidden rounded-xl bg-emerald-50/60 dark:bg-emerald-950/20">
              <Forest
                count={drawn}
                remainder={remainder}
                represents={whole}
                perTree={perDrawnTree}
              />
            </div>
            <p className="mt-3 text-sm text-zinc-600 dark:text-zinc-400">
              {whole === 0
                ? "Not yet a whole tree."
                : `${formatNumber(whole)} whole ${whole === 1 ? "tree" : "trees"}`}
              {remainder > 0.02 && whole > 0 && `, and ${Math.round(remainder * 100)}% of another`}
              {perDrawnTree > 1 && ` — each tree drawn here stands for ${perDrawnTree}`}.
            </p>
          </>
        ) : (
          <>
            <div className="overflow-hidden rounded-xl bg-sky-50/60 dark:bg-sky-950/20">
              <PaperPile reams={sheets / 500} packsPerTree={perTree / 500} />
            </div>
            <p className="mt-3 text-sm text-zinc-600 dark:text-zinc-400">
              <strong className="tabular-nums">{(sheets / 500).toFixed(2)}</strong> packs
              of paper — 500 sheets each, the ones in the supply closet. It takes about{" "}
              {formatNumber(Math.round(perTree / 500))} of them to make one tree&rsquo;s
              worth of wood, so this is {(trees * 100).toFixed(1)}% of a tree.
              {drawnPacks < Math.round(perTree / 500) &&
                ` Each pack drawn here stands for ${(
                  perTree /
                  500 /
                  drawnPacks
                ).toFixed(1)}.`}
            </p>
            {!collective && peoplePerTree > 1 && (
              <p className="mt-1 text-sm text-zinc-500">
                About {formatNumber(peoplePerTree)} people printing like you would use
                the whole tree.
              </p>
            )}
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
