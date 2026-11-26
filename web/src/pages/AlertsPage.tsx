import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/PageHeader";

export function AlertsPage() {
  return (
    <>
      <PageHeader title="Alerts" />
      <EmptyState title="Alerts coming soon" hint="Built in Phase 13 (docs/ui-design.md §4.4)." />
    </>
  );
}
