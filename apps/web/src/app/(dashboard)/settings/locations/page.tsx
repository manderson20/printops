"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  createLocation,
  deleteLocation,
  listLocations,
  listUnmatchedBuildings,
  updateLocation,
  type Location,
  type LocationInput,
  type UnmatchedBuilding,
} from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardTitle } from "@/components/ui/Card";
import { EmptyState, ErrorState } from "@/components/ui/EmptyState";
import { Field, Input } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";

type Draft = {
  name: string;
  street: string;
  city: string;
  state: string;
  postal_code: string;
  latitude: string;
  longitude: string;
};

const EMPTY: Draft = {
  name: "",
  street: "",
  city: "",
  state: "",
  postal_code: "",
  latitude: "",
  longitude: "",
};

function draftFrom(location: Location): Draft {
  return {
    name: location.name,
    street: location.street ?? "",
    city: location.city ?? "",
    state: location.state ?? "",
    postal_code: location.postal_code ?? "",
    latitude: location.latitude === null ? "" : String(location.latitude),
    longitude: location.longitude === null ? "" : String(location.longitude),
  };
}

/** Empty string means "not set", which is a different thing from zero —
 *  and zero is a real coordinate, so this cannot use a falsy check. */
function coordinate(value: string): number | null {
  const trimmed = value.trim();
  if (trimmed === "") return null;
  const parsed = Number(trimmed);
  // Number("39.7864° N") is NaN, and NaN survives the both-or-neither check
  // above only to be serialised as null — so a pasted coordinate with a
  // degree sign or a compass letter would silently save as no coordinate at
  // all. Refused here instead, where isValidCoordinates can say so.
  return Number.isFinite(parsed) ? parsed : NaN;
}

/** Both present or both absent, and both actually numbers. */
function coordinatesAreUsable(latitude: string, longitude: string): boolean {
  const pairedness = latitude.trim() === "" ? longitude.trim() === "" : longitude.trim() !== "";
  if (!pairedness) return false;
  if (latitude.trim() === "") return true;
  return Number.isFinite(Number(latitude)) && Number.isFinite(Number(longitude));
}

function toInput(draft: Draft): LocationInput {
  return {
    name: draft.name.trim(),
    street: draft.street.trim() || null,
    city: draft.city.trim() || null,
    state: draft.state.trim() || null,
    postal_code: draft.postal_code.trim() || null,
    latitude: coordinate(draft.latitude),
    longitude: coordinate(draft.longitude),
  };
}

/** Both or neither. The API enforces this too; catching it here means the
 *  admin is told before the round trip rather than after it. */
function coordinatesArePaired(draft: Draft): boolean {
  return coordinatesAreUsable(draft.latitude, draft.longitude);
}

function addressLine(location: Location): string {
  const parts = [
    location.street,
    [location.city, location.state].filter(Boolean).join(", "),
    location.postal_code,
  ].filter((part) => part && part.length > 0);
  return parts.join(" · ");
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
      <Field label="Name">
        <Input value={draft.name} onChange={set("name")} disabled={disabled} placeholder="High School" />
      </Field>
      <Field label="Street" className="lg:col-span-2">
        <Input value={draft.street} onChange={set("street")} disabled={disabled} placeholder="1000 Pershing Rd" />
      </Field>
      <Field label="City">
        <Input value={draft.city} onChange={set("city")} disabled={disabled} placeholder="Brookfield" />
      </Field>
      <Field label="State">
        <Input value={draft.state} onChange={set("state")} disabled={disabled} placeholder="MO" />
      </Field>
      <Field label="ZIP">
        <Input value={draft.postal_code} onChange={set("postal_code")} disabled={disabled} placeholder="64628" />
      </Field>
      <Field label="Latitude">
        <Input value={draft.latitude} onChange={set("latitude")} disabled={disabled} placeholder="39.7864" inputMode="decimal" />
      </Field>
      <Field label="Longitude">
        <Input value={draft.longitude} onChange={set("longitude")} disabled={disabled} placeholder="-93.0735" inputMode="decimal" />
      </Field>
    </div>
  );
}

