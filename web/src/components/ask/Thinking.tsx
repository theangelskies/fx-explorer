import { useEffect, useState } from "react";

import { thinkingStages } from "../../lib/agent";
import type { Currency } from "../../api/queries";

/**
 * "Thinking" state: a status line that changes while the agent works ("Checking EUR→NGN…").
 * The API answers in one go, so the wording comes from the question and the clock.
 */
export function Thinking({ question, currencies }: { question: string; currencies: Currency[] }) {
  const [stage, setStage] = useState(0);
  useEffect(() => {
    const stages = thinkingStages(question, currencies);
    const timers = stages.map((s, i) => window.setTimeout(() => setStage(i), s.afterMs));
    return () => timers.forEach(window.clearTimeout);
  }, [question, currencies]);

  const text = thinkingStages(question, currencies)[stage]!.text;
  return (
    <div role="status" aria-live="polite" className="flex items-center gap-3 text-sm text-muted">
      <span aria-hidden="true" className="flex gap-1">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="size-1.5 rounded-full bg-accent motion-safe:animate-pulse"
            style={{ animationDelay: `${i * 200}ms` }}
          />
        ))}
      </span>
      <span>{text}</span>
    </div>
  );
}
