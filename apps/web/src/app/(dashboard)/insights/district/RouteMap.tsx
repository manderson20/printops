"use client";

import { useEffect, useRef, useState } from "react";
import "leaflet/dist/leaflet.css";
import type { DistrictRoute, RouteStop } from "@/lib/api";

// Leaflet reads `window` as it loads, and a client component is still
// rendered on the server first, so the library is imported inside the
// effect rather than at the top of the file. The stylesheet above is a
// plain CSS import and has no such problem.
//
// No image assets anywhere: the default Leaflet marker is a PNG referenced
// by a URL the bundler rewrites, and the usual result is a map full of
// broken icons. Everything drawn here is a circle or a piece of HTML, which
// also means the route still draws when the tiles cannot be fetched — a
// lobby screen that loses its internet connection shows a grey field with
// the journey still on it, rather than nothing.

const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

const TRAVELLED = "#2563eb";
const AHEAD = "#94a3b8";

type Point = [number, number];

/** The road to a stop, or the straight line to it when no route was ever
 *  fetched. A visibly poorer drawing of a real fact, which is the right
 *  failure: the milestone is still true, it is just drawn as the crow
 *  flies until somebody presses Refresh route in Settings. */
function pathTo(home: Point, stop: RouteStop): { points: Point[]; isRoad: boolean } {
  if (stop.geometry && stop.geometry.length > 1) {
    return { points: stop.geometry, isRoad: true };
  }
  return { points: [home, [stop.latitude, stop.longitude]], isRoad: false };
}

/** The first `fraction` of a polyline, measured by the polyline's own
 *  length rather than by its point count — a simplified route carries far
 *  more points through a city than across two hundred miles of interstate,
 *  so "the first half of the points" is not the first half of the drive.
 *
 *  This is the same rule the server applies to place the marker
 *  (app/reports/road_trip.py:_along), so the line ends exactly where the
 *  marker sits. */
function travelledPortion(points: Point[], fraction: number): Point[] {
  if (points.length < 2 || fraction <= 0) return [];
  if (fraction >= 1) return points;

  const lengths = points
    .slice(1)
    .map((point, index) => Math.hypot(point[0] - points[index][0], point[1] - points[index][1]));
  const total = lengths.reduce((sum, length) => sum + length, 0);
  if (total <= 0) return [];

  let remaining = total * fraction;
  const walked: Point[] = [points[0]];
  for (let index = 0; index < lengths.length; index += 1) {
    const length = lengths[index];
    if (remaining <= length) {
      const share = length > 0 ? remaining / length : 0;
      const from = points[index];
      const to = points[index + 1];
      walked.push([
        from[0] + (to[0] - from[0]) * share,
        from[1] + (to[1] - from[1]) * share,
      ]);
      return walked;
    }
    walked.push(points[index + 1]);
    remaining -= length;
  }
  return walked;
}

function caption(route: DistrictRoute): string {
  const miles = `${route.miles_travelled.toLocaleString()} miles so far`;
  const position = route.position;
  if (!position) return miles;
  if (position.from_label && position.to_label) {
    return `${miles} — between ${position.from_label} and ${position.to_label}`;
  }
  if (position.to_label) return `${miles} — on the way to ${position.to_label}`;
  if (position.from_label) return `${miles} — past ${position.from_label}`;
  return miles;
}

function footnote(route: DistrictRoute): string | null {
  const straightLegs = route.stops.filter((stop) => !stop.geometry).length;
  if (straightLegs === 0) return null;
  return straightLegs === route.stops.length
    ? "Drawn as straight lines — no driving routes have been fetched yet."
    : `${straightLegs} of these are drawn as straight lines, having no driving route yet.`;
}

