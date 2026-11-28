import type { AgentAnswer } from "../../api/agent";
import { Disclaimer } from "../Notices";
import { HowIGotThis } from "./HowIGotThis";
import { PendingActionCard } from "./PendingActionCard";
import { SourceChips } from "./SourceChips";

/** One answer from the agent: text, sources, the honesty note for trends, the steps. */
export function AnswerCard({ answer }: { answer: AgentAnswer }) {
  // The trend note (ADR 0005) appears whenever the agent actually looked at a trend.
  const usedTrend = answer.steps.some((s) => s.tool_calls.some((c) => c.tool === "get_pair_trend" && !c.is_error));
  return (
    <div className="max-w-[44rem] rounded-xl rounded-tl-sm border border-line bg-surface px-4 py-3">
      <p className="whitespace-pre-wrap text-sm leading-relaxed">{answer.answer}</p>
      {answer.status === "max_steps" && (
        <p role="note" className="mt-2 text-xs text-notice-fg">
          The assistant ran out of steps, so this may be incomplete. Try a narrower question.
        </p>
      )}
      {answer.status === "refused" && (
        <p role="note" className="mt-2 text-xs text-notice-fg">The model declined to answer this.</p>
      )}
      <SourceChips sources={answer.sources} />
      {usedTrend && (
        <div className="mt-3">
          <Disclaimer />
        </div>
      )}
      {answer.pending_action && <PendingActionCard action={answer.pending_action} />}
      <HowIGotThis steps={answer.steps} usage={answer.usage} />
    </div>
  );
}
