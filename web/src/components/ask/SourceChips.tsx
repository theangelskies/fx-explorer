import { Link } from "react-router";

import type { AgentAnswer } from "../../api/agent";
import { sourceLink } from "../../lib/agent";

const chip = "rounded-full border border-line px-2.5 py-1 text-xs";

/** Where the numbers came from. A chip opens the matching page when there is one. */
export function SourceChips({ sources }: { sources: AgentAnswer["sources"] }) {
  if (sources.length === 0) return null;
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2" aria-label="Sources">
      <span className="text-xs text-muted">Sources</span>
      {sources.map((s) => {
        const to = sourceLink(s.request);
        return to ? (
          <Link key={s.request} to={to} title={s.request} className={`${chip} text-accent hover:bg-accent/10`}>
            {s.label}
          </Link>
        ) : (
          <span key={s.request} title={s.request} className={`${chip} text-muted`}>
            {s.label}
          </span>
        );
      })}
    </div>
  );
}
