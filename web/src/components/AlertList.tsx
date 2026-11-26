/**
 * Alert list (docs/ui-design.md §4.4): filter tabs, badges that differ by shape AND text, inline
 * delete confirmation, "Load more" via next_cursor.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { api } from "../api/client";
import { ApiError, call, describeError } from "../api/errors";
import { useAlerts, type Alert, type AlertStatus } from "../api/queries";
import { formatDate, formatDateTime, formatDecimal } from "../lib/format";
import { EmptyState } from "./EmptyState";
import { ErrorState } from "./ErrorState";
import { Loading } from "./Loading";

const TABS: { value: AlertStatus | undefined; label: string }[] = [
  { value: undefined, label: "All" },
  { value: "triggered", label: "Triggered" },
  { value: "waiting", label: "Waiting" },
];

const small = "rounded-md border border-line-input px-2.5 py-1 text-xs font-medium hover:bg-line/40 disabled:opacity-50";

function Badge({ triggered }: { triggered: boolean }) {
  return triggered ? (
    <span className="inline-flex items-center gap-1 rounded-full bg-accent px-2 py-0.5 text-xs font-bold text-on-accent">
      <span aria-hidden="true">●</span> TRIGGERED
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-full border border-line-input px-2 py-0.5 text-xs font-medium text-muted">
      <span aria-hidden="true">○</span> WAITING
    </span>
  );
}

function DeleteButton({ alert }: { alert: Alert }) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const remove = useMutation({
    mutationFn: async () => {
      try {
        await call(api.DELETE("/v1/alerts/{id}", { params: { path: { id: alert.id } } }));
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return; // already gone: fine
        throw error;
      }
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["alerts"] }),
  });

  if (remove.isError) {
    return (
      <span role="alert" className="text-xs text-down">
        {describeError(remove.error).title}.{" "}
        <button type="button" className="underline" onClick={() => remove.mutate()}>Retry</button>
      </span>
    );
  }
  if (!confirming) {
    return <button type="button" className={small} onClick={() => setConfirming(true)}>Delete</button>;
  }
  return (
    <span className="inline-flex items-center gap-1.5 text-xs" onKeyDown={(e) => e.key === "Escape" && setConfirming(false)}>
      Delete?
      <button type="button" autoFocus className={`${small} border-down text-down`} disabled={remove.isPending}
        onClick={() => remove.mutate()}>
        {remove.isPending ? "Deleting…" : "Yes"}
      </button>
      <button type="button" className={small} onClick={() => setConfirming(false)}>No</button>
    </span>
  );
}

function AlertRow({ alert }: { alert: Alert }) {
  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line/60 py-3 last:border-0">
      <Badge triggered={alert.triggered} />
      <div className="min-w-[12rem] flex-1">
        <p className="text-sm">
          <Link to={`/pairs/${alert.base}/${alert.quote}`} className="font-semibold text-accent hover:underline">
            {alert.base}/{alert.quote}
          </Link>{" "}
          {alert.condition} <span className="font-medium tabular-nums">{formatDecimal(alert.threshold)}</span>
          <span className="text-muted">
            {" · now "}
            {alert.current_rate ? <span className="tabular-nums">{formatDecimal(alert.current_rate)}</span> : "no rate"}
          </span>
        </p>
        {alert.note && <p className="truncate text-xs text-muted" title={alert.note}>“{alert.note}”</p>}
      </div>
      <span className="text-xs text-muted" title={formatDateTime(alert.created_at)}>
        {formatDate(alert.created_at.slice(0, 10))}
      </span>
      <DeleteButton alert={alert} />
    </li>
  );
}

interface AlertListProps {
  status: AlertStatus | undefined;
  onStatus: (status: AlertStatus | undefined) => void;
}

export function AlertList({ status, onStatus }: AlertListProps) {
  const alerts = useAlerts(status);
  const items = alerts.data?.pages.flatMap((p) => p.data) ?? [];

  return (
    <div>
      <div role="group" aria-label="Filter alerts" className="mb-3 inline-flex rounded-lg border border-line-input p-0.5">
        {TABS.map((tab) => (
          <button key={tab.label} type="button" aria-pressed={tab.value === status} onClick={() => onStatus(tab.value)}
            className={`rounded-md px-3 py-1 text-sm font-medium ${tab.value === status ? "bg-accent text-on-accent" : "text-muted hover:text-fg"}`}>
            {tab.label}
          </button>
        ))}
      </div>

      {alerts.isPending ? (
        <Loading label="Loading alerts…" rows={4} />
      ) : alerts.isError ? (
        <ErrorState problem={describeError(alerts.error)} onRetry={() => alerts.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState
          title={status ? `No ${status} alerts` : "No alerts yet"}
          hint={status ? "Try another filter." : "Create one with the form above."}
        />
      ) : (
        <>
          <ul aria-busy={alerts.isFetching}>{items.map((a) => <AlertRow key={a.id} alert={a} />)}</ul>
          {alerts.hasNextPage && (
            <div className="mt-3 flex justify-center">
              <button type="button" className={small} disabled={alerts.isFetchingNextPage} onClick={() => alerts.fetchNextPage()}>
                {alerts.isFetchingNextPage ? "Loading…" : "Load more"}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
