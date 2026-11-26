import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/PageHeader";

export function DashboardPage() {
  return (
    <>
      <PageHeader title="Dashboard" />
      <EmptyState title="Dashboard coming soon" hint="Built in Phase 13 (docs/ui-design.md §4.1)." />
    </>
  );
}
