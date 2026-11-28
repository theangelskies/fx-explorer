/**
 * The "Ask your data" operations (askAgent, confirmAgentAction, cancelAgentAction), typed by the
 * contract. Pages use these hooks; they never call fetch themselves.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api, type Schemas } from "./client";
import { call, callWithResponse } from "./errors";

export type AgentAnswer = Schemas["AgentAnswer"];
export type AgentStep = Schemas["AgentStep"];
export type AgentToolCall = Schemas["AgentToolCall"];
export type AgentPendingAction = Schemas["AgentPendingAction"];
export type Alert = Schemas["Alert"];

/** askAgent. `conversationId` null starts a new conversation. */
export function useAskAgent() {
  return useMutation({
    mutationFn: ({ question, conversationId }: { question: string; conversationId: string | null }) =>
      call(api.POST("/v1/agent/ask", { body: { question, conversation_id: conversationId } })),
  });
}

/**
 * confirmAgentAction: creates the alert the agent proposed. Confirming twice is safe (the API
 * replays the stored alert), so `replayed` tells us when no new alert was made.
 */
export function useConfirmAction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { data, response } = await callWithResponse(
        api.POST("/v1/agent/actions/{id}/confirm", { params: { path: { id } } }),
      );
      return { alert: data, replayed: response.headers.get("Idempotent-Replayed") === "true" };
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["alerts"] }),
  });
}

/** cancelAgentAction: nothing is created. */
export function useCancelAction() {
  return useMutation({
    mutationFn: (id: string) => call(api.POST("/v1/agent/actions/{id}/cancel", { params: { path: { id } } })),
  });
}
