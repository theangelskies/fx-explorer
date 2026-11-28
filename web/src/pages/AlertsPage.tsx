/**
 * Alerts (docs/ui-design.md §4.4).
 * Operations: listCurrencies, getPair (current rate hint), createAlert (+ Idempotency-Key),
 * listAlerts (status, cursor), deleteAlert.
 */
import { useSearchParams } from "react-router";

import type { AlertStatus } from "../api/queries";
import { AlertForm } from "../components/AlertForm";
import { AlertList } from "../components/AlertList";
import { Card } from "../components/Card";
import { PageHeader } from "../components/PageHeader";

export function AlertsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  // Sent as is; an unknown ?status= comes back as the API's 400 message in the list.
  const status = (searchParams.get("status") ?? undefined) as AlertStatus | undefined;

  return (
    <>
      <PageHeader title="Alerts" />
      <div className="space-y-4">
        <Card title="New alert">
          <AlertForm />
        </Card>
        <Card title="Your alerts">
          <AlertList
            status={status}
            onStatus={(next) => setSearchParams(next ? { status: next } : {}, { replace: true })}
          />
        </Card>
      </div>
    </>
  );
}
