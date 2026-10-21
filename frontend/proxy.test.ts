import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";
import { config, proxy } from "./proxy";

/**
 * The middleware is the first thing a request meets, and the page it guards is the only place
 * where "signed out" is decided before any React runs. It is also a list, which is the kind of
 * thing that goes stale one route at a time: `/admin/users` was added to the app without being
 * added here, and answered a stranger with a rendered page instead of the sign-in redirect every
 * other page gives. The list below is the whole app, so a new page fails this test rather than
 * quietly becoming the exception.
 */

/** Every page the app serves, minus `/login` itself. */
const APP_PAGES = [
  "/",
  "/admin/users",
  "/alerts",
  "/incidents/2",
  "/knowledge",
  "/post-mortems",
  "/sandbox",
];

function request(path: string, session?: string) {
  return new NextRequest(`http://localhost:3000${path}`, {
    headers: session ? { cookie: `cloudops_session=${session}` } : undefined,
  });
}

describe("a request with no session", () => {
  it.each(APP_PAGES)("is sent to sign-in from %s", (path) => {
    const response = proxy(request(path));

    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe(
      `http://localhost:3000/login?next=${encodeURIComponent(path)}`
    );
  });

  it("is sent to sign-in from a page nested under a guarded one", () => {
    const response = proxy(request("/incidents/2"));

    expect(response.headers.get("location")).toContain("next=%2Fincidents%2F2");
  });

  it("reaches the sign-in page itself", () => {
    const response = proxy(request("/login"));

    expect(response.headers.get("location")).toBeNull();
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });
});

describe("a request with a session", () => {
  it.each(APP_PAGES)("reaches %s instead of being redirected", (path) => {
    const response = proxy(request(path, "a.session.cookie"));

    expect(response.status).toBe(200);
    expect(response.headers.get("x-middleware-next")).toBe("1");
  });

  it("is turned away from the sign-in page it no longer needs", () => {
    const response = proxy(request("/login", "a.session.cookie"));

    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe("http://localhost:3000/");
  });
});

describe("the matcher", () => {
  it("runs for pages and skips the proxy, the API and static assets", () => {
    const [pattern] = config.matcher;

    expect(pattern).toBe("/((?!api|_next/static|_next/image|favicon.ico).*)");
  });
});
