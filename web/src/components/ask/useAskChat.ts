import { useCallback, useRef, useState } from "react";

import { useAskAgent, type AgentAnswer } from "../../api/agent";
import { ApiError, describeError, type DisplayProblem } from "../../api/errors";

export type ChatItem =
  | { id: number; kind: "user"; text: string }
  | { id: number; kind: "answer"; answer: AgentAnswer }
  | { id: number; kind: "error"; problem: DisplayProblem; question: string };

/**
 * The conversation on screen: the thread, the conversation id the API gave us, and whether the
 * agent is working. The API keeps the history (ADR 0009); we only send the id and the question.
 */
export function useAskChat() {
  const [items, setItems] = useState<ChatItem[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [working, setWorking] = useState<string | null>(null); // the question being answered
  const nextId = useRef(1);
  const ask = useAskAgent();

  const run = useCallback(
    (question: string, idToUse: string | null) => {
      setWorking(question);
      ask.mutate(
        { question, conversationId: idToUse },
        {
          onSuccess: (answer) => {
            setConversationId(answer.conversation_id);
            setItems((list) => [...list, { id: nextId.current++, kind: "answer", answer }]);
          },
          onError: (error) => {
            // 404 = the API no longer knows this conversation: the next try starts a new one.
            if (error instanceof ApiError && error.status === 404 && idToUse) setConversationId(null);
            setItems((list) => [...list, { id: nextId.current++, kind: "error", problem: describeError(error), question }]);
          },
          onSettled: () => setWorking(null),
        },
      );
    },
    [ask],
  );

  /** Send a new question. */
  const send = useCallback(
    (raw: string) => {
      const question = raw.trim();
      if (!question || working) return;
      setItems((list) => [...list, { id: nextId.current++, kind: "user", text: question }]);
      run(question, conversationId);
    },
    [conversationId, run, working],
  );

  /** Try the failed question again (its bubble is already on screen). */
  const retry = useCallback(
    (errorId: number) => {
      const failed = items.find((i) => i.id === errorId);
      if (!failed || failed.kind !== "error" || working) return;
      setItems((list) => list.filter((i) => i.id !== errorId));
      run(failed.question, conversationId);
    },
    [conversationId, items, run, working],
  );

  const reset = useCallback(() => {
    setItems([]);
    setConversationId(null);
  }, []);

  return { items, working, send, retry, reset };
}
