"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ApiError,
  createDestination,
  deleteDestination,
  listDestinations,
  listLocations,
  updateDestination,
  type Destination,
  type DestinationInput,
  type Location,
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
    miles: Number(draft.miles),
    latitude: coordinate(draft.latitude),
    longitude: coordinate(draft.longitude),
  };
}

function coordinatesArePaired(draft: Draft): boolean {
  return (draft.latitude.trim() === "") === (draft.longitude.trim() === "");
}

function isComplete(draft: Draft): boolean {
  return (
    draft.name.trim().length > 0 &&
    Number(draft.miles) > 0 &&
    coordinatesArePaired(draft)
  );
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
      <Field label={<>Short name <span className="text-zinc-500">(mid-sentence)</span></>}>
        <Input
          value={draft.short_name}
          onChange={set("short_name")}
          disabled={disabled}
          placeholder="Jefferson City"
        />
      </Field>
      <Field label="Driving miles">
        <Input
          value={draft.miles}
          onChange={set("miles")}
          disabled={disabled}
          placeholder="120"
          inputMode="decimal"
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
    </div>
  );
}

function DestinationRow({
  destination,
  onSaved,
  onError,
}: {
  destination: Destination;
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

  // Always shorter than the drive, so a driving figure at or below it is
  // a number somebody guessed rather than looked up.
  const suspicious =
    destination.straight_line_miles !== null &&
    destination.miles < destination.straight_line_miles;

  return (
    <li className="border-b border-black/[.06] py-3 last:border-0 dark:border-white/[.1]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`font-medium ${destination.enabled ? "" : "text-zinc-500 line-through"}`}>
              {destination.name}
            </span>
            {destination.short_name && (
              <span className="text-xs text-zinc-500">“{destination.short_name}”</span>
            )}
            {!destination.enabled && <Badge tone="neutral">Paused</Badge>}
            {destination.latitude === null && <Badge tone="neutral">Not on the map</Badge>}
          </div>
          <p className="text-xs text-zinc-500">
            {destination.miles.toLocaleString()} miles
            {destination.straight_line_miles !== null && (
              <span className="ml-2">
                · {destination.straight_line_miles.toLocaleString()} in a straight line
              </span>
            )}
          </p>
          {suspicious && (
            <p className="text-xs text-amber-700 dark:text-amber-400">
              Shorter than the straight-line distance, which no road can be.
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
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
              onClick={() => run(() => updateDestination(destination.id, toInput(draft)), "Couldn't save")}
            >
              {saving ? "Saving…" : "Save"}
            </Button>
          </div>
        </div>
      )}
    </li>
  );
}

export default function RoadTripSettingsPage() {
  const [destinations, setDestinations] = useState<Destination[] | null>(null);
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

  const enabledCount = destinations?.filter((destination) => destination.enabled).length ?? 0;

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardTitle>Road Trip</CardTitle>
        <p className="mb-4 text-xs text-zinc-500">
          Laid end to end, the district&apos;s pages stretch a certain distance — and this is the
          list of places that distance is measured against. Every one of them becomes a milestone
          on <strong>Our Printing</strong>: “that&apos;s Brookfield to Jefferson City”, “we&apos;re
          17% of the way to Chicago”. The ones with coordinates are also the pins on the map.
        </p>
        <p className="mb-4 text-xs text-zinc-500">
          Miles are <strong>driving</strong> miles, because that is what the sentence claims and
          what a reader will check against their own drive. The straight-line figure shown beside
          each one is computed from the coordinates and is always shorter — it is a floor, not an
          answer.
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
            {destinations.map((destination) => (
              <DestinationRow
                key={destination.id}
                destination={destination}
                onSaved={load}
                onError={setError}
              />
            ))}
          </ul>
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
              {saving ? "Adding…" : "Add destination"}
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
    </div>
  );
}
