import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router";

import { SHORTCUTS, shortcutAllowed } from "../lib/shortcuts";
import { ApiStatusBanner } from "./ApiStatusBanner";
import { Modal } from "./Modal";
import { QuickSwitcher } from "./QuickSwitcher";
import { ThemeToggle } from "./ThemeToggle";

const NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/rates", label: "Rates", end: false },
  { to: "/alerts", label: "Alerts", end: false },
  { to: "/ask", label: "Ask", end: false },
];

const GO_TO: Record<string, string> = { d: "/", r: "/rates", a: "/alerts", k: "/ask" };

function Kbd({ children }: { children: string }) {
  return <kbd className="rounded border border-line-input px-1.5 py-0.5 font-mono text-[11px]">{children}</kbd>;
}

/** Header, nav, quick switcher, shortcuts, theme toggle, page content, disclaimer footer. */
export function AppShell() {
  const navigate = useNavigate();
  const [dialog, setDialog] = useState<"switcher" | "help" | null>(null);

  // Global shortcuts: "/" switcher, "?" help, "g" then d/r/a to navigate (within 1 s).
  useEffect(() => {
    let gPressedAt = 0;
    const onKeyDown = (event: KeyboardEvent) => {
      if (!shortcutAllowed(event)) return;
      if (event.key === "/") {
        event.preventDefault();
        setDialog("switcher");
      } else if (event.key === "?") {
        event.preventDefault();
        setDialog("help");
      } else if (event.key === "g") {
        gPressedAt = Date.now();
      } else if (Date.now() - gPressedAt < 1000 && GO_TO[event.key]) {
        gPressedAt = 0;
        navigate(GO_TO[event.key]!);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [navigate]);

  const headerButton = "rounded-md border border-line-input px-2.5 py-1 text-sm text-muted hover:text-fg";

  return (
    <div className="flex min-h-screen flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-2 focus:z-50 focus:rounded-md focus:bg-surface focus:px-3 focus:py-2"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-40 border-b border-line bg-surface/95 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-[1200px] items-center gap-2 px-4 sm:gap-4 md:gap-8 md:px-6">
          <NavLink to="/" className="flex items-center gap-2 font-semibold" aria-label="fx-explorer home">
            <span aria-hidden="true" className="text-accent">◆</span>
            <span className="hidden sm:inline">fx-explorer</span>
          </NavLink>
          <nav aria-label="Main" className="flex flex-1 gap-1 overflow-x-auto">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `rounded-md px-2.5 py-1.5 text-sm font-medium whitespace-nowrap sm:px-3 ${
                    isActive ? "bg-accent/10 text-accent" : "text-muted hover:text-fg"
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <button type="button" className={headerButton} onClick={() => setDialog("switcher")} aria-label="Go to a pair or page (shortcut /)">
            <span aria-hidden="true">⌕</span>
            <span className="ml-1 hidden md:inline">Go to…</span> <span className="hidden md:inline"><Kbd>/</Kbd></span>
          </button>
          <button type="button" className={`${headerButton} hidden sm:block`} onClick={() => setDialog("help")} aria-label="Keyboard shortcuts (shortcut ?)">
            <span aria-hidden="true">?</span>
          </button>
          <ThemeToggle />
        </div>
      </header>
      <ApiStatusBanner />

      <main id="main" className="mx-auto w-full max-w-[1200px] flex-1 px-4 py-6 md:px-6">
        <Outlet />
      </main>

      <footer className="border-t border-line">
        <p className="mx-auto max-w-[1200px] px-4 py-4 text-xs text-muted md:px-6">
          Reference rates from Frankfurter. Past trends don't predict future rates. Press <Kbd>?</Kbd> for keyboard shortcuts.
        </p>
      </footer>

      <Modal open={dialog === "switcher"} onClose={() => setDialog(null)} title="Go to a pair or page">
        <QuickSwitcher onDone={() => setDialog(null)} />
      </Modal>
      <Modal open={dialog === "help"} onClose={() => setDialog(null)} title="Keyboard shortcuts">
        <h2 className="mb-3 font-semibold">Keyboard shortcuts</h2>
        <table className="w-full text-sm">
          <tbody>
            {SHORTCUTS.map((s) => (
              <tr key={s.what} className="border-b border-line/60 last:border-0">
                <td className="py-2 pr-4 whitespace-nowrap">
                  {s.keys.map((k, i) => (
                    <span key={i}>{k === "…" ? " … " : <Kbd>{k}</Kbd>}{i < s.keys.length - 1 && k !== "…" && s.keys[i + 1] !== "…" ? " then " : ""}</span>
                  ))}
                </td>
                <td className="py-2 text-muted">{s.what}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="mt-4 flex justify-end">
          <button type="button" onClick={() => setDialog(null)} className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-on-accent">Close</button>
        </div>
      </Modal>
    </div>
  );
}
