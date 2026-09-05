"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ApiError,
  createDestination,
  createDestinations,
  deleteDestination,
  getItinerary,
  getRoadTripSettings,
  listDestinations,
  listLocations,
  refreshItinerary,
  reorderDestinations,
  suggestDestinations,
  updateDestination,
  updateRoadTripSettings,
  type Destination,
  type DestinationInput,
  type DestinationSuggestion,
  type Itinerary,
  type Location,
  type RoadTripSettings,
} from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Field, Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";

type Draft = {
  name: string;
  short_name: string;
  miles: string;
  latitude: string;
  longitude: string;
};

const EMPTY: Draft = { name: "", short_name: "", miles: "", latitude: "", longitude: "" };

function draftFrom(destination: Destination): Draft {
  return {
    name: destination.name,
    short_name: destination.short_name ?? "",
    miles: String(destination.miles),
    latitude: destination.latitude === null ? "" : String(destination.latitude),
    longitude: destination.longitude === null ? "" : String(destination.longitude),
  };
}

function coordinate(value: string): number | null {
  const trimmed = value.trim();
  return trimmed === "" ? null : Number(trimmed);
}

function toInput(draft: Draft): DestinationInput {
  return {
    name: draft.name.trim(),
    short_name: draft.short_name.trim() || null,
    // Left out when blank so the road network measures it. Typing a
    // number here is the deliberate override.
    miles: draft.miles.trim() === "" ? null : Number(draft.miles),
    latitude: coordinate(draft.latitude),
    longitude: coordinate(draft.longitude),
  };
}

function coordinatesArePaired(draft: Draft): boolean {
  return (draft.latitude.trim() === "") === (draft.longitude.trim() === "");
}

/** Either somewhere to route to, or a distance already known. A rung with
 *  neither is not a rung. */
function isComplete(draft: Draft): boolean {
  if (!draft.name.trim() || !coordinatesArePaired(draft)) return false;
  const hasCoordinates = draft.latitude.trim() !== "";
  const hasMiles = draft.miles.trim() !== "" && Number(draft.miles) > 0;
  return hasCoordinates || hasMiles;
}

function DraftFields({
  draft,
  onChange,
  disabled,
}: {
  draft: Draft;
  onChange: (draft: Draft) => void;
  disabled: boolean;
}) {
  const set = (key: keyof Draft) => (event: React.ChangeEvent<HTMLInputElement>) =>
    onChange({ ...draft, [key]: event.target.value });

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      <Field label="Milestone" className="lg:col-span-2">
        <Input
          value={draft.name}
          onChange={set("name")}
          disabled={disabled}
          placeholder="Brookfield to Jefferson City"
        />
      </Field>
      <Field
        label={
          <>
            Short name <span className="text-zinc-500">(mid-sentence)</span>
          </>
        }
      >
        <Input
          value={draft.short_name}
          onChange={set("short_name")}
          disabled={disabled}
          placeholder="Jefferson City"
        />
      </Field>
      <Field label="Latitude">
        <Input
          value={draft.latitude}
          onChange={set("latitude")}
          disabled={disabled}
          placeholder="38.5767"
          inputMode="decimal"
        />
      </Field>
      <Field label="Longitude">
        <Input
          value={draft.longitude}
          onChange={set("longitude")}
          disabled={disabled}
          placeholder="-92.1735"
          inputMode="decimal"
        />
      </Field>
      <Field
        label={
          <>
            Driving miles <span className="text-zinc-500">(optional)</span>
          </>
        }
      >
        <Input
          value={draft.miles}
          onChange={set("miles")}
          disabled={disabled}
          placeholder="measured for you"
          inputMode="decimal"
        />
      </Field>
    </div>
  );
}

