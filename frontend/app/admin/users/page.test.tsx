import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import UsersPage from "./page";

/**
 * The user-management page against a stubbed API. Two things matter: an admin can see and
 * change accounts, and anyone else sees the reason rather than a roster -- the second is
 * asserted on the requests, not just on the pixels.
 */

interface RecordedCall {
  url: string;
  method: string;
  body: string | undefined;
}

const ACCOUNTS = [
  { id: 1, email: "admin@cloudops.local", name: "Cloud Admin", role: "admin" },
  { id: 2, email: "ops@cloudops.local", name: "Ops Person", role: "operator" },
];

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stubApi(sessionRole: string) {
  const calls: RecordedCall[] = [];

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({
        url,
        method,
        body: typeof init?.body === "string" ? init.body : undefined,
      });

      if (url.includes("/auth/me")) {
        return jsonResponse({
          id: 1,
          email: "admin@cloudops.local",
          name: "Cloud Admin",
          role: sessionRole,
        });
      }
      if (url.includes("/users") && method === "GET") {
        return jsonResponse({
          items: ACCOUNTS,
          total: ACCOUNTS.length,
          limit: 50,
          offset: 0,
          has_more: false,
        });
      }
      if (url.includes("/users") && method === "POST") {
        return jsonResponse(
          { id: 3, email: "new@cloudops.local", name: "New", role: "viewer" },
          201
        );
      }
      // Anything else is the PATCH response: the account as it now stands.
      return jsonResponse({ ...ACCOUNTS[1] });
    })
  );

  return { calls, requests: () => calls };
}

/** A fresh SWR cache per render, so one test's roster cannot leak into the next. */
function renderPage() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <UsersPage />
    </SWRConfig>
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("as an admin", () => {
  it("lists the accounts with their roles", async () => {
    stubApi("admin");
    renderPage();

    expect(await screen.findByText("ops@cloudops.local")).toBeInTheDocument();
    expect(screen.getByText("admin@cloudops.local")).toBeInTheDocument();
    expect(screen.getByLabelText("Role for ops@cloudops.local")).toHaveValue("operator");
  });

  it("patches a role when the select changes", async () => {
    const { requests } = stubApi("admin");
    renderPage();

    const select = await screen.findByLabelText("Role for ops@cloudops.local");
    fireEvent.change(select, { target: { value: "admin" } });

    await waitFor(() => {
      const patch = requests().find(
        (call) => call.method === "PATCH" && call.url.includes("/users/2")
      );
      expect(patch).toBeDefined();
      expect(JSON.parse(patch!.body ?? "{}")).toEqual({ role: "admin" });
    });
  });

  it("creates an account with the role that was chosen", async () => {
    const { requests } = stubApi("admin");
    renderPage();

    fireEvent.change(await screen.findByLabelText("Email"), {
      target: { value: "new@cloudops.local" },
    });
    fireEvent.change(screen.getByLabelText("Display name"), {
      target: { value: "New Person" },
    });
    fireEvent.change(screen.getByLabelText("Temporary password"), {
      target: { value: "sup3r-secret" },
    });
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "operator" } });
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));

    await waitFor(() => {
      const post = requests().find(
        (call) => call.method === "POST" && call.url.includes("/users")
      );
      expect(post).toBeDefined();
      expect(JSON.parse(post!.body ?? "{}")).toMatchObject({
        email: "new@cloudops.local",
        name: "New Person",
        role: "operator",
      });
    });
    // The form is cleared once the account exists.
    await waitFor(() => expect(screen.getByLabelText("Email")).toHaveValue(""));
  });

  it("refuses a password the API would reject anyway", async () => {
    const { requests } = stubApi("admin");
    renderPage();

    fireEvent.change(await screen.findByLabelText("Email"), {
      target: { value: "short@cloudops.local" },
    });
    fireEvent.change(screen.getByLabelText("Temporary password"), {
      target: { value: "short" },
    });
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));

    expect(await screen.findByText(/at least 8 characters/i)).toBeInTheDocument();
    expect(
      requests().filter((call) => call.method === "POST" && call.url.includes("/users"))
    ).toHaveLength(0);
  });
});

describe("as anyone else", () => {
  it("shows the reason and never asks for the roster", async () => {
    const { requests } = stubApi("viewer");
    renderPage();

    expect(await screen.findByText("Requires the admin role or higher.")).toBeInTheDocument();
    expect(screen.queryByText("ops@cloudops.local")).not.toBeInTheDocument();

    const asked = requests().filter((call) => call.url.includes("/users"));
    expect(asked).toHaveLength(0);
  });
});
