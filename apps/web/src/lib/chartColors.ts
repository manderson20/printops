/** Validated categorical palette (see the dataviz skill's references/palette.md)
 * — fixed hue order, each pair CVD-safe when adjacent. Never reassign a hue
 * per-filter or cycle these; pick a fixed slot per series and keep it. */
export const CHART_HUES = {
  blue: { light: "#2a78d6", dark: "#3987e5" },
  aqua: { light: "#1baf7a", dark: "#199e70" },
  yellow: { light: "#eda100", dark: "#c98500" },
  green: { light: "#008300", dark: "#008300" },
  violet: { light: "#4a3aa7", dark: "#9085e9" },
  red: { light: "#e34948", dark: "#e66767" },
  magenta: { light: "#e87ba4", dark: "#d55181" },
  orange: { light: "#eb6834", dark: "#d95926" },
} as const;

export function hue(name: keyof typeof CHART_HUES, isDark: boolean): string {
  return isDark ? CHART_HUES[name].dark : CHART_HUES[name].light;
}

/** Chart chrome (axes, gridlines, ink) — not the app's zinc tokens, since
 * recharts needs literal color strings, not Tailwind classes. */
export function chartChrome(isDark: boolean) {
  return {
    grid: isDark ? "#2c2c2a" : "#e1e0d9",
    axis: isDark ? "#383835" : "#c3c2b7",
    mutedText: "#898781",
    tooltipBg: isDark ? "#1a1a19" : "#fcfcfb",
    tooltipBorder: isDark ? "rgba(255,255,255,0.10)" : "rgba(11,11,11,0.10)",
    tooltipText: isDark ? "#ffffff" : "#0b0b0b",
  };
}
