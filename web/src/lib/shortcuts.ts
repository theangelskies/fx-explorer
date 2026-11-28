/** Helpers for keyboard shortcuts: never fire while the user is typing or a dialog is open. */

export function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

export function shortcutAllowed(event: KeyboardEvent): boolean {
  return (
    !event.defaultPrevented &&
    !event.ctrlKey && !event.metaKey && !event.altKey &&
    !isTyping(event.target) &&
    document.querySelector("dialog[open]") === null
  );
}

export const SHORTCUTS: { keys: string[]; what: string }[] = [
  { keys: ["/"], what: "Go to a pair or page" },
  { keys: ["g", "d"], what: "Dashboard" },
  { keys: ["g", "r"], what: "Rates explorer" },
  { keys: ["g", "a"], what: "Alerts" },
  { keys: ["1", "…", "7"], what: "Pair detail: switch window (7d … max)" },
  { keys: ["?"], what: "Show this help" },
  { keys: ["Esc"], what: "Close a dialog" },
];
