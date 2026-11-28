import { useState } from "react";
import { Link } from "react-router";

import { useCancelAction, useConfirmAction, type AgentPendingAction, type Alert } from "../../api/agent";
import { describeError } from "../../api/errors";
import { formatDecimal } from "../../lib/format";
import { ErrorState } from "../ErrorState";

type Outcome = { kind: "created"; alert: Alert; replayed: boolean } | { kind: "cancelled" } | null;

/**
 * A proposed alert. NOTHING has been created yet: the agent only proposes. The alert exists
 * only after the person presses Confirm (and never twice: the API makes confirming idempotent).
 */
export function PendingActionCard({ action }: { action: AgentPendingAction }) {
  const confirm = useConfirmAction();
  const cancel = useCancelAction();
  const [outcome, setOutcome] = useState<Outcome>(null);
  const busy = confirm.isPending || cancel.isPending;
  const { alert } = action;
  const summary = `${alert.base}/${alert.quote} goes ${alert.condition} ${formatDecimal(alert.threshold)}`;

  if (outcome?.kind === "created") {
    return (
      <div role="status" className="mt-3 rounded-lg border border-up/40 bg-up/5 px-4 py-3 text-sm">
        <p className="font-semibold text-up">✓ Alert created</p>
        <p className="mt-1">
          {outcome.alert.base}/{outcome.alert.quote} {outcome.alert.condition} {formatDecimal(outcome.alert.threshold)}
          {outcome.alert.current_rate && <> · current rate {formatDecimal(outcome.alert.current_rate)}</>}
          {outcome.replayed && <> · this alert already existed, so no duplicate was made</>}
        </p>
        <Link to="/alerts" className="mt-1 inline-block text-accent underline">
          View my alerts
        </Link>
      </div>
    );
  }
  if (outcome?.kind === "cancelled") {
    return (
      <p role="status" className="mt-3 rounded-lg border border-line px-4 py-3 text-sm text-muted">
        Cancelled. No alert was created.
      </p>
    );
  }

  return (
    <div className="mt-3 rounded-lg border border-accent/50 bg-accent/5 px-4 py-3" aria-label="Proposed alert">
      <p className="text-xs font-semibold uppercase tracking-wide text-accent">Proposed alert · not created yet</p>
      <p className="mt-1 text-base font-semibold">Alert me when {summary}</p>
      {alert.note && <p className="mt-0.5 text-sm text-muted">Note: “{alert.note}”</p>}
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => confirm.mutate(action.id, { onSuccess: (r) => setOutcome({ kind: "created", ...r }) })}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-on-accent hover:opacity-90 disabled:opacity-50"
        >
          {confirm.isPending ? "Creating…" : "Confirm"}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => cancel.mutate(action.id, { onSuccess: () => setOutcome({ kind: "cancelled" }) })}
          className="rounded-md border border-line-input px-3 py-1.5 text-sm hover:bg-bg disabled:opacity-50"
        >
          {cancel.isPending ? "Cancelling…" : "Cancel"}
        </button>
      </div>
      {(confirm.isError || cancel.isError) && (
        <div className="mt-3">
          <ErrorState problem={describeError(confirm.error ?? cancel.error)} />
        </div>
      )}
    </div>
  );
}
