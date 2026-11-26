import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/PageHeader";

export function RatesPage() {
  return (
    <>
      <PageHeader title="Rates" />
      <EmptyState title="Rates explorer coming soon" hint="Built in Phase 13 (docs/ui-design.md §4.2)." />
    </>
  );
}
