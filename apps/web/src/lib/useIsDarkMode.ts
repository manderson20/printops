import { useEffect, useState } from "react";

/** Charts render to SVG fill/stroke attributes, which can't pick up
 * Tailwind's `dark:` variant — this tracks the same `prefers-color-scheme`
 * signal in JS so chart series colors can follow it. */
export function useIsDarkMode(): boolean {
  const [isDark, setIsDark] = useState(false);

  useEffect(() => {
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    setIsDark(query.matches);
    const listener = (e: MediaQueryListEvent) => setIsDark(e.matches);
    query.addEventListener("change", listener);
    return () => query.removeEventListener("change", listener);
  }, []);

  return isDark;
}
