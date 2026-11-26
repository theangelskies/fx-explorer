/**
 * Turning every kind of failure into something the UI can show.
 *
 * The API always answers errors with RFC 9457 Problem Details (ADR 0002). But the browser can
 * also fail BEFORE reaching it: no network, or the API isn't running and the dev proxy answers
 * with its own non-JSON error. `call()` makes all of these look the same to the UI.
 */
import type { Problem } from "./client";

/** What <ErrorState> needs. A real Problem fits; so does our "can't reach the API" message. */
export interface DisplayProblem {
  title: string;
  detail: string;
  status?: number;
  type?: Problem["type"];
  errors?: Problem["errors"];
}

export class ApiError extends Error {
  readonly status: number; // 0 = never got an HTTP answer from the API
  readonly problem: DisplayProblem;

  constructor(status: number, problem: DisplayProblem, cause?: unknown) {
    super(problem.title, { cause });
    this.name = "ApiError";
    this.status = status;
    this.problem = problem;
  }
}

export const API_UNREACHABLE: DisplayProblem = {
  title: "Can't reach the API",
  detail: "Is it running? Start it with: uv run uvicorn fx_explorer.api.app:app --reload",
};

function isProblem(value: unknown): value is Problem {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as Problem).title === "string" &&
    typeof (value as Problem).status === "number"
  );
}

/**
 * Await an openapi-fetch request: return its data, or throw an ApiError.
 *
 *   const rates = await call(api.GET("/v1/rates", { params: { query: { base: "EUR" } } }));
 */
export async function call<T>(
  request: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  let result: { data?: T; error?: unknown; response: Response };
  try {
    result = await request;
  } catch (cause) {
    throw new ApiError(0, API_UNREACHABLE, cause); // network error: fetch itself failed
  }
  const { data, error, response } = result;
  if (response.ok) return data as T;
  if (isProblem(error)) throw new ApiError(response.status, error);
  // Not a Problem: something between us and the API answered (e.g. the dev proxy when the API
  // is down gives a plain 500/502/504).
  if (response.status >= 500) throw new ApiError(0, API_UNREACHABLE);
  throw new ApiError(response.status, {
    title: `Unexpected response (${response.status})`,
    detail: "The server answered in a format this app doesn't understand.",
    status: response.status,
  });
}

/** Any thrown value -> something <ErrorState> can display. */
export function describeError(error: unknown): DisplayProblem {
  if (error instanceof ApiError) return error.problem;
  return {
    title: "Something went wrong",
    detail: error instanceof Error ? error.message : "An unexpected error occurred.",
  };
}
