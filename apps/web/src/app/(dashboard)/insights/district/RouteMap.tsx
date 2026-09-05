"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import "leaflet/dist/leaflet.css";
import type { Map as LeafletMap, LatLngBoundsExpression } from "leaflet";
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

// How close "Where we are" gets. Close enough to read the road names
// around the marker, wide enough that the next town is usually still on
// screen — the question being answered is "where exactly have we got
// to", and a point with no context around it does not answer it.
const CLOSE_ZOOM = 11;

// A lobby screen has nobody standing at it to put the map back. Zoom is
// worth having anyway — somebody does walk up and look — so instead of
// refusing it, the map returns to the whole trip after this long without
// a touch. Long enough to lean in and read, short enough that the next
// person finds the journey rather than a street corner in Iowa.
const RETURN_TO_TRIP_MS = 90_000;

type Point = [number, number];

/** One leg: the road from the previous waypoint to this one, or the
 *  straight line between them when the trip has never been driven. A
 *  visibly poorer drawing of a real fact, which is the right failure —
 *  the milestone is still true, it is just drawn as the crow flies until
 *  somebody presses Refresh trip in Settings. */
function leg(
  home: Point,
  stops: RouteStop[],
  index: number,
): { points: Point[]; isRoad: boolean } {
  const stop = stops[index];
  if (stop.geometry && stop.geometry.length > 1) {
    return { points: stop.geometry, isRoad: true };
  }
  const previous = index === 0 ? home : ([stops[index - 1].latitude, stops[index - 1].longitude] as Point);
  return { points: [previous, [stop.latitude, stop.longitude]], isRoad: false };
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
    ? "Drawn as straight lines — the trip hasn't been driven yet."
    : `${straightLegs} of these legs are drawn straight, having no driving route yet.`;
}