function LocationRow({
  location,
  busy,
  onSaved,
  onError,
}: {
  location: Location;
  busy: boolean;
  onSaved: () => void;
  onError: (message: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Draft>(() => draftFrom(location));
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

  const disabled = busy || saving;

  return (
    <li className="border-b border-black/[.06] py-3 last:border-0 dark:border-white/[.1]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium">{location.name}</span>
            {location.is_home && <Badge tone="info">Home</Badge>}
            {!location.latitude && <Badge tone="neutral">No coordinates</Badge>}
          </div>
          <p className="text-xs text-zinc-500">
            {addressLine(location) || "No address yet"}
            {location.latitude !== null && (
              <span className="ml-2 font-mono">
                {location.latitude}, {location.longitude}
              </span>
            )}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {!location.is_home && (
            <Button
              variant="secondary"
              className="!px-2 !py-1 text-xs"
              disabled={disabled}
              onClick={() =>
                run(() => updateLocation(location.id, { is_home: true }), "Couldn't set home")
              }
            >
              Make home
            </Button>
          )}
          <Button
            variant="secondary"
            className="!px-2 !py-1 text-xs"
            disabled={disabled}
            onClick={() => {
              setDraft(draftFrom(location));
              setEditing((open) => !open);
            }}
          >
            {editing ? "Cancel" : "Edit"}
          </Button>
          <Button
            variant="danger"
            className="!px-2 !py-1 text-xs"
            disabled={disabled}
            onClick={() => run(() => deleteLocation(location.id), "Couldn't remove location")}
          >
            Remove
          </Button>
        </div>
      </div>

      {editing && (
        <div className="mt-3 flex flex-col gap-3 rounded-lg bg-black/[.02] p-3 dark:bg-white/[.03]">
          <DraftFields draft={draft} onChange={setDraft} disabled={disabled} />
          <div>
            <Button
              disabled={disabled || !draft.name.trim() || !coordinatesArePaired(draft)}
              onClick={() => run(() => updateLocation(location.id, toInput(draft)), "Couldn't save location")}
            >
              {saving ? "Saving…" : "Save"}
            </Button>
            {!coordinatesArePaired(draft) && (
              <span className="ml-3 text-xs text-amber-700 dark:text-amber-400">
                Latitude and longitude go together, and both must be plain numbers — no degree signs or compass letters.
              </span>
            )}
          </div>
        </div>
      )}
    </li>
  );
}

export default function LocationsSettingsPage() {
  const [locations, setLocations] = useState<Location[] | null>(null);
  const [unmatched, setUnmatched] = useState<UnmatchedBuilding[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [saving, setSaving] = useState(false);

  const load = useCallback(() => {
    listLocations()
      .then(setLocations)
      .catch((err: Error) => setLoadError(err.message));
    listUnmatchedBuildings()
      .then(setUnmatched)
      .catch(() => setUnmatched([]));
  }, []);

  useEffect(load, [load]);

  async function handleAdd() {
    setSaving(true);
    setError(null);
    try {
      await createLocation(toInput(draft));
      setDraft(EMPTY);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't add that location");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardTitle>Locations</CardTitle>
        <p className="mb-4 text-xs text-zinc-500">
          The district&apos;s own places. One of them is <strong>home</strong>: the point every
          road-trip milestone is measured from, and where the map on Our Printing starts. Until
          this page existed that address was compiled into the server, which is fine for one
          district and no use to anybody else.
        </p>
        <p className="mb-4 text-xs text-zinc-500">
          Coordinates are what put a pin on the map. PrintOps does not look addresses up — paste
          the two numbers from any map site (right-click a spot in Google Maps and the
          latitude/longitude is the first item on the menu). A location without them still keeps
          its address; it just can&apos;t be drawn.
        </p>

        {loadError && <ErrorState>{loadError}</ErrorState>}
        {locations === null && !loadError && <Spinner label="Loading…" />}

        {locations !== null && locations.length === 0 && (
          <EmptyState>
            No locations yet. Add the building the road trip should start from.
          </EmptyState>
        )}

        {locations !== null && locations.length > 0 && (
          <ul className="mb-4">
            {locations.map((location) => (
              <LocationRow
                key={location.id}
                location={location}
                busy={saving}
                onSaved={load}
                onError={setError}
              />
            ))}
          </ul>
        )}

        {locations !== null && !locations.some((location) => location.is_home) && (
          <p className="mb-4 text-xs text-amber-700 dark:text-amber-400">
            No location is marked home, so there is no map on Our Printing. The milestones still
            work — they just have nowhere to start from.
          </p>
        )}

        <div className="flex flex-col gap-3 rounded-lg border border-dashed border-black/[.12] p-3 dark:border-white/[.15]">
          <span className="text-sm font-medium">Add a location</span>
          <DraftFields draft={draft} onChange={setDraft} disabled={saving} />
          <div>
            <Button
              disabled={saving || !draft.name.trim() || !coordinatesArePaired(draft)}
              onClick={handleAdd}
            >
              {saving ? "Adding…" : "Add location"}
            </Button>
            {!coordinatesArePaired(draft) && (
              <span className="ml-3 text-xs text-amber-700 dark:text-amber-400">
                Latitude and longitude go together, and both must be plain numbers — no degree signs or compass letters.
              </span>
            )}
          </div>
        </div>

        {error && <ErrorState>{error}</ErrorState>}
      </Card>

      {unmatched.length > 0 && (
        <Card>
          <CardTitle>Buildings with no location</CardTitle>
          <p className="mb-4 text-xs text-zinc-500">
            Names typed into printers&apos; <strong>Building</strong> field that nothing on this
            page claims. They are matched by name, ignoring case and stray spaces — so adding a
            location with the same name is all it takes.
          </p>
          <ul className="flex flex-col gap-2">
            {unmatched.map((row) => (
              <li
                key={row.building}
                className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-black/[.08] px-3 py-2 text-sm dark:border-white/[.145]"
              >
                <span>
                  {row.building}
                  <span className="ml-2 text-xs text-zinc-500">
                    {row.printer_count} printer{row.printer_count === 1 ? "" : "s"}
                  </span>
                </span>
                <Button
                  variant="secondary"
                  className="!px-2 !py-1 text-xs"
                  onClick={() => setDraft({ ...EMPTY, name: row.building })}
                >
                  Use this name
                </Button>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
