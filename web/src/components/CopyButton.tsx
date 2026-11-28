import { useState } from "react";

/** Copies an exact value (e.g. a rate string) to the clipboard, with spoken + visible feedback. */
export function CopyButton({ value, label }: { value: string; label: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setState("copied");
    } catch {
      setState("failed"); // e.g. clipboard blocked by the browser
    }
    window.setTimeout(() => setState("idle"), 1500);
  };
  return (
    <button type="button" onClick={copy} aria-label={label} title={label}
      className="rounded-md border border-line-input px-2 py-0.5 text-xs text-muted hover:text-fg">
      <span aria-live="polite">{state === "copied" ? "Copied ✓" : state === "failed" ? "Couldn't copy" : "Copy"}</span>
    </button>
  );
}
