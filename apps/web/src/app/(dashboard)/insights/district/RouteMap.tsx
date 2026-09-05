"use client";

import { useEffect, useRef, useState } from "react";
import "leaflet/dist/leaflet.css";
import type { DistrictRoute } from "@/lib/api";

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

export function RouteMap({ route }: { route: DistrictRoute }) {
  const container = useRef<HTMLDivElement | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let map: { remove: () => void } | null = null;
    let cancelled = false;

    (async () => {
      const L = await import("leaflet");
      if (cancelled || !container.current) return;

      const home: [number, number] = [route.home_latitude, route.home_longitude];
      const stops: [number, number][] = route.stops.map((stop) => [
        stop.latitude,
        stop.longitude,
      ]);

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

      // The whole journey, then the part of it already travelled drawn
      // over the top — so "how far along are we" is readable at a glance
      // from across a room.
      L.polyline([home, ...stops], {
        color: "#94a3b8",
        weight: 3,
        opacity: 0.8,
        dashArray: "6 6",
      }).addTo(instance);

      const travelled: [number, number][] = [home];
      for (const stop of route.stops) {
        if (stop.reached) travelled.push([stop.latitude, stop.longitude]);
      }
      if (route.position) {
        travelled.push([route.position.latitude, route.position.longitude]);
      }
      if (travelled.length > 1) {
        L.polyline(travelled, { color: "#2563eb", weight: 5, opacity: 0.9 }).addTo(instance);
      }

      L.circleMarker(home, {
        radius: 7,
        color: "#2563eb",
        fillColor: "#2563eb",
        fillOpacity: 1,
        weight: 2,
      })
        .bindTooltip(route.home_name, { permanent: false })
        .addTo(instance);

      for (const stop of route.stops) {
        L.circleMarker([stop.latitude, stop.longitude], {
          radius: 6,
          color: stop.reached ? "#2563eb" : "#94a3b8",
          fillColor: stop.reached ? "#2563eb" : "#ffffff",
          fillOpacity: 1,
          weight: 2,
        })
          .bindTooltip(`${stop.label} — ${stop.miles.toLocaleString()} miles`, {
            permanent: false,
          })
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
          .bindTooltip("We are here", { permanent: false })
          .addTo(instance);
      }

      // Padded so a pin never sits under the attribution notice or hard
      // against an edge.
      instance.fitBounds(L.latLngBounds([home, ...stops]), { padding: [32, 32] });
    })().catch(() => {
      if (!cancelled) setFailed(true);
    });

    return () => {
      cancelled = true;
      map?.remove();
    };
  }, [route]);

  if (failed) return null;

  return (
    <div className="flex flex-col gap-2">
      <div
        ref={container}
        // The height is fixed rather than an aspect ratio because this sits
        // in a column with cards either side of it, and a map that changes
        // height with the viewport makes that column jump on a phone.
        className="h-[22rem] w-full overflow-hidden rounded-2xl border border-black/[.08] bg-zinc-100 dark:border-white/[.145] dark:bg-zinc-900"
      />
      <p className="text-xs text-zinc-500">{caption(route)}</p>
    </div>
  );
}
