/**
 * Create-alert form (docs/ui-design.md §4.4).
 *
 * - Sends what the user typed; the API validates. A 422's errors[] are shown under the input whose
 *   name matches `field`. No validation rules are duplicated here.
 * - Retry/double-click safe (ADR 0006): an Idempotency-Key per form CONTENT. Editing any field makes
 *   a new key (it's a different request now); a resubmit of unchanged content reuses it.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";

import { api } from "../api/client";
import { ApiError, callWithResponse, describeError, type DisplayProblem } from "../api/errors";
import { useCurrencies, usePair, type AlertCreate } from "../api/queries";
import { formatDate, formatDecimal } from "../lib/format";

type FormValues = { base: string; quote: string; condition: AlertCreate["condition"]; threshold: string; note: string };
const FIELDS = ["base", "quote", "condition", "threshold", "note"] as const;
const NOTE_HINT = 200; // shown as a counter only; the API enforces the real limit

function newIdempotencyKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  // randomUUID needs a secure context (https or localhost); fall back to random bytes elsewhere.
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

const control =
  "w-full rounded-md border border-line-input bg-surface px-2.5 py-1.5 text-sm aria-[invalid=true]:border-down";

function Field({ id, label, error, children, hint }: { id: string; label: string; error?: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-xs font-medium text-muted">{label}</label>
      {children}
      {error && <p id={`${id}-error`} className="mt-1 text-xs text-down">⚠ {error}</p>}
      {hint && !error && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  );
}

export function AlertForm() {
  const queryClient = useQueryClient();
  const currencies = useCurrencies({ hasRates: true });
  const [values, setValues] = useState<FormValues>({ base: "EUR", quote: "USD", condition: "above", threshold: "", note: "" });
  const [key, setKey] = useState(newIdempotencyKey);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<DisplayProblem | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const pairChosen = values.base !== "" && values.quote !== "" && values.base !== values.quote;
  const pair = usePair(values.base, values.quote, "7d", undefined, { enabled: pairChosen });

  const update = <K extends keyof FormValues>(field: K, value: FormValues[K]) => {
    setValues((v) => ({ ...v, [field]: value }));
    setKey(newIdempotencyKey()); // different content = a different request
    setFieldErrors(({ [field]: _removed, ...rest }) => rest);
    setSuccess(null);
  };

  const create = useMutation({
    mutationFn: async ({ body, idempotencyKey }: { body: AlertCreate; idempotencyKey: string }) => {
      const send = () =>
        callWithResponse(api.POST("/v1/alerts", { body, params: { header: { "Idempotency-Key": idempotencyKey } } }));
      try {
        return await send();
      } catch (error) {
        // 409: the same request is still being processed (e.g. a double-click). Wait, then retry
        // with the SAME key: we get the stored result instead of a duplicate.
        if (error instanceof ApiError && error.status === 409) {
          await sleep(1000);
          return await send();
        }
        throw error;
      }
    },
    onSuccess: ({ data, response }) => {
      const replayed = response.headers.get("Idempotent-Replayed") === "true";
      setSuccess(
        `${replayed ? "This alert was already created (no duplicate made)" : "Alert created"}: ` +
          `${data.base}/${data.quote} ${data.condition} ${formatDecimal(data.threshold)}.`,
      );
      setValues((v) => ({ ...v, threshold: "", note: "" }));
      setKey(newIdempotencyKey());
      void queryClient.invalidateQueries({ queryKey: ["alerts"] }); // the list re-reads live values
    },
    onError: (error) => {
      const problem = describeError(error);
      const perField: Record<string, string> = {};
      const leftover: { field: string; message: string }[] = [];
      for (const e of problem.errors ?? []) {
        if ((FIELDS as readonly string[]).includes(e.field)) perField[e.field] = e.message;
        else leftover.push(e); // "body" or a field the form doesn't have
      }
      setFieldErrors(perField);
      const onlyFieldErrors = problem.type === "/problems/validation-error" && leftover.length === 0;
      setFormError(onlyFieldErrors ? null : { ...problem, errors: leftover });
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setFieldErrors({});
    setFormError(null);
    setSuccess(null);
    create.mutate({
      body: {
        base: values.base,
        quote: values.quote,
        condition: values.condition,
        threshold: values.threshold, // exactly as typed: the API checks the format
        note: values.note === "" ? null : values.note,
      },
      idempotencyKey: key,
    });
  };

  const aria = (field: string) => ({
    "aria-invalid": fieldErrors[field] ? true : undefined,
    "aria-describedby": fieldErrors[field] ? `a-${field}-error` : undefined,
  });
  const options = currencies.data ?? [];
  const currencyOptions = (current: string) => (
    <>
      {!options.some((c) => c.iso_code === current) && <option value={current}>{current}</option>}
      {options.map((c) => (
        <option key={c.iso_code} value={c.iso_code}>{c.iso_code} · {c.name}</option>
      ))}
    </>
  );

  let pairHint: ReactNode = null;
  if (pairChosen) {
    if (pair.isPending) pairHint = "Looking up the current rate…";
    else if (pair.isError) pairHint = pair.error instanceof ApiError && pair.error.status === 404
      ? "No rates are stored for this pair."
      : "Couldn't load the current rate.";
    else pairHint = <>Current rate <strong className="tabular-nums text-fg">{formatDecimal(pair.data.latest.rate)}</strong> ({formatDate(pair.data.latest.rate_date)})</>;
  }

  return (
    <form onSubmit={submit} noValidate aria-label="New alert" className="space-y-4">
      {formError && (
        <div role="alert" className="rounded-lg border border-down/40 bg-down/5 px-4 py-3 text-sm">
          <p className="font-semibold text-down">{formError.title}</p>
          <p>{formError.detail}</p>
          {formError.errors?.map((e) => <p key={e.field}>{e.message}</p>)}
        </div>
      )}
      {success && (
        <p role="status" className="rounded-lg border border-up/40 bg-up/5 px-4 py-3 text-sm text-up">✓ {success}</p>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-[1fr_1fr_10rem_12rem]">
        <Field id="a-base" label="Base" error={fieldErrors.base}>
          <select id="a-base" className={control} value={values.base} onChange={(e) => update("base", e.target.value)} {...aria("base")}>
            {currencyOptions(values.base)}
          </select>
        </Field>
        <Field id="a-quote" label="Quote" error={fieldErrors.quote} hint={pairHint}>
          <select id="a-quote" className={control} value={values.quote} onChange={(e) => update("quote", e.target.value)} {...aria("quote")}>
            {currencyOptions(values.quote)}
          </select>
        </Field>
        <Field id="a-condition" label="When the rate" error={fieldErrors.condition}>
          <select id="a-condition" className={control} value={values.condition}
            onChange={(e) => update("condition", e.target.value as FormValues["condition"])} {...aria("condition")}>
            <option value="above">reaches or goes above</option>
            <option value="below">reaches or goes below</option>
          </select>
        </Field>
        <Field id="a-threshold" label="Threshold" error={fieldErrors.threshold} hint='e.g. 1.1500'>
          <input id="a-threshold" className={`${control} tabular-nums`} inputMode="decimal" autoComplete="off"
            value={values.threshold} onChange={(e) => update("threshold", e.target.value)} {...aria("threshold")} />
        </Field>
      </div>
      <Field id="a-note" label="Note (optional)" error={fieldErrors.note}
        hint={<span className="tabular-nums">{values.note.length}/{NOTE_HINT}</span>}>
        <input id="a-note" className={control} value={values.note} onChange={(e) => update("note", e.target.value)} {...aria("note")} />
      </Field>

      <div className="flex justify-end">
        <button type="submit" disabled={create.isPending}
          className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-on-accent hover:opacity-90 disabled:cursor-wait disabled:opacity-60">
          {create.isPending ? "Creating…" : "Create alert"}
        </button>
      </div>
    </form>
  );
}
