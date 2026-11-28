/**
 * The ONE way the app talks to the API.
 *
 * `schema.d.ts` is generated from ../openapi.yaml (`npm run gen:api`), so every path, parameter
 * and response here is typed by the contract: a renamed field or a wrong parameter is a
 * compile error, not a blank screen.
 */
import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type Problem = Schemas["Problem"];

export const api = createClient<paths>({
  // Same origin: in development Vite forwards /v1 to the API (vite.config.ts).
  baseUrl: "",
  // The contract sends lists as ONE comma-separated value (style: form, explode: false),
  // e.g. ?quotes=USD,GBP. openapi-fetch doesn't read that from the spec and would send
  // ?quotes=USD&quotes=GBP by default, so we tell it explicitly.
  querySerializer: { array: { style: "form", explode: false } },
});