export function RouteMap({ route }: { route: DistrictRoute }) {
  const container = useRef<HTMLDivElement | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let map: { remove: () => void } | null = null;
    let cancelled = false;

    (async () => {
      const L = await import("leaflet");
      if (cancelled || !container.current) return;

      const home: Point = [route.home_latitude, route.home_longitude];

      const instance = L.map(container.current, {
        // A lobby screen has nobody standing at it to pan a map back after
        // a cat walks over the keyboard, and this map has exactly one thing
        // to say. Everything interactive is off.
        zoomControl: false,
        dragging: false,
        scrollWheelZoom: false,
        doubleClickZoom: false,
        touchZoom: false,
        keyboard: false,
        attributionControl: true,
      });
      map = instance;

      L.tileLayer(TILE_URL, { attribution: TILE_ATTRIBUTION, maxZoom: 18 }).addTo(instance);

      // Every road out of town, faint. Each one runs from home rather than
      // from the previous stop, because that is what a milestone means:
      // "far enough to have reached Chicago", measured from home, not "via
      // Des Moines". Chaining them would quietly turn 410 miles into 1,200.
      const bounds: Point[] = [home];
      for (const stop of route.stops) {
        const { points, isRoad } = pathTo(home, stop);
        bounds.push(...points);
        L.polyline(points, {
          color: AHEAD,
          weight: isRoad ? 3 : 2,
          opacity: 0.65,
          // A leg with no fetched route is dashed, so a straight line
          // never passes itself off as a road.
          dashArray: isRoad ? undefined : "5 7",
        }).addTo(instance);
      }

      // And the one being driven, picked out up to where the district has
      // actually got to.
      const target = route.stops.find((stop) => stop.is_target);
      if (target && route.position) {
        const { points } = pathTo(home, target);
        const fraction = target.miles > 0 ? route.miles_travelled / target.miles : 1;
        const travelled = travelledPortion(points, fraction);
        if (travelled.length > 1) {
          L.polyline(travelled, { color: TRAVELLED, weight: 5, opacity: 0.95 }).addTo(instance);
        }
      }

      L.circleMarker(home, {
        radius: 7,
        color: TRAVELLED,
        fillColor: TRAVELLED,
        fillOpacity: 1,
        weight: 2,
      })
        .bindTooltip(route.home_name)
        .addTo(instance);

      for (const stop of route.stops) {
        L.circleMarker([stop.latitude, stop.longitude], {
          radius: 6,
          color: stop.reached ? TRAVELLED : AHEAD,
          fillColor: stop.reached ? TRAVELLED : "#ffffff",
          fillOpacity: 1,
          weight: 2,
        })
          .bindTooltip(`${stop.label} — ${stop.miles.toLocaleString()} miles`)
          .addTo(instance);
      }

      if (route.position) {
        L.marker([route.position.latitude, route.position.longitude], {
          icon: L.divIcon({
            className: "",
            html:
              '<div style="width:16px;height:16px;border-radius:9999px;' +
              "background:#f59e0b;border:3px solid #fff;" +
              'box-shadow:0 0 0 1px rgba(0,0,0,.25)"></div>',
            iconSize: [16, 16],
            iconAnchor: [8, 8],
          }),
          keyboard: false,
        })
          .bindTooltip("We are here")
          .addTo(instance);
      }

      // Padded so a pin never sits under the attribution notice or hard
      // against an edge. Bounds cover the roads, not just the pins — a
      // route that swings wide would otherwise run off the map.
      instance.fitBounds(L.latLngBounds(bounds), { padding: [32, 32] });
    })().catch(() => {
      if (!cancelled) setFailed(true);
    });

    return () => {
      cancelled = true;
      map?.remove();
    };
  }, [route]);

  if (failed) return null;

  const note = footnote(route);

  return (
    <div className="flex flex-col gap-2">
      <div
        ref={container}
        // The height is fixed rather than an aspect ratio because this sits
        // in a column with cards either side of it, and a map that changes
        // height with the viewport makes that column jump on a phone.
        className="h-[22rem] w-full overflow-hidden rounded-2xl border border-black/[.08] bg-zinc-100 dark:border-white/[.145] dark:bg-zinc-900"
      />
      <p className="text-xs text-zinc-500">
        {caption(route)}
        {note && <span className="ml-2 text-zinc-400">{note}</span>}
      </p>
    </div>
  );
}