function DestinationRow({
  destination,
  canMoveUp,
  canMoveDown,
  onMove,
  onSaved,
  onError,
}: {
  destination: Destination;
  canMoveUp: boolean;
  canMoveDown: boolean;
  onMove: (destination: Destination, direction: -1 | 1) => void;
  onSaved: () => void;
  onError: (message: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Draft>(() => draftFrom(destination));
  const [saving, setSaving] = useState(false);

  async function run(action: () => Promise<unknown>, failure: string) {
    setSaving(true);
    try {
      await action();
      setEditing(false);
      onSaved();
    } catch (error) {
      onError(error instanceof ApiError ? error.message : failure);
    } finally {
      setSaving(false);
    }
  }

  const overridden =
    destination.route_miles !== null &&
    Math.abs(destination.route_miles - destination.miles) > 0.05;

  return (
    <li className="border-b border-black/[.06] py-3 last:border-0 dark:border-white/[.1]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            {destination.position !== null && (
              <span className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-zinc-200 text-[11px] font-semibold text-zinc-700 dark:bg-zinc-700 dark:text-zinc-100">
                {destination.position}
              </span>
            )}
            <span
              className={`font-medium ${destination.enabled ? "" : "text-zinc-500 line-through"}`}
            >
              {destination.name}
            </span>
            {destination.short_name && (
              <span className="text-xs text-zinc-500">&ldquo;{destination.short_name}&rdquo;</span>
            )}
            {!destination.enabled && <Badge tone="neutral">Paused</Badge>}
            {destination.latitude === null ? (
              <Badge tone="neutral">Not on the map</Badge>
            ) : destination.has_route ? (
              <Badge tone="info">Driving route</Badge>
            ) : (
              <Badge tone="warning">Straight line</Badge>
            )}
          </div>
          <p className="text-xs text-zinc-500">
            {destination.leg_miles !== null && destination.position !== null && (
              <span>
                {destination.leg_miles.toLocaleString()} miles from{" "}
                {destination.position === 1 ? "home" : "the last stop"} ·{" "}
              </span>
            )}
            <strong>{destination.miles.toLocaleString()}</strong> miles into the trip
            {destination.has_route && !overridden && " — measured by road"}
            {overridden && ` — your figure; the trip measures ${destination.route_miles}`}
          </p>
          {destination.route_error && (
            <p className="text-xs text-amber-700 dark:text-amber-400">
              No driving route: {destination.route_error}
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {destination.position !== null && (
            <>
              <Button
                variant="secondary"
                className="!px-2 !py-1 text-xs"
                disabled={saving || !canMoveUp}
                onClick={() => onMove(destination, -1)}
                aria-label="Move earlier in the trip"
              >
                ↑
              </Button>
              <Button
                variant="secondary"
                className="!px-2 !py-1 text-xs"
                disabled={saving || !canMoveDown}
                onClick={() => onMove(destination, 1)}
                aria-label="Move later in the trip"
              >
                ↓
              </Button>
            </>
          )}
          <Button
            variant="secondary"
            className="!px-2 !py-1 text-xs"
            disabled={saving}
            onClick={() =>
              run(
                () => updateDestination(destination.id, { enabled: !destination.enabled }),
                "Couldn't change that",
              )
            }
          >
            {destination.enabled ? "Pause" : "Resume"}
          </Button>
          <Button
            variant="secondary"
            className="!px-2 !py-1 text-xs"
            disabled={saving}
            onClick={() => {
              setDraft(draftFrom(destination));
              setEditing((open) => !open);
            }}
          >
            {editing ? "Cancel" : "Edit"}
          </Button>
          <Button
            variant="danger"
            className="!px-2 !py-1 text-xs"
            disabled={saving}
            onClick={() => run(() => deleteDestination(destination.id), "Couldn't remove that")}
          >
            Remove
          </Button>
        </div>
      </div>

      {editing && (
        <div className="mt-3 flex flex-col gap-3 rounded-lg bg-black/[.02] p-3 dark:bg-white/[.03]">
          <DraftFields draft={draft} onChange={setDraft} disabled={saving} />
          <div>
            <Button
              disabled={saving || !isComplete(draft)}
              onClick={() =>
                run(() => updateDestination(destination.id, toInput(draft)), "Couldn't save")
              }
            >
              {saving ? "Saving…" : "Save"}
            </Button>
          </div>
        </div>
      )}
    </li>
  );
}

function SuggestionsCard({ onAdded }: { onAdded: () => void }) {
  const [suggestions, setSuggestions] = useState<DestinationSuggestion[] | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);

  async function shuffle() {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const body = await suggestDestinations();
      setSuggestions(body.suggestions);
      // Everything ticked to start with: the common case is "yes, all of
      // those", and unticking two is less work than ticking four.
      setChosen(new Set(body.suggestions.map((s) => s.name)));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't get suggestions");
      setSuggestions(null);
    } finally {
      setLoading(false);
    }
  }

  async function add() {
    if (!suggestions) return;
    setAdding(true);
    setError(null);
    try {
      const picked = suggestions.filter((s) => chosen.has(s.name));
      const outcome = await createDestinations(
        picked.map((s) => ({
          name: s.name,
          short_name: s.short_name,
          latitude: s.latitude,
          longitude: s.longitude,
        })),
      );
      const parts = [`Added ${outcome.added.length}.`];
      for (const skip of outcome.skipped) {
        parts.push(`${skip.name} — ${skip.reason}`);
      }
      setResult(parts.join(" "));
      setSuggestions(null);
      setChosen(new Set());
      onAdded();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't add those");
    } finally {
      setAdding(false);
    }
  }

  return (
    <Card>
      <CardTitle>Suggest some places</CardTitle>
      <p className="mb-4 text-xs text-zinc-500">
        Picks a place at each of a series of distances from home, each band further out than the
        last, from a bundled list of North American cities of 15,000 people or more. Nothing is
        saved until you add it, and the driving distance is measured when you do.
      </p>
      <p className="mb-4 text-xs text-zinc-500">
        It won&apos;t offer anywhere closer than about 25 miles — the list starts at 15,000
        people, so the small town down the road isn&apos;t in it. Those are the ones worth adding
        by hand, because you know them and this doesn&apos;t.
      </p>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Button variant="secondary" disabled={loading || adding} onClick={shuffle}>
          {loading ? "Thinking…" : suggestions ? "Try a different trip" : "Suggest places"}
        </Button>
        {suggestions && suggestions.length > 0 && (
          <Button disabled={adding || chosen.size === 0} onClick={add}>
            {adding
              ? "Measuring the drives…"
              : `Add ${chosen.size} destination${chosen.size === 1 ? "" : "s"}`}
          </Button>
        )}
      </div>

      {suggestions && suggestions.length === 0 && (
        <EmptyState>
          Nothing left to suggest — every place in range is already on the list.
        </EmptyState>
      )}

      {suggestions && suggestions.length > 0 && (
        <ul className="flex flex-col gap-2">
          {suggestions.map((suggestion) => (
            <li
              key={suggestion.name}
              className="flex flex-wrap items-center gap-3 rounded-lg border border-black/[.08] px-3 py-2 text-sm dark:border-white/[.145]"
            >
              <input
                type="checkbox"
                checked={chosen.has(suggestion.name)}
                onChange={(event) =>
                  setChosen((previous) => {
                    const next = new Set(previous);
                    if (event.target.checked) next.add(suggestion.name);
                    else next.delete(suggestion.name);
                    return next;
                  })
                }
                className="h-4 w-4"
              />
              <span className="min-w-0 flex-1">
                {suggestion.short_name}
                <span className="ml-2 text-xs text-zinc-500">
                  about {suggestion.straight_line_miles.toLocaleString()} miles away ·{" "}
                  {suggestion.population.toLocaleString()} people
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}

      {result && (
        <p className="mt-3 text-xs text-emerald-700 dark:text-emerald-400">{result}</p>
      )}
      {error && <ErrorState>{error}</ErrorState>}
    </Card>
  );
}

function RoutingCard({ onChanged }: { onChanged: () => void }) {
  const [settings, setSettings] = useState<RoadTripSettings | null>(null);
  const [url, setUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getRoadTripSettings()
      .then((value) => {
        setSettings(value);
        setUrl(value.routing_base_url);
      })
      .catch(() => setSettings(null));
  }, []);

  async function save(changes: Partial<RoadTripSettings>) {
    setSaving(true);
    setError(null);
    try {
      const updated = await updateRoadTripSettings(changes);
      setSettings(updated);
      setUrl(updated.routing_base_url);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save that");
    } finally {
      setSaving(false);
    }
  }

  if (!settings) return null;

  return (
    <Card>
      <CardTitle>Routing service</CardTitle>
      <p className="mb-4 text-xs text-zinc-500">
        Where PrintOps asks how far it really is by road, and which way the road goes. Asked once
        when you save a destination — never while a dashboard is on screen, so a lobby display
        never waits on it.
      </p>
      <p className="mb-4 text-xs text-zinc-500">
        The default is the public OSRM demo server, whose operators ask that it not be leaned on.
        PrintOps asks it a handful of questions when you edit a destination and none at any other
        time, but if you would rather not depend on it, point this at your own OSRM instance.
      </p>

      <div className="flex flex-wrap items-end gap-3">
        <Field label="Base URL" className="min-w-[20rem] flex-1">
          <Input
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            disabled={saving || !settings.routing_enabled}
          />
        </Field>
        <Button
          disabled={saving || url === settings.routing_base_url}
          onClick={() => save({ routing_base_url: url.trim() })}
        >
          {saving ? "Saving…" : "Save"}
        </Button>
        <Button
          variant="secondary"
          disabled={saving}
          onClick={() => save({ routing_enabled: !settings.routing_enabled })}
        >
          {settings.routing_enabled ? "Turn routing off" : "Turn routing on"}
        </Button>
      </div>

      {!settings.routing_enabled && (
        <p className="mt-3 text-xs text-amber-700 dark:text-amber-400">
          Routing is off. Distances stay whatever you type and every leg of the map is drawn as a
          straight line — which is the honest setting for a server with no way out to the
          internet.
        </p>
      )}
      {error && <ErrorState>{error}</ErrorState>}
    </Card>
  );
}

export default function RoadTripSettingsPage() {
  const [destinations, setDestinations] = useState<Destination[] | null>(null);
  const [itinerary, setItinerary] = useState<Itinerary | null>(null);
  const [home, setHome] = useState<Location | null | undefined>(undefined);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [saving, setSaving] = useState(false);

  const load = useCallback(() => {
    listDestinations()
      .then(setDestinations)
      .catch((err: Error) => setLoadError(err.message));
    listLocations()
      .then((locations) => setHome(locations.find((location) => location.is_home) ?? null))
      .catch(() => setHome(null));
    getItinerary()
      .then(setItinerary)
      .catch(() => setItinerary(null));
  }, []);

  useEffect(load, [load]);

  async function handleAdd() {
    setSaving(true);
    setError(null);
    try {
      await createDestination(toInput(draft));
      setDraft(EMPTY);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't add that destination");
    } finally {
      setSaving(false);
    }
  }

  /** Swap a waypoint with its neighbour and re-drive the trip. The whole
   *  order is sent, not the one that moved, because a waypoint's distance
   *  is the sum of the legs before it — moving one changes everything
   *  after it, and the server has to see the trip it is being asked to
   *  drive rather than infer it. */
  async function handleMove(destination: Destination, direction: -1 | 1) {
    if (!destinations) return;
    const waypoints = destinations.filter((d) => d.position !== null);
    const index = waypoints.indexOf(destination);
    const swapWith = index + direction;
    if (index < 0 || swapWith < 0 || swapWith >= waypoints.length) return;

    const reordered = [...waypoints];
    [reordered[index], reordered[swapWith]] = [reordered[swapWith], reordered[index]];

    setSaving(true);
    setError(null);
    try {
      await reorderDestinations(reordered.map((d) => d.id));
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't reorder the trip");
    } finally {
      setSaving(false);
    }
  }

  const enabledCount = destinations?.filter((destination) => destination.enabled).length ?? 0;
  const withoutRoutes =
    destinations?.filter((d) => d.latitude !== null && !d.has_route).length ?? 0;

  async function handleDriveTrip() {
    setSaving(true);
    setError(null);
    try {
      setItinerary(await refreshItinerary());
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't drive the trip");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardTitle>Road Trip</CardTitle>
        <p className="mb-4 text-xs text-zinc-500">
          Laid end to end, the district&apos;s pages stretch a certain distance — and this is the
          list of places that distance is measured against. Every one of them becomes a milestone
          on <strong>Our Printing</strong>: &ldquo;that&apos;s Brookfield to Jefferson
          City&rdquo;, &ldquo;we&apos;re 17% of the way to Chicago&rdquo;. The ones with
          coordinates are also the pins on the map, and the roads drawn between them.
        </p>
        <p className="mb-4 text-xs text-zinc-500">
          It is <strong>one trip</strong>, visiting these places in this order. A milestone is how
          far the district has to have driven to have reached that place <em>on this trip</em> —
          so Jefferson City is 130 miles in if the trip goes via Marceline first, not the 123 it
          would be as a drive of its own. Use ↑ and ↓ to change the order; everything after the
          move changes with it.
        </p>
        <p className="mb-4 text-xs text-zinc-500">
          Miles are <strong>driving</strong> miles, measured by a routing service. Type a figure of
          your own only if you want to override one; it will survive every later refresh.
        </p>

        {home === null && (
          <p className="mb-4 text-xs text-amber-700 dark:text-amber-400">
            No location is marked home, so distances have nowhere to start and the map
            won&apos;t draw.{" "}
            <Link href="/settings/locations" className="underline underline-offset-2">
              Set one in Locations
            </Link>
            .
          </p>
        )}
        {home && (
          <p className="mb-4 text-xs text-zinc-500">
            Measured from <strong>{home.name}</strong>
            {home.latitude === null && " — which has no coordinates yet, so nothing can be drawn"}.
          </p>
        )}

        {loadError && <ErrorState>{loadError}</ErrorState>}
        {destinations === null && !loadError && <Spinner label="Loading…" />}

        {destinations !== null && destinations.length === 0 && (
          <EmptyState>
            No destinations configured, so the built-in ladder is being used. Add one to take it
            over.
          </EmptyState>
        )}

        {destinations !== null && destinations.length > 0 && (
          <ul className="mb-4">
            {destinations.map((destination) => {
              const waypoints = destinations.filter((d) => d.position !== null);
              const index = waypoints.indexOf(destination);
              return (
                <DestinationRow
                  key={destination.id}
                  destination={destination}
                  canMoveUp={index > 0}
                  canMoveDown={index >= 0 && index < waypoints.length - 1}
                  onMove={handleMove}
                  onSaved={load}
                  onError={setError}
                />
              );
            })}
          </ul>
        )}

        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg bg-black/[.02] px-3 py-2 dark:bg-white/[.03]">
          <Button variant="secondary" disabled={saving} onClick={handleDriveTrip}>
            {saving ? "Driving the trip…" : "Drive the trip again"}
          </Button>
          <span className="text-xs text-zinc-500">
            {itinerary && itinerary.total_miles !== null ? (
              <>
                {itinerary.waypoints} stop{itinerary.waypoints === 1 ? "" : "s"} ·{" "}
                <strong>{itinerary.total_miles.toLocaleString()} miles</strong> end to end
                {itinerary.last_driven_at &&
                  ` · last driven ${new Date(itinerary.last_driven_at).toLocaleString()}`}
              </>
            ) : (
              "The trip hasn't been driven yet."
            )}
          </span>
        </div>

        {itinerary?.error && (
          <p className="mb-4 text-xs text-amber-700 dark:text-amber-400">
            The last attempt to drive the trip didn&apos;t land: {itinerary.error}
          </p>
        )}

        {withoutRoutes > 0 && (
          <p className="mb-4 text-xs text-amber-700 dark:text-amber-400">
            {withoutRoutes} leg{withoutRoutes === 1 ? " is" : "s are"} drawn as a straight line,
            with whatever distance was typed. <strong>Drive the trip again</strong> measures the
            real roads.
          </p>
        )}

        {destinations !== null && destinations.length > 0 && enabledCount === 0 && (
          <p className="mb-4 text-xs text-amber-700 dark:text-amber-400">
            Every destination is paused, so the built-in ladder is being used instead.
          </p>
        )}

        <div className="flex flex-col gap-3 rounded-lg border border-dashed border-black/[.12] p-3 dark:border-white/[.15]">
          <span className="text-sm font-medium">Add a destination</span>
          <DraftFields draft={draft} onChange={setDraft} disabled={saving} />
          <div>
            <Button disabled={saving || !isComplete(draft)} onClick={handleAdd}>
              {saving ? "Measuring the drive…" : "Add destination"}
            </Button>
            {!coordinatesArePaired(draft) && (
              <span className="ml-3 text-xs text-amber-700 dark:text-amber-400">
                Latitude and longitude go together — fill both, or clear both.
              </span>
            )}
          </div>
        </div>

        {error && <ErrorState>{error}</ErrorState>}
      </Card>

      {home && home.latitude !== null && <SuggestionsCard onAdded={load} />}

      <RoutingCard onChanged={load} />
    </div>
  );
}
