import { useRef, type KeyboardEvent } from "react";

import type { Window } from "../api/queries";

// The contract's Window enum, in display order.
export const WINDOWS: Window[] = ["7d", "30d", "90d", "1y", "3y", "5y", "max"];

export const WINDOW_LABEL: Record<Window, string> = {
  "7d": "7 days",
  "30d": "30 days",
  "90d": "90 days",
  "1y": "1 year",
  "3y": "3 years",
  "5y": "5 years",
  max: "all history",
};

interface WindowToggleProps {
  value: Window;
  onChange: (window: Window) => void;
}

/**
 * Range toggle as an accessible radio group (docs/ui-design.md §6): Tab reaches the selected
 * option, arrow keys move and select, the choice is announced.
 */
export function WindowToggle({ value, onChange }: WindowToggleProps) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const onKeyDown = (event: KeyboardEvent, index: number) => {
    const step = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1
      : event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const next = (index + step + WINDOWS.length) % WINDOWS.length;
    onChange(WINDOWS[next]!);
    refs.current[next]?.focus();
  };

  return (
    <div role="radiogroup" aria-label="Time window" className="inline-flex rounded-lg border border-line-input p-0.5">
      {WINDOWS.map((w, i) => {
        const selected = w === value;
        return (
          <button
            key={w}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={selected}
            aria-label={WINDOW_LABEL[w]}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(w)}
            onKeyDown={(e) => onKeyDown(e, i)}
            className={`rounded-md px-2.5 py-1 text-xs font-semibold uppercase tabular-nums sm:text-sm ${
              selected ? "bg-accent text-on-accent" : "text-muted hover:text-fg"
            }`}
          >
            {w}
          </button>
        );
      })}
    </div>
  );
}
