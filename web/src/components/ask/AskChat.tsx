import { useEffect, useRef } from "react";

import { useCurrencies } from "../../api/queries";
import { EmptyState } from "../EmptyState";
import { ErrorState } from "../ErrorState";
import { AnswerCard } from "./AnswerCard";
import { AskInput } from "./AskInput";
import { StarterQuestions } from "./StarterQuestions";
import { Thinking } from "./Thinking";
import { useAskChat } from "./useAskChat";

/**
 * The whole chat: thread, states and input. States (docs/ui-design.md §4.5):
 *   empty (starter questions) · thinking · answer · error (with Retry).
 */
export function AskChat({ initialQuestion }: { initialQuestion?: string }) {
  const chat = useAskChat();
  const { send } = chat;
  const currencies = useCurrencies({ hasRates: true });
  const endRef = useRef<HTMLDivElement>(null);
  const sentInitial = useRef(false);

  // A question handed over from the dashboard panel: send it once.
  useEffect(() => {
    if (initialQuestion && !sentInitial.current) {
      sentInitial.current = true;
      send(initialQuestion);
    }
  }, [initialQuestion, send]);

  // Keep the newest message in view.
  useEffect(() => {
    const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    endRef.current?.scrollIntoView({ block: "end", behavior: calm ? "auto" : "smooth" });
  }, [chat.items.length, chat.working]);

  return (
    <div className="flex flex-col gap-4">
      {chat.items.length > 0 && (
        <div className="flex justify-end">
          <button
            type="button"
            onClick={chat.reset}
            disabled={chat.working !== null}
            className="rounded-md border border-line-input px-2.5 py-1 text-xs text-muted hover:text-fg disabled:opacity-50"
          >
            New conversation
          </button>
        </div>
      )}
      <div role="log" aria-live="polite" aria-label="Conversation" className="flex min-h-[16rem] flex-col gap-4">
        {chat.items.length === 0 && !chat.working && (
          <EmptyState
            title="Ask about your exchange rates"
            hint="Answers come only from the rates stored in this app, with the sources shown. It can describe what rates did, but it can't predict them or give financial advice."
            action={<StarterQuestions onPick={chat.send} />}
          />
        )}
        {chat.items.map((item) =>
          item.kind === "user" ? (
            <div key={item.id} className="max-w-[40rem] self-end rounded-xl rounded-tr-sm bg-accent px-4 py-2 text-sm text-on-accent">
              <p className="whitespace-pre-wrap">{item.text}</p>
            </div>
          ) : item.kind === "answer" ? (
            <AnswerCard key={item.id} answer={item.answer} />
          ) : (
            <div key={item.id} className="max-w-[44rem]">
              <ErrorState problem={item.problem} onRetry={() => chat.retry(item.id)} />
            </div>
          ),
        )}
        {chat.working && <Thinking question={chat.working} currencies={currencies.data ?? []} />}
        <div ref={endRef} />
      </div>
      <AskInput onSend={chat.send} disabled={chat.working !== null} autoFocus />
    </div>
  );
}
