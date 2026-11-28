import type { AgentAnswer, AgentToolCall } from "../../api/agent";

function statusText(call: AgentToolCall): string {
  if (call.status === "pending") return "proposed, not executed";
  if (call.status === null) return "no response";
  return call.is_error ? `error ${call.status}` : `${call.status} OK`;
}

/** A collapsible record of what the agent did: each tool, its request, status and a short result. */
export function HowIGotThis({ steps, usage }: Pick<AgentAnswer, "steps" | "usage">) {
  const calls = steps.reduce((n, s) => n + s.tool_calls.length, 0);
  return (
    <details className="group mt-3 text-sm">
      <summary className="cursor-pointer select-none text-xs font-medium text-muted hover:text-fg">
        How I got this ({calls === 0 ? "no lookups" : `${calls} ${calls === 1 ? "lookup" : "lookups"}`})
      </summary>
      <ol className="mt-2 space-y-3 border-l border-line pl-4">
        {steps.map((step) => (
          <li key={step.step}>
            <p className="text-xs text-muted">
              Step {step.step} · {step.input_tokens + step.output_tokens} tokens ·{" "}
              {(step.duration_ms / 1000).toFixed(1)} s
            </p>
            {step.model_text && <p className="mt-1 italic">{step.model_text}</p>}
            {step.tool_calls.map((call, i) => (
              <div key={i} className="mt-1 rounded-md bg-bg px-3 py-2">
                <p>
                  <span className="font-mono text-xs font-semibold">{call.tool}</span>{" "}
                  <span className="text-xs text-muted">({statusText(call)})</span>
                </p>
                <p className="mt-0.5 break-all font-mono text-xs">{call.request ?? JSON.stringify(call.input)}</p>
                <p className="mt-0.5 break-words text-xs text-muted">{call.result_summary}</p>
              </div>
            ))}
          </li>
        ))}
      </ol>
      <p className="mt-2 text-xs text-muted">
        {usage.input_tokens} in / {usage.output_tokens} out tokens
        {usage.cost_usd !== null && ` · about $${Number(usage.cost_usd).toFixed(4)}`} ·{" "}
        {(usage.duration_ms / 1000).toFixed(1)} s in total
      </p>
    </details>
  );
}
