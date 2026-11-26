/**
 * "Go to…" quick switcher (shortcut "/"): type to filter pairs and pages, arrows to move,
 * Enter to open. ARIA combobox + listbox, so screen readers announce the highlighted option.
 */
import { useId, useState, type KeyboardEvent } from "react";
import { useNavigate } from "react-router";

import { useCurrencies } from "../api/queries";

interface Entry {
  label: string;
  hint: string;
  to: string;
}

const PAGES: Entry[] = [
  { label: "Dashboard", hint: "page", to: "/" },
  { label: "Rates explorer", hint: "page", to: "/rates" },
  { label: "Alerts", hint: "page", to: "/alerts" },
];

export function QuickSwitcher({ onDone }: { onDone: () => void }) {
  const navigate = useNavigate();
  const currencies = useCurrencies({ hasRates: true });
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listId = useId();

  const pairs: Entry[] = (currencies.data ?? [])
    .filter((c) => c.iso_code !== "EUR")
    .map((c) => ({ label: `EUR/${c.iso_code}`, hint: c.name, to: `/pairs/EUR/${c.iso_code}` }));
  const q = query.trim().toLowerCase();
  const entries = [...pairs, ...PAGES].filter(
    (e) => !q || e.label.toLowerCase().includes(q) || e.hint.toLowerCase().includes(q),
  );
  const current = Math.min(active, Math.max(entries.length - 1, 0));

  const go = (entry: Entry | undefined) => {
    if (!entry) return;
    onDone();
    navigate(entry.to);
  };

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((current + step + entries.length) % Math.max(entries.length, 1));
    } else if (event.key === "Enter") {
      event.preventDefault();
      go(entries[current]);
    }
  };

  return (
    <div>
      <input
        autoFocus
        role="combobox"
        aria-expanded="true"
        aria-controls={listId}
        aria-activedescendant={entries.length ? `${listId}-${current}` : undefined}
        aria-label="Go to a pair or page"
        placeholder="Go to… (e.g. usd, naira, alerts)"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setActive(0);
        }}
        onKeyDown={onKeyDown}
        className="w-full rounded-md border border-line-input bg-surface px-3 py-2"
      />
      <ul id={listId} role="listbox" aria-label="Results" className="mt-2 max-h-72 overflow-y-auto">
        {entries.map((entry, i) => (
          <li
            key={entry.to}
            id={`${listId}-${i}`}
            role="option"
            aria-selected={i === current}
            onMouseEnter={() => setActive(i)}
            onClick={() => go(entry)}
            className={`flex cursor-pointer items-baseline justify-between gap-3 rounded-md px-3 py-2 text-sm ${
              i === current ? "bg-accent/10 text-accent" : ""
            }`}
          >
            <span className="font-medium">{entry.label}</span>
            <span className="truncate text-xs text-muted">{entry.hint}</span>
          </li>
        ))}
        {entries.length === 0 && <li className="px-3 py-2 text-sm text-muted">No matches.</li>}
      </ul>
      <p className="mt-2 text-xs text-muted">↑↓ to move · Enter to open · Esc to close</p>
    </div>
  );
}
