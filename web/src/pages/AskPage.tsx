/**
 * Ask your data (docs/ui-design.md §4.5): a chat with the analyst agent.
 * Operations: askAgent, confirmAgentAction, cancelAgentAction (ADR 0008, 0009); listCurrencies
 * (only to word the "Checking EUR→NGN…" hint).
 */
import { useLocation, useNavigate } from "react-router";
import { useEffect, useState } from "react";

import { AskChat } from "../components/ask/AskChat";
import { Card } from "../components/Card";
import { PageHeader } from "../components/PageHeader";

export function AskPage() {
  const location = useLocation();
  const navigate = useNavigate();
  // The dashboard panel hands over its question through the navigation state.
  const [handedOver] = useState(() => (location.state as { question?: string } | null)?.question);

  useEffect(() => {
    // Forget the state, so refreshing this page doesn't send the question a second time.
    if (handedOver) navigate(location.pathname, { replace: true, state: null });
  }, [handedOver, location.pathname, navigate]);

  return (
    <>
      <PageHeader title="Ask your data" />
      <Card>
        <AskChat initialQuestion={handedOver} />
      </Card>
      <p className="mt-3 text-xs text-muted">
        An AI assistant reads the stored reference rates through this app's own API. Check the sources: it can make
        mistakes. It does not predict rates or give financial advice.
      </p>
    </>
  );
}
