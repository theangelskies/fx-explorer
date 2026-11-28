import { useNavigate } from "react-router";

import { Card } from "../Card";
import { AskInput } from "./AskInput";
import { StarterQuestions } from "./StarterQuestions";

/** Compact "Ask your data" card for the dashboard. It hands the question to the Ask page. */
export function AskPanel() {
  const navigate = useNavigate();
  const go = (question: string) => navigate("/ask", { state: { question } });
  return (
    <Card title="Ask your data">
      <AskInput onSend={go} rows={2} placeholder="e.g. How has EUR→NGN changed over 3 years?" />
      <div className="mt-3">
        <StarterQuestions onPick={go} limit={3} />
      </div>
    </Card>
  );
}
