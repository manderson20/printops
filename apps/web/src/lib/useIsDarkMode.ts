import { useSyncExternalStore } from "react";

function subscribe(onChange: () => void) {
  const query = window.matchMedia("(prefers-color-scheme: dark)");
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

function getSnapshot() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function getServerSnapshot() {
  return false;
}

/** Charts render to SVG fill/stroke attributes, which can't pick up
 * Tailwind's `dark:` variant — this tracks the same `prefers-color-scheme`
 * signal in JS so chart series colors can follow it. useSyncExternalStore
 * (rather than an effect + state) is the pattern React recommends for
 * subscribing to a mutable value that lives outside React, like this. */
export function useIsDarkMode(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
