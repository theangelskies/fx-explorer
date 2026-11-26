/**
 * Display formatting (docs/ui-design.md §2.5).
 *
 * Rates arrive as exact decimal STRINGS (ADR 0004). They are formatted as text, never through
 * Number(), so "0.000017454" or "1516.74" are shown exactly as the API sent them.
 */

const MINUS = "−"; // a real minus sign, the same width as "+"

/** "1516.74" -> "1,516.74"; "-0.0029" -> "−0.0029". Digits are never rounded. */
export function formatDecimal(value: string): string {
  const negative = value.startsWith("-");
  const unsigned = negative ? value.slice(1) : value;
  const [integer = "", fraction] = unsigned.split(".");
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return (negative ? MINUS : "") + grouped + (fraction !== undefined ? `.${fraction}` : "");
}

/** "2026-11-26" (a calendar date, no time zone) -> "26 Nov 2026" in the user's locale. */
export function formatDate(isoDate: string): string {
  // Parse as UTC and format in UTC, so the date never shifts by a day in other time zones.
  return new Intl.DateTimeFormat(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${isoDate}T00:00:00Z`));
}

/** An RFC 3339 timestamp -> "3 hours ago" (relative to now). */
export function formatRelative(timestamp: string, now: Date = new Date()): string {
  const seconds = Math.round((new Date(timestamp).getTime() - now.getTime()) / 1000);
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ["year", 31_536_000],
    ["month", 2_592_000],
    ["day", 86_400],
    ["hour", 3_600],
    ["minute", 60],
  ];
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) return rtf.format(Math.round(seconds / size), unit);
  }
  return rtf.format(seconds, "second");
}

/** An RFC 3339 timestamp -> full local date and time, for tooltips. */
export function formatDateTime(timestamp: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(timestamp),
  );
}

export type Direction = "up" | "down" | "flat";

/** Direction of a signed decimal string, without converting it to a float. */
export function direction(value: string): Direction {
  if (/^-?0*(\.0*)?$/.test(value)) return "flat";
  return value.startsWith("-") ? "down" : "up";
}

/**
 * "-0.2628" -> "−0.26%" (decimals given) or "−0.2628%" (exact). Always signed.
 * Rounding to fewer decimals uses Number() for DISPLAY only (ADR 0004).
 */
export function formatPct(value: string, decimals?: number): string {
  const dir = direction(value);
  const magnitude = value.replace(/^-/, "");
  const text = decimals === undefined ? formatDecimal(magnitude) : Number(magnitude).toFixed(decimals);
  const sign = dir === "down" ? MINUS : dir === "up" ? "+" : "";
  return `${sign}${text}%`;
}

/** Calendar days between two "YYYY-MM-DD" dates. */
export function daysBetween(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000);
}

/** 830 -> "2 years 3 months"; 45 -> "45 days". For "only … of data" messages. */
export function spanText(days: number): string {
  if (days < 60) return `${days} day${days === 1 ? "" : "s"}`;
  const years = Math.floor(days / 365.25);
  const months = Math.round((days - years * 365.25) / 30.44);
  const parts = [];
  if (years) parts.push(`${years} year${years === 1 ? "" : "s"}`);
  if (months) parts.push(`${months} month${months === 1 ? "" : "s"}`);
  return parts.join(" ") || "less than a month";
}
