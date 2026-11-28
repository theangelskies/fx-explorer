import { useRef, useState, type FormEvent, type KeyboardEvent } from "react";

const MAX = 1000; // the contract's maxLength for a question
const NEAR = 800;

interface AskInputProps {
  onSend: (question: string) => void;
  disabled?: boolean;
  placeholder?: string;
  autoFocus?: boolean;
  rows?: number;
}

/** Question box. Enter sends, Shift+Enter adds a line. */
export function AskInput({ onSend, disabled, placeholder = "Ask about the stored rates…", autoFocus, rows = 2 }: AskInputProps) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  const canSend = text.trim().length > 0 && !disabled;

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    if (!canSend) return;
    onSend(text);
    setText("");
    ref.current?.focus();
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <form onSubmit={submit} className="flex items-end gap-2">
      <div className="flex-1">
        <label htmlFor="ask-input" className="sr-only">
          Your question
        </label>
        <textarea
          id="ask-input"
          ref={ref}
          value={text}
          rows={rows}
          maxLength={MAX}
          autoFocus={autoFocus}
          placeholder={placeholder}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          className="w-full resize-none rounded-md border border-line-input bg-surface px-3 py-2 text-sm"
        />
        {text.length >= NEAR && (
          <p className="text-right text-xs text-muted" aria-live="polite">
            {text.length}/{MAX}
          </p>
        )}
      </div>
      <button
        type="submit"
        disabled={!canSend}
        className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-on-accent hover:opacity-90 disabled:opacity-50"
      >
        Send
      </button>
    </form>
  );
}
