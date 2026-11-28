import { STARTER_QUESTIONS } from "../../lib/agent";

/** Suggested questions: the empty state of the Ask page, and the dashboard panel. */
export function StarterQuestions({ onPick, limit, disabled }: { onPick: (q: string) => void; limit?: number; disabled?: boolean }) {
  return (
    <ul className="flex flex-wrap gap-2" aria-label="Suggested questions">
      {STARTER_QUESTIONS.slice(0, limit).map((q) => (
        <li key={q}>
          <button
            type="button"
            disabled={disabled}
            onClick={() => onPick(q)}
            className="rounded-full border border-line px-3 py-1.5 text-left text-sm hover:border-accent hover:text-accent disabled:opacity-50"
          >
            {q}
          </button>
        </li>
      ))}
    </ul>
  );
}
