# UI/UX design

The design of the fx-explorer web app, written **before** any UI code. The app is built only
from `openapi.yaml` (ADR 0001): every number on screen comes from an operation listed in
[§7](#7-api-operations-per-page), and anything the pages need that the contract lacks is listed in
[§8](#8-contract-gaps). Stack (fixed): Vite, React, TypeScript, Tailwind CSS, React Router,
TanStack Query, Recharts, openapi-fetch.

---

## 1. Visual direction

**A modern fintech tool: clean, data-dense, calm.** Think of a good banking or brokerage
dashboard, not a trading terminal: lots of numbers, little decoration, and nothing that blinks.

- **Numbers first.** Rates are the largest and clearest things on screen; chrome is quiet.
- **Calm colour.** Neutral greys everywhere. One accent (indigo) for interaction. Green/red
  appear **only** for up/down, always with ▲▼ and a sign, never colour alone.
- **Honest.** Every trend view says these are reference rates and past trends don't predict
  future rates. Partial data is labelled, never hidden.
- **Light and dark mode.** Follow the system setting by default; a header toggle overrides it
  (saved in `localStorage`).

## 2. Design system

### 2.1 Colour tokens

Defined once as CSS variables and exposed to Tailwind (`bg-surface`, `text-muted`, `text-up` …),
so components never hard-code hex values. Contrast ratios below were **calculated** (WCAG 2.1
formula); every text colour passes AA (≥ 4.5:1) on both `surface` and `bg`.

| Token | Use | Light | Contrast on surface | Dark | Contrast on surface |
|---|---|---|---|---|---|
| `bg` | page background | `#f8fafc` (slate-50) | n/a | `#020617` (slate-950) | n/a |
| `surface` | cards, tables, inputs | `#ffffff` | n/a | `#0f172a` (slate-900) | n/a |
| `border` | card/table dividers (decorative) | `#cbd5e1` (slate-300) | n/a | `#334155` (slate-700) | n/a |
| `border-input` | form control outlines (needs 3:1) | `#64748b` (slate-500) | 4.76 | `#64748b` | 3.75 |
| `text` | primary text, rates | `#0f172a` (slate-900) | 17.85 | `#f1f5f9` (slate-100) | 16.30 |
| `muted` | labels, captions, axis text | `#475569` (slate-600) | 7.58 | `#94a3b8` (slate-400) | 6.96 |
| `accent` | links, focus ring, selected toggle, chart line | `#4f46e5` (indigo-600) | 6.29 | `#818cf8` (indigo-400) | 5.98 |
| `on-accent` | text on accent buttons | `#ffffff` (6.29 on accent) | n/a | `#020617` (6.76 on accent) | n/a |
| `up` | rise ▲ | `#166534` (green-800) | 7.13 | `#86efac` (green-300) | 12.71 |
| `down` | fall ▼ | `#dc2626` (red-600) | 4.83 | `#f87171` (red-400) | 6.45 |
| `notice` | partial-history / disclaimer text | `#92400e` on `#fffbeb` | 6.84 | `#fcd34d` on `#1c1917` | 12.13 |

**Up/down are told apart three ways:** hue, **lightness** (light mode: up is darker than down,
relative luminance 0.097 vs 0.167; dark mode: up is lighter, 0.698 vs 0.330), and always a
symbol and sign: `▲ +0.32%` / `▼ −0.65%`. Unchanged is `▬ 0.00%` in `muted`. So it still works
for colour-blind users and in greyscale.

### 2.2 Typography

- **Font:** the system UI stack (`ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, …`).
  No web-font dependency, fast, native-looking.
- **All numbers use tabular figures** (`font-variant-numeric: tabular-nums`, Tailwind
  `tabular-nums`) so digits line up in tables and don't jiggle when values change.

| Role | Size / weight | Example |
|---|---|---|
| Page title | 24 px / 600 | "EUR / USD" |
| KPI value | 28 px / 600, tabular | `1.0948` |
| Section title | 16 px / 600 | "Trend over 5 years" |
| Body, table | 14 px / 400 | rates table rows |
| Caption, axis, label | 12 px / 500, `muted` | "Based on 1,825 daily rates" |

### 2.3 Spacing, layout, cards

- **4 px grid** (Tailwind's default scale): 4 / 8 / 12 / 16 / 24 / 32.
- **Content width** max 1200 px, centred; 16 px side padding on mobile, 24 px from `md`.
- **Breakpoints:** mobile < 640 px (1 column), `md` ≥ 768 (2 columns), `lg` ≥ 1024 (3–4 columns).
- **Card:** `surface` background, 1 px `border`, 12 px radius, 16–24 px padding, no shadow in dark
  mode and a very soft one (`shadow-sm`) in light mode. Card title top-left in section style;
  optional action (link/toggle) top-right.

### 2.4 Chart style (Recharts)

- **Line chart**, 2 px `accent` stroke, no point markers (one appears on hover), monotone curve,
  a faint area fill under the line (accent at ~10% opacity) on the main chart only.
- **Axes:** `muted` 12 px text; horizontal grid lines only (`border` colour, dashed); y-axis
  domain fitted to the data with a little padding (never forced to start at 0, since rates don't).
- **Tooltip:** card style; shows the date ("Thu 26 Nov 2026"), the rate **exactly as returned**
  (the decimal string), and the change since the first point of the chart (▲/▼ %).
- **Moving averages:** MA30 and MA200 are single numbers in the contract (not series), so they're
  drawn as optional dashed **horizontal reference lines** labelled "MA30" / "MA200".
- **Sparklines** (KPI cards): 1.5 px line, no axes/tooltip, coloured `up`/`down` by the window's
  overall direction, with the ▲▼ change printed next to it (colour is never the only cue).
- **Numbers in charts:** Recharts needs numbers, so series rates are converted with `Number()`
  *for drawing only* (ADR 0004). Every number shown as text uses the API's string.

### 2.5 Number & date formatting

- **Rates are displayed from the API's decimal strings without float conversion.** Thousands
  separators are inserted into the string (`"1516.74"` → `1,516.74`); digits are never rounded in
  tables. KPI cards may shorten to 4 significant decimals with the full value in a tooltip/`title`.
- **Percentages:** sign + 2 decimals in cards (`▲ +0.32%`), the full 4 decimals in the trend panel.
- **Dates:** `26 Nov 2026` (the user's locale via `Intl.DateTimeFormat`); timestamps converted
  from UTC to local time with "ago" text where useful ("updated 2 h ago").
- **Missing values** (`null`) are shown as `—` with an explanation on hover/focus ("needs at
  least 1 year of data").

### 2.6 Shared components

`<AppShell>` (header, nav, theme toggle, footer disclaimer) · `<Card>` · `<Change value>` (▲▼,
sign, colour) · `<Rate value>` (formats a decimal string) · `<WindowToggle>` · `<Skeleton>` ·
`<Loading/>` · `<EmptyState title hint action/>` · `<ErrorState problem onRetry/>` ·
`<PartialHistoryNote/>` · `<Disclaimer/>` · `<CurrencySelect>` · `<Pagination>`.

---

## 3. App shell & navigation

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ◆ fx-explorer      Dashboard   Rates   Alerts                     [☾ / ☀]    │  header (sticky)
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│                              page content                                    │
│                                                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│ Reference rates from Frankfurter. Past trends don't predict future rates.   │  footer
└──────────────────────────────────────────────────────────────────────────────┘
```

| Route | Page | State kept in the URL (shareable, survives refresh) |
|---|---|---|
| `/` | Dashboard | n/a |
| `/rates` | Rates explorer | `?base=EUR&quotes=USD,GBP&from=…&to=…&sort=date_desc` |
| `/pairs/:base/:quote` | Pair detail | `?window=5y` (and `interval` if the user overrides it) |
| `/alerts` | Alerts | `?status=triggered` |

On mobile the nav collapses into a menu button; the theme toggle stays visible.

---

## 4. Pages

Wireframes show layout, not data. EUR/USD and EUR/NGN figures are real API values from
26 Nov 2026; other numbers (GBP, JPY, the movers list) are placeholders.

### 4.1 Dashboard (`/`)

"How are my currencies doing?" in one glance.

```
┌ Dashboard ────────────────────────────────────────────── Data: 26 Nov 2026 · imported 2 h ago ✓ ┐
│                                                                                                  │
│ ┌ EUR/USD ──────────┐ ┌ EUR/GBP ──────────┐ ┌ EUR/JPY ──────────┐ ┌ EUR/NGN ──────────┐          │
│ │ 1.0948            │ │ 0.8364            │ │ 175.26            │ │ 1,516.74          │  KPI cards│
│ │ ▼ −0.01% today    │ │ ▲ +0.12% today    │ │ ▬ 0.00% today     │ │ ▼ −0.49% today    │  (one per │
│ │ ╲_╱‾╲__ 30d       │ │ _╱‾‾╲_╱ 30d       │ │ ‾‾╲__╱‾ 30d       │ │ ╱‾╲_╱╲_ 30d       │  key pair,│
│ └───────────────────┘ └───────────────────┘ └───────────────────┘ └───────────────────┘  click → │
│                                                                                     pair detail) │
│ ┌ EUR/USD ─────────────────────────────── [7d][30d][90d][1y][3y][5y][max] ┐ ┌ Biggest movers ────┐ │
│ │ 1.13 ┤        ╭─╮                                                       │ │ today (26 Nov)     │ │
│ │      │   ╭────╯ ╰──╮      ╭╮                                            │ │ NGN  ▲ +1.21%      │ │
│ │ 1.10 ┤───╯         ╰──────╯╰────╮                                       │ │ JPY  ▲ +0.40%      │ │
│ │ 1.09 ┤                          ╰───                                    │ │ GBP  ▼ −0.31%      │ │
│ │      └──────────────────────────────────                                │ │ CHF  ▼ −0.52%      │ │
│ │  [pair: EUR/USD ▾]                                                      │ └────────────────────┘ │
│ └─────────────────────────────────────────────────────────────────────────┘ ┌ Quick converter ───┐ │
│ ┌ Long-term view · 5 years ───────────────────────────────────────────────┐ │ [ 100       ] EUR ▾│ │
│ │ Pair      5y change    per year    volatility                           │ │      ⇅             │ │
│ │ EUR/USD   ▼ −2.78%     ▼ −0.56%    5.0%                                 │ │ = 109.48     USD ▾ │ │
│ │ EUR/NGN   ▲ +227.58%   ▲ +26.82%   20.8%                                │ │ at 1.0948 · 26 Nov │ │
│ │ EUR/GBP   …                                                             │ └────────────────────┘ │
│ │ Reference rates. Past trends don't predict future rates.                │                        │
│ └─────────────────────────────────────────────────────────────────────────┘                        │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Key pairs** = every currency with stored rates (`listCurrencies?has_rates=true`) except the
  base, quoted against EUR (the only base the importer stores). So it adapts automatically when
  the importer's quote list changes; nothing is hard-coded.
- **KPI card:** `latest.rate`, top-level `change_pct` ("today" = vs the previous stored rate, with
  its date in the tooltip, since NGN is often a day behind), sparkline from the 30d `series`.
- **Main chart:** one pair at a time (selector + window toggle), same chart as pair detail.
- **Long-term view:** `trend.change_pct`, `trend.annualized_change_pct`,
  `trend.volatility_annualized_pct` per key pair from `window=5y`. Pairs with less than 5 years
  show the real period ("since 2019") using the partial-history rule (§5).
- **Biggest movers / data freshness:** `listMovers` and `getLatestImportRun` (ADR 0007, closing gaps G1 and G2).
- **Quick converter:** amount × `latest.rate`, computed as you type (no request per keystroke).
  The ⇅ button swaps direction (amount ÷ rate). Only EUR ↔ X pairs are offered (gap G3, decided).

### 4.2 Rates explorer (`/rates`)

Browse and filter the raw daily rates.

```
┌ Rates ───────────────────────────────────────────────────────────────────────────────┐
│ Base [EUR ▾]  Quotes [USD ×][GBP ×][+ add ▾]   From [2026-10-01]  To [2026-11-26]     │  filter bar
│ Sort [Newest first ▾]                                         [Reset]  [Apply]       │  (form; Enter
├──────────────────────────────────────────────────────────────────────────────────────┤   applies)
│ Date ▾        Base   Quote   Rate          Updated                                    │
│ 26 Nov 2026   EUR    GBP     0.83641       2 h ago                                    │
│ 26 Nov 2026   EUR    USD     1.0948        2 h ago                       → pair detail│
│ 25 Nov 2026   EUR    GBP     0.83512       …                                          │
│ …                                                                                     │
├──────────────────────────────────────────────────────────────────────────────────────┤
│ Rows per page [50 ▾]                                   [‹ Previous]  Page 2  [Next ›] │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

- **Filters map 1:1 to `listRates` parameters**: `base`, `quotes` (multi-select), `from`, `to`,
  `sort`, `limit`. Options come from `listCurrencies?has_rates=true`.
- **Pagination:** "Next" sends `page.next_cursor`; it's hidden/disabled when `next_cursor` is
  `null`. "Previous" works by remembering the cursors already visited (a client-side stack).
  No "page 3 of 20": the API has no total count (ADR 0003).
- Changing a filter resets to page 1 (cursors are only valid with the filters that made them).
- Clicking a row opens `/pairs/EUR/USD`.
- **Validation is the API's:** the UI doesn't re-implement "from ≤ to". A 400 shows its
  `errors[]` next to the matching filter (`field` = parameter name).

### 4.3 Pair detail (`/pairs/:base/:quote`)

The long-range view for informed decisions.

```
┌ EUR / USD ─ Euro → United States Dollar ─────────────────────────────────────────────────┐
│ 1.0948  ▼ −0.0001 (−0.01%) vs 25 Nov          [7d][30d][90d][1y][3y][■5y■][max]           │
│                                                                                           │
│ ┌ Chart ─────────────────────────────────────────────────────────── weekly points ─────┐ │
│ │ 1.20 ┤                                       ╭╮                                      │ │
│ │      │         ╭╮                ╭──╮       ╭╯╰─╮      ┄┄┄┄ MA200 1.1429             │ │
│ │ 1.10 ┤──╮   ╭──╯╰─╮      ╭──╮  ╭─╯  ╰──────╯    ╰───╮  ┄┄┄┄ MA30  1.1100             │ │
│ │ 1.00 ┤  ╰───╯     ╰──────╯  ╰──╯                    ╰──                              │ │
│ │      └ 2022 ──── 2023 ──── 2024 ──── 2025 ──── 2026                                  │ │
│ └──────────────────────────────────────────────────────────────────────────────────────┘ │
│ ┌ Trend · 28 Nov 2021 – 26 Nov 2026 ──────────────┐ ┌ Recent rates ─────────────────────┐ │
│ │ Change            ▼ −0.0313   (−2.7795%)        │ │ 26 Nov 2026   1.0948              │ │
│ │ Per year          ▼ −0.5629%                    │ │ 25 Nov 2026   1.0949              │ │
│ │ Volatility        4.9651% a year                │ │ 24 Nov 2026   1.0949              │ │
│ │ Biggest rise      ▲ +1.6745%  11 Apr 2025       │ │ …                                 │ │
│ │ Biggest fall      ▼ −1.1078%   6 Jul 2022       │ │ [View all in Rates explorer →]    │ │
│ │ MA30 vs MA200     1.1100 < 1.1429 (below)       │ └───────────────────────────────────┘ │
│ │ Min / avg / max   0.96024 · 1.09997 · 1.1965    │                                       │
│ │ Based on 1,825 daily rates since 1 Jan 2016     │                                       │
│ └─────────────────────────────────────────────────┘                                       │
│ ⓘ Reference rates. Past trends don't predict future rates.                                │
└───────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Window toggle** = `window` (7d … max); the chart spacing label shows `series_interval`
  ("daily / weekly / monthly points"). No interval control by default (the contract's defaults
  are sensible); an "advanced" menu may expose `interval`.
- **Header change** = top-level `change_abs`/`change_pct` (vs `previous.rate_date`).
  **Trend panel change** = `trend.change_*` (over the whole window). They are labelled
  differently on purpose: "vs 25 Nov" vs "Change (5 years)".
- **Trend panel:** every `trend` field; `null` → `—` with the reason (§2.5):
  annualised "needs ≥ 1 year", volatility "needs ≥ 20 daily rates", biggest rise "no day rose".
  MA30/MA200 labelled "(last 30/200 daily rates)" because they don't depend on the window.
- **"Based on N daily rates since <date>"** = `data_points` and `first_available_date`.
- **Recent rates table** = `listRates?base&quotes=<quote>&limit=10`, link to the explorer.
- **Disclaimer is always visible** under the trend panel (not only in the footer).

### 4.4 Alerts (`/alerts`)

```
┌ Alerts ──────────────────────────────────────────────────────────────────────────────┐
│ ┌ New alert ────────────────────────────────────────────────────────────────────────┐ │
│ │ Pair      [EUR ▾] → [USD ▾]          current rate 1.0948 (26 Nov)                  │ │
│ │ When rate [goes above ▾]  [ 1.1500         ]                                       │ │
│ │                           ⚠ Must be a number greater than 0 with at most 10 …      │ │  ← API's
│ │ Note      [ Buy dollars for the trip                     ]  0/200                  │ │    message,
│ │                                                         [ Create alert ]           │ │    under the
│ └────────────────────────────────────────────────────────────────────────────────────┘ │    field
│ [All] [Triggered] [Waiting]                                                            │
│ ┌──────────────────────────────────────────────────────────────────────────────────────┐ │
│ │ ● TRIGGERED  EUR/USD below 1.1000   now 1.0948   "—"            26 Nov   [Delete]    │ │
│ │ ○ WAITING    EUR/USD above 1.1500   now 1.0948   "Buy dollars…" 26 Nov   [Delete]    │ │
│ └──────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                            [Load more]                 │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

- **The form never duplicates validation rules.** It sends what the user typed (threshold as a
  string) and shows each `errors[].message` from the 422 under the input whose name equals
  `field` (`base`, `quote`, `condition`, `threshold`, `note`). A `body` error, or an unknown
  field name, is shown at the top of the form. Inputs get `aria-invalid` and
  `aria-describedby` pointing at the message.
  (Only *helpers* exist client-side: a `maxLength` counter on note, `inputmode="decimal"` on threshold.)
- **Double-click / retry safe:** the form creates an `Idempotency-Key` (`crypto.randomUUID()`)
  when it's opened/reset and sends it with every submit attempt of *that* form content; a new
  key after success or after the user edits the form. The button is disabled while saving.
  A `409` (still in progress) retries once after 1 s; a replay (`Idempotent-Replayed: true`) is
  treated as success (ADR 0006).
- **After creating**, the list is re-fetched (the POST response is a snapshot; ADR 0006).
- **Badges:** TRIGGERED (filled dot ●, bold text) / WAITING (hollow ○), so they differ by
  shape and text, not just colour. "now" = `current_rate` (or "no rate" if `null`).
- **Delete** asks for confirmation (inline "Delete? [Yes] [No]"), then `deleteAlert` and refetch.
  A 404 on delete (already gone) is treated as success with a short toast.
- **Filter tabs** = `status`; "Load more" uses `next_cursor`.

---

## 5. States (every view)

| State | What the user sees | When |
|---|---|---|
| **Loading** | Skeletons with the final layout's shape (grey blocks for KPI values, a flat chart placeholder, 5 table rows), with `aria-busy="true"`. Shown only after ~150 ms so fast loads don't flicker. Previous data stays visible (dimmed) while a filter/window change loads. | query pending |
| **Empty** | Friendly message plus the next step, in a card: *"No rates in this range. Try a wider date range, or run an import: `uv run python -m fx_explorer import --incremental`."* · *"No alerts yet. Create one above."* · *"No triggered alerts."* · Dashboard with no data: *"No rates stored yet. Run the backfill first."* | `200` with `data: []` |
| **Error** | `<ErrorState>`: the Problem's `title` as heading, `detail` as text, a **Retry** button (refetch). 503: "The database is unavailable" plus Retry. No API at all (network error, or the dev proxy returns a non-Problem 502/504): *"Can't reach the API. Is it running? `uv run uvicorn fx_explorer.api.app:app`"*. 404 on pair detail: "No rates stored for USD/GBP" plus links to stored pairs. | non-2xx / network |
| **Success** | The page as designed. | `200` with data |
| **Partial history** | `<PartialHistoryNote>` above the chart, `notice` style: *"Only 2 years 3 months of data for EUR/XYZ (since 3 Aug 2024); you asked for 5 years."* | `trend.period_start > window_start` (pair detail, long-term card) |

Rules: a failing *part* of a page (e.g. one KPI card) shows its own small error with Retry; the
rest of the page still works. Errors are never shown as raw JSON.

## 6. Interactions & accessibility

- **Chart hover/keyboard tooltips:** on hover, and on focus + ←/→ when the chart is focused
  (`tabindex=0`, an `aria-label` summary like "EUR/USD, 5 years, −2.78%"). The trend panel and
  tables are the text alternative to every chart.
- **Range toggles** are a radio group (`role="radiogroup"`, arrow keys move, Enter/Space select),
  with the selected one filled `accent`. The choice is written to the URL.
- **Converter updates as you type** (no debounce needed; it's local maths), result in an
  `aria-live="polite"` region; invalid input shows "Enter a number".
- **Keyboard:** everything reachable by Tab in visual order; visible focus ring (2 px `accent`,
  2 px offset) on every control; `Esc` closes menus and confirmations; no keyboard traps.
- **Responsive:** KPI cards 1 → 2 → 4 columns; on mobile, side cards (movers, converter) move
  below the chart, the rates table scrolls horizontally inside its card, the trend panel stacks
  above recent rates.
- **Motion:** subtle transitions (≤ 200 ms) only; disabled under `prefers-reduced-motion`.
- **Language:** plain words ("per year", not "CAGR"; "how bumpy" explained in a tooltip for volatility).

## 7. API operations per page

| Page | Operation (`operationId`) | Used for |
|---|---|---|
| Shell | `getHealth` | optional "API offline" banner (polled every 60 s) |
| Dashboard | `listCurrencies` (`has_rates=true`) | which key pairs to show |
| | `getPair` (`window=30d`) × key pairs | KPI value, today's change, sparkline |
| | `getPair` (selected `window`) | main chart |
| | `getPair` (`window=5y`) × key pairs | long-term view card |
| | `getPair` (`window=7d`) | converter rate (reuses the KPI query when cached) |
| | `listMovers` (ADR 0007, was gap G1) | biggest movers |
| | `getLatestImportRun` (ADR 0007, was gap G2) | data freshness |
| Rates explorer | `listCurrencies` (`has_rates=true`) | base/quote options |
| | `listRates` | table, filters, cursor pagination |
| Pair detail | `getPair` (`window`, optional `interval`) | header, chart, trend panel, partial history |
| | `getCurrency` × 2 | full names in the title |
| | `listRates` (`quotes=<quote>`, `limit=10`) | recent rates table |
| Alerts | `listCurrencies` (`has_rates=true`) | pair selects |
| | `getPair` (`window=7d`) | "current rate" hint next to the form |
| | `createAlert` (+ `Idempotency-Key`) | form submit, 422 field errors |
| | `listAlerts` (`status`, `cursor`) | list, tabs, "Load more" |
| | `deleteAlert` | delete |

`getAlert` isn't needed by any page (the list holds everything); it stays available for links.

## 8. Contract gaps

Things the pages need that `openapi.yaml` does **not** provide, with a recommendation. Per the
golden rule, none of these is worked around in the UI: each becomes a contract change + ADR first.

| # | Need | Gap | Recommendation |
|---|---|---|---|
| **G1** | Dashboard "biggest movers today" | No operation returns day changes across all quotes. Calling `getPair` for ~200 currencies is out of the question. | **Add `GET /v1/movers`** (`base` default EUR, `limit`, maybe `direction`): each stored quote's latest rate, previous rate and `change_pct`, sorted by size of move, with each item's own `rate_date` (NGN is often a day behind). **Resolved: `listMovers`, ADR 0007.** |
| **G2** | Dashboard "data freshness" (last import) | `import_runs` isn't exposed. `Currency.latest_rate_date` gives the newest *rate date*, but not *when* the import ran or whether it failed. | **Add `GET /v1/import-runs/latest`**: `started_at`, `finished_at`, `status` (`running`/`success`/`partial`/`failed`), counts. 404 if no import has run. **Resolved: `getLatestImportRun`, ADR 0007.** |
| **G3** | Converter between two non-EUR currencies (e.g. USD → GBP) | The API serves only stored pairs, so no USD/GBP rate (by design: no cross rates). | **Decided 2026-11-26: (a).** The converter only offers pairs that exist (EUR ↔ X), exactly what the contract offers. No client-side cross rates; if ever wanted, they come as a contract change (server-side). |
| G4 | Dashboard makes ~2 × N `getPair` calls (N key pairs: 30d + 5y) | No batch/summary operation. | Acceptable for N ≈ 6 (parallel, cached by TanStack Query). **No change now**; revisit with a summary endpoint if N grows. |
| G5 | Rates explorer: which currencies have data *as a base*? | `has_rates` counts base **or** quote, so picking USD as base gives an empty result. | Minor: default and suggest `EUR`; the empty state says "Rates are stored with EUR as the base". **No change now.** |

Everything else the pages need is already in the contract, including partial history
(`window_start` vs `trend.period_start`), chart spacing (`series_interval`) and alert badges
(`triggered`).
