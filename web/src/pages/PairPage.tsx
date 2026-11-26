import { useParams } from "react-router";

import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/PageHeader";

export function PairPage() {
  const { base = "", quote = "" } = useParams();
  return (
    <>
      <PageHeader title={`${base} / ${quote}`} />
      <EmptyState title="Pair detail coming soon" hint="Built in Phase 13 (docs/ui-design.md §4.3)." />
    </>
  );
}
