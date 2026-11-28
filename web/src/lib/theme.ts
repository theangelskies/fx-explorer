/**
 * Light/dark mode (docs/ui-design.md §1): follow the system until the user picks one; remember
 * the pick in localStorage. index.html applies it before first paint; this hook keeps it in sync.
 */
import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";

const STORAGE_KEY = "fx-theme";

function savedTheme(): Theme | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null; // storage blocked (private mode, settings): just follow the system
  }
}

function apply(theme: Theme): void {
  document.documentElement.classList.toggle("dark", theme === "dark");
}

export function useTheme(): [Theme, (theme: Theme) => void] {
  const [theme, setThemeState] = useState<Theme>(() =>
    document.documentElement.classList.contains("dark") ? "dark" : "light",
  );

  // While the user hasn't chosen, follow system changes live.
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      if (savedTheme() !== null) return;
      const next: Theme = media.matches ? "dark" : "light";
      apply(next);
      setThemeState(next);
    };
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  const setTheme = useCallback((next: Theme) => {
    apply(next);
    setThemeState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // not saved; the choice still applies for this visit
    }
  }, []);

  return [theme, setTheme];
}
