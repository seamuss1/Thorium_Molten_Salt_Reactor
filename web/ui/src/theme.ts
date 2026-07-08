import { useEffect, useState } from "react";

export type ThemeMode = "light" | "dark" | "system";

const STORAGE_KEY = "thorium-theme";
const THEME_EVENT = "thorium-themechange";

export function getStoredTheme(): ThemeMode {
  if (typeof localStorage === "undefined") return "system";
  const value = localStorage.getItem(STORAGE_KEY);
  return value === "light" || value === "dark" || value === "system" ? value : "system";
}

/** Resolve a mode to the concrete scheme currently in effect. */
export function resolvedScheme(mode: ThemeMode): "light" | "dark" {
  if (mode === "system") {
    return typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  return mode;
}

/** Apply a theme mode to <html> and notify subscribers (charts, etc.). */
export function applyTheme(mode: ThemeMode): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (mode === "system") {
    root.removeAttribute("data-theme");
  } else {
    root.setAttribute("data-theme", mode);
  }
  try {
    localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    // Ignore storage failures (private mode, etc.).
  }
  window.dispatchEvent(new CustomEvent(THEME_EVENT));
}

/** Read the theme mode + resolved scheme, staying in sync with changes. */
export function useTheme(): { mode: ThemeMode; scheme: "light" | "dark"; setMode: (mode: ThemeMode) => void } {
  const [mode, setModeState] = useState<ThemeMode>(getStoredTheme);
  const [scheme, setScheme] = useState<"light" | "dark">(() => resolvedScheme(getStoredTheme()));

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const sync = () => {
      const current = getStoredTheme();
      setModeState(current);
      setScheme(resolvedScheme(current));
    };
    window.addEventListener(THEME_EVENT, sync);
    media.addEventListener("change", sync);
    return () => {
      window.removeEventListener(THEME_EVENT, sync);
      media.removeEventListener("change", sync);
    };
  }, []);

  return {
    mode,
    scheme,
    setMode: (next: ThemeMode) => {
      applyTheme(next);
      setModeState(next);
      setScheme(resolvedScheme(next));
    }
  };
}

export interface ChartTokens {
  scheme: "light" | "dark";
  accent: string;
  axis: string;
  grid: string;
  label: string;
  muted: string;
  series: string[];
}

function readVar(styles: CSSStyleDeclaration, name: string, fallback: string): string {
  const value = styles.getPropertyValue(name).trim();
  return value || fallback;
}

/** Chart colors sourced from the CSS design tokens, refreshed on theme change. */
export function useChartTokens(): ChartTokens {
  const compute = (): ChartTokens => {
    if (typeof window === "undefined") {
      return { scheme: "light", accent: "#0f766e", axis: "#51606f", grid: "#dfe5ea", label: "#303944", muted: "#5b6a74", series: ["#0f766e"] };
    }
    const styles = getComputedStyle(document.documentElement);
    const scheme = resolvedScheme(getStoredTheme());
    return {
      scheme,
      accent: readVar(styles, "--accent", "#0f766e"),
      axis: readVar(styles, "--chart-axis", "#51606f"),
      grid: readVar(styles, "--chart-grid", "#dfe5ea"),
      label: readVar(styles, "--chart-label", "#303944"),
      muted: readVar(styles, "--muted", "#5b6a74"),
      series: [
        readVar(styles, "--chart-1", "#0f766e"),
        readVar(styles, "--chart-2", "#2b6cb0"),
        readVar(styles, "--chart-3", "#a1660a"),
        readVar(styles, "--chart-4", "#8a5cd0"),
        readVar(styles, "--chart-5", "#b23a2b"),
        readVar(styles, "--chart-6", "#4f8a5b")
      ]
    };
  };

  const [tokens, setTokens] = useState<ChartTokens>(compute);

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const refresh = () => setTokens(compute());
    window.addEventListener(THEME_EVENT, refresh);
    media.addEventListener("change", refresh);
    return () => {
      window.removeEventListener(THEME_EVENT, refresh);
      media.removeEventListener("change", refresh);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return tokens;
}

/** Whether the user prefers reduced motion (for chart/3D animations). */
export function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
