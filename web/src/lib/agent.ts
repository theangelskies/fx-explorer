/** Small pure helpers for the Ask page (easy to reason about, no React in here). */
import type { Currency } from "../api/queries";

/**
 * A source chip's destination. The agent's `request` is an API call such as
 * "GET /v1/pairs/EUR/NGN?window=3y"; we map it to the matching page of the app, or null when
 * there is no such page (the chip is then just a label).
 */
export function sourceLink(request: string): string | null {
  let url: URL;
  try {
    url = new URL(request.replace(/^GET\s+/, ""), "http://placeholder.invalid");
  } catch {
    return null;
  }
  const segments = url.pathname.split("/").filter(Boolean); // ["v1", "pairs", "EUR", "NGN"]
  const q = url.searchParams;
  switch (segments[1]) {
    case "pairs": {
      if (segments.length < 4) return null;
      const window = q.get("window");
      const path = `/pairs/${encodeURIComponent(segments[2]!)}/${encodeURIComponent(segments[3]!)}`;
      return window ? `${path}?window=${encodeURIComponent(window)}` : path;
    }
    case "rates": {
      // Only the filters the Rates page understands (it has no `limit` in the URL).
      const params = new URLSearchParams();
      for (const key of ["base", "quotes", "from", "to"]) {
        const value = q.get(key);
        if (value) params.set(key, value);
      }
      const query = params.toString();
      return query ? `/rates?${query}` : "/rates";
    }
    case "movers":
      return "/";
    case "alerts":
      return "/alerts";
    default:
      return null; // e.g. currencies: no page for it
  }
}

/**
 * Which stored currencies does the question mention? By code ("NGN") or by a distinctive word of
 * its name ("naira"). Only used to word the "Checking EUR→NGN…" hint while waiting.
 */
export function mentionedCodes(question: string, currencies: Currency[]): string[] {
  const text = question.toLowerCase();
  const words = new Map<string, Set<string>>(); // name word -> codes that have it
  for (const c of currencies) {
    for (const word of c.name.toLowerCase().split(/[^a-z]+/)) {
      if (word.length < 3) continue;
      if (!words.has(word)) words.set(word, new Set());
      words.get(word)!.add(c.iso_code);
    }
  }
  const found: string[] = [];
  for (const c of currencies) {
    if (c.iso_code === "EUR") continue;
    const byCode = new RegExp(`\\b${c.iso_code.toLowerCase()}\\b`).test(text);
    const byName = [...words].some(
      ([word, codes]) => codes.size === 1 && codes.has(c.iso_code) && new RegExp(`\\b${word}`).test(text),
    );
    if (byCode || byName) found.push(c.iso_code);
  }
  return found;
}

export interface ThinkingStage {
  afterMs: number;
  text: string;
}

/**
 * What to show while the agent works. The API answers in one go (no live progress), so these
 * hints are written from the QUESTION and the clock, not from the agent's real steps.
 */
export function thinkingStages(question: string, currencies: Currency[]): ThinkingStage[] {
  const codes = mentionedCodes(question, currencies);
  const target =
    codes.length === 0
      ? "Looking through the stored rates…"
      : `Checking ${codes.slice(0, 3).map((c) => `EUR→${c}`).join(" and ")}…`;
  return [
    { afterMs: 0, text: "Reading your question…" },
    { afterMs: 1200, text: target },
    { afterMs: 7000, text: "Comparing the numbers…" },
    { afterMs: 15000, text: "Still working: some questions need several lookups…" },
  ];
}

export const STARTER_QUESTIONS = [
  "How has EUR→NGN changed over the last 3 years?",
  "Which of my currencies moved most against the euro over the last year?",
  "How much is €500 in Nigerian naira at the latest rate?",
  "What's the latest EUR→GBP rate?",
  "Alert me if EUR→USD goes above 1.20",
];
