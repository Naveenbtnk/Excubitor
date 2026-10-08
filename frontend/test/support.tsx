/**
 * Shared plumbing for the page tests.
 *
 * Every page test drives the real page through a stubbed `fetch`, so the rendering, the SWR
 * wiring and the request the page builds are all in the loop. The two things that are easy to
 * get subtly wrong are handled here once:
 *
 * - each render gets its own SWR cache. SWR keys by path, so without an isolated provider the
 *   second test in a file is served the first test's data -- and then asserts against a page
 *   that never asked the stub anything.
 * - a request with no matching route answers 404 with its own path in the body, so a missing
 *   stub shows up as a failing assertion instead of a silently empty page.
 */

import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { SWRConfig } from "swr";
import { vi } from "vitest";

/** The reason the UI puts on a control the current role may not use, matching the API's 403. */
export const VIEWER_REASON = "Requires the operator role or higher.";

export interface RecordedCall {
  url: string;
  method: string;
  body: string | undefined;
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** A page envelope as `usePaged` reads it. */
export function pageOf<T>(items: T[], total = items.length, limit = 25) {
  return { items, total, limit, offset: 0, has_more: items.length < total };
}

export function sessionResponse(role: string): Response {
  return jsonResponse({ id: 2, email: "ops@cloudops.local", name: "Ops Person", role });
}

/** Return a response for a request, or `undefined` when the request is not one of yours. */
export type RouteHandler = (url: string, method: string) => Response | undefined;

/**
 * Install a stubbed `fetch` that answers `/auth/me` with `role` and everything else through
 * `route`, recording every call. The order of checks inside a handler matters: `/alerts`
 * also matches `/alerts/scenarios`, so the specific path has to come first.
 */
export function stubApi(role: string, route: RouteHandler): RecordedCall[] {
  const calls: RecordedCall[] = [];

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: typeof init?.body === "string" ? init.body : undefined });

      if (url.includes("/auth/me")) return sessionResponse(role);

      const response = route(url, method);
      if (response) return response;
      return jsonResponse({ detail: `Unstubbed request: ${method} ${url}` }, 404);
    })
  );

  return calls;
}

/** Render a page in its own SWR cache. */
export function renderPage(ui: ReactElement) {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{ui}</SWRConfig>
  );
}

/** The calls that changed something, which is what a read-only role must never produce. */
export function writes(calls: RecordedCall[]): RecordedCall[] {
  return calls.filter((call) => call.method !== "GET");
}
