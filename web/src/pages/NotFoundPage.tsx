import { Link } from "react-router";

import { EmptyState } from "../components/EmptyState";

export function NotFoundPage() {
  return (
    <EmptyState
      title="Page not found"
      hint="That address doesn't exist in fx-explorer."
      action={<Link to="/" className="text-accent underline underline-offset-2">Go to the dashboard</Link>}
    />
  );
}
