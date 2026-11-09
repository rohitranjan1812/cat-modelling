import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

/** Typed client generated from schemas/openapi.json — the backend is the source of truth. */
export const api = createClient<paths>({ baseUrl: import.meta.env.VITE_API_URL ?? "" });

// Derived from the client's own response type so it matches exactly what openapi-fetch yields.
const listPortfolios = () => api.GET("/api/v1/portfolios");
export type PortfolioMeta = NonNullable<Awaited<ReturnType<typeof listPortfolios>>["data"]>[number];
export type AggregateRow = components["schemas"]["AggregateRow"];
export type CodeLists = components["schemas"]["CodeLists"];
export type Dimension = NonNullable<
  NonNullable<paths["/api/v1/portfolios/{pid}/aggregate"]["get"]["parameters"]["query"]>["by"]
>;