export function RouteMap({ route }: { route: DistrictRoute }) {
  const container = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<LeafletMap | null>(null);
  // The whole-trip view, kept so both the buttons and the idle timer can
  // put the map back exactly where it started.
  const wholeTrip = useRef<LatLngBoundsExpression | null>(null);
  const idleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Set while the map is being moved by this component rather than by a
  // person. Leaflet fires the same events either way, and without this a
  // programmatic return to the whole trip would immediately look like a
  // viewer moving the map again.
  const moving = useRef(false);
  const [failed, setFailed] = useState(false);
  const [zoomed, setZoomed] = useState(false);

  const showWholeTrip = useCallback(() => {
    if (!mapRef.current || !wholeTrip.current) return;
    moving.current = true;
    mapRef.current.fitBounds(wholeTrip.current, { padding: [32, 32] });
    setZoomed(false);
  }, []);

  const showWhereWeAre = useCallback(() => {
    if (!mapRef.current || !route.position) return;
    moving.current = true;
    mapRef.current.setView(
      [route.position.latitude, route.position.longitude],
      CLOSE_ZOOM,
    );
    setZoomed(true);
  }, [route.position]);

  useEffect(() => {
    let map: LeafletMap | null = null;
    let cancelled = false;

    (async () => {
      const L = await import("leaflet");
      if (cancelled || !container.current) return;

      const home: Point = [route.home_latitude, route.home_longitude];

      const instance = L.map(container.current, {
        zoomControl: true,
        dragging: true,
        doubleClickZoom: true,
        touchZoom: true,
        keyboard: false,
        // The one interaction still off. This map often sits inside a
        // scrolling page, and a wheel that zooms instead of scrolling
        // traps the reader on it — the +/− buttons and pinch both work,
        // so nothing is actually unreachable.
        scrollWheelZoom: false,
        attributionControl: true,
      });
      map = instance;
      mapRef.current = instance;

      L.tileLayer(TILE_URL, { attribution: TILE_ATTRIBUTION, maxZoom: 18 }).addTo(instance);

      // The whole trip, leg by leg, in the order it is driven — later legs
      // over earlier ones. A trip that doubles back retraces the same road
      // and is drawn once; the numbered pins are what say which way round
      // it went, which is cheaper and steadier than nudging one line to
      // sit alongside the other.
      const bounds: Point[] = [home];
      route.stops.forEach((stop, index) => {
        const { points, isRoad } = leg(home, route.stops, index);
        bounds.push(...points);
        L.polyline(points, {
          color: AHEAD,
          weight: isRoad ? 3 : 2,
          opacity: 0.65,
          // A leg with no fetched route is dashed, so a straight line
          // never passes itself off as a road.
          dashArray: isRoad ? undefined : "5 7",
        }).addTo(instance);
      });

      // Then everything already driven, over the top: whole legs that are
      // behind us, and part of the one being driven now.
      route.stops.forEach((stop, index) => {
        const { points } = leg(home, route.stops, index);
        if (stop.reached) {
          L.polyline(points, { color: TRAVELLED, weight: 5, opacity: 0.95 }).addTo(instance);
          return;
        }
        if (!stop.is_target || !route.position) return;
        // How far into *this leg* the district has got, measured against
        // the interval the ladder shows rather than the road's own length.
        // The two differ when a distance was typed over the measured one,
        // and this has to match what the server used to place the marker
        // (app/reports/road_trip.py) or the line would stop somewhere the
        // pin is not.
        const before = index === 0 ? 0 : route.stops[index - 1].miles;
        const shownLeg = stop.miles - before;
        const fraction = shownLeg > 0 ? (route.miles_travelled - before) / shownLeg : 1;
        const travelled = travelledPortion(points, fraction);
        if (travelled.length > 1) {
          L.polyline(travelled, { color: TRAVELLED, weight: 5, opacity: 0.95 }).addTo(instance);
        }
      });

      L.circleMarker(home, {
        radius: 7,
        color: TRAVELLED,
        fillColor: TRAVELLED,
        fillOpacity: 1,
        weight: 2,
      })
        .bindTooltip(route.home_name)
        .addTo(instance);

      // Numbered, because the order is the one thing a drawn line cannot
      // say when a trip doubles back over its own road.
      for (const stop of route.stops) {
        const color = stop.reached ? TRAVELLED : AHEAD;
        L.marker([stop.latitude, stop.longitude], {
          icon: L.divIcon({
            className: "",
            html:
              `<div style="width:20px;height:20px;border-radius:9999px;` +
              `background:${stop.reached ? color : "#ffffff"};` +
              `border:2px solid ${color};color:${stop.reached ? "#ffffff" : color};` +
              `font:600 11px/16px system-ui,sans-serif;text-align:center;` +
              `box-shadow:0 0 0 1px rgba(0,0,0,.15)">${stop.position}</div>`,
            iconSize: [20, 20],
            iconAnchor: [10, 10],
          }),
          keyboard: false,
        })
          .bindTooltip(
            `${stop.position}. ${stop.label} — reached at ${stop.miles.toLocaleString()} miles`,
          )
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
      const tripBounds = L.latLngBounds(bounds);
      wholeTrip.current = tripBounds;
      moving.current = true;
      instance.fitBounds(tripBounds, { padding: [32, 32] });

      // Anything the viewer does restarts the clock; when it runs out the
      // map goes back to the whole trip. `moveend` rather than the start
      // events so the timer measures time since the map stopped moving,
      // which is when somebody actually starts reading it.
      instance.on("moveend zoomend", () => {
        // A move this component made — the initial fit, or either
        // button — is not somebody looking around, and only the button
        // knows which view it just set.
        if (moving.current) {
          moving.current = false;
          return;
        }
        // Panned or zoomed by hand, including with Leaflet's own +/−
        // buttons, so offer the way back.
        setZoomed(true);
        if (idleTimer.current) clearTimeout(idleTimer.current);
        idleTimer.current = setTimeout(showWholeTrip, RETURN_TO_TRIP_MS);
      });
    })().catch(() => {
      if (!cancelled) setFailed(true);
    });

    return () => {
      cancelled = true;
      if (idleTimer.current) clearTimeout(idleTimer.current);
      mapRef.current = null;
      wholeTrip.current = null;
      map?.remove();
    };
  }, [route, showWholeTrip]);

  if (failed) return null;

  const note = footnote(route);

  return (
    <div className="flex flex-col gap-2">
      <div className="relative">
        <div
          ref={container}
          // The height is fixed rather than an aspect ratio because this sits
          // in a column with cards either side of it, and a map that changes
          // height with the viewport makes that column jump on a phone.
          className="h-[22rem] w-full overflow-hidden rounded-2xl border border-black/[.08] bg-zinc-100 dark:border-white/[.145] dark:bg-zinc-900"
        />
        {/* Above Leaflet's own panes, which sit at z-index 400-700, and
            opposite its zoom buttons so the two controls don't collide. */}
        <div className="pointer-events-none absolute right-3 top-3 z-[800] flex gap-2">
          {route.position && (
            <button
              type="button"
              onClick={showWhereWeAre}
              className="pointer-events-auto rounded-full bg-white/95 px-3 py-1.5 text-xs font-medium text-zinc-700 shadow ring-1 ring-black/10 transition-colors hover:bg-white dark:bg-zinc-900/95 dark:text-zinc-200 dark:ring-white/15 dark:hover:bg-zinc-900"
            >
              Where we are
            </button>
          )}
          {zoomed && (
            <button
              type="button"
              onClick={showWholeTrip}
              className="pointer-events-auto rounded-full bg-white/95 px-3 py-1.5 text-xs font-medium text-zinc-700 shadow ring-1 ring-black/10 transition-colors hover:bg-white dark:bg-zinc-900/95 dark:text-zinc-200 dark:ring-white/15 dark:hover:bg-zinc-900"
            >
              Whole trip
            </button>
          )}
        </div>
      </div>
      <p className="text-xs text-zinc-500">
        {caption(route)}
        {note && <span className="ml-2 text-zinc-400">{note}</span>}
      </p>
    </div>
  );
}
