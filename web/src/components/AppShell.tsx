import { NavLink, Outlet } from "react-router";

import { ApiStatusBanner } from "./ApiStatusBanner";
import { ThemeToggle } from "./ThemeToggle";

const NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/rates", label: "Rates", end: false },
  { to: "/alerts", label: "Alerts", end: false },
];

/** Header, nav, theme toggle, page content, disclaimer footer (docs/ui-design.md §3). */
export function AppShell() {
  return (
    <div className="flex min-h-screen flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-2 focus:z-50 focus:rounded-md focus:bg-surface focus:px-3 focus:py-2"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-40 border-b border-line bg-surface/95 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-[1200px] items-center gap-4 px-4 md:gap-8 md:px-6">
          <NavLink to="/" className="flex items-center gap-2 font-semibold">
            <span aria-hidden="true" className="text-accent">
              ◆
            </span>
            fx-explorer
          </NavLink>
          <nav aria-label="Main" className="flex flex-1 gap-1 overflow-x-auto">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `rounded-md px-3 py-1.5 text-sm font-medium whitespace-nowrap ${
                    isActive ? "bg-accent/10 text-accent" : "text-muted hover:text-fg"
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <ThemeToggle />
        </div>
      </header>
      <ApiStatusBanner />

      <main id="main" className="mx-auto w-full max-w-[1200px] flex-1 px-4 py-6 md:px-6">
        <Outlet />
      </main>

      <footer className="border-t border-line">
        <p className="mx-auto max-w-[1200px] px-4 py-4 text-xs text-muted md:px-6">
          Reference rates from Frankfurter. Past trends don't predict future rates.
        </p>
      </footer>
    </div>
  );
}
