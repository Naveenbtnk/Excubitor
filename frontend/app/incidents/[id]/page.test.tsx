import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import IncidentDetailPage from "./page";

/**
 * The incident detail page against a stubbed API. The route id comes from `next/navigation`
 * and the two clicks that matter -- a status change and a comment -- are asserted on the
 * requests that leave the page, not only on what the page renders: a control that looks
 * disabled but still sends a write is the failure this page has to not have.
 */

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "7" }),
}));

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

interface RecordedCall {
  url: string;
  method: string;
  body: string | undefined;
}

const INCIDENT = {
  id: 7,
  alert_id: 3,
  title: "Database pool exhaustion",
  summary: "Pool exhausted on orders-api",
  root_cause: "Connection leak",
  recommendation: "1. Drain idle sessions\n2. Restart orders-api",
  confidence: 0.75,
  status: "triage",
  assignee: null as string | null,
  created_at: "2026-10-08T09:12:44Z",
};

const EVENTS = [
  {
    id: 1,
    incident_id: 7,
    kind: "created",
    actor: "admin@cloudops.local",
    from_value: null,
    to_value: "triage",
    body: "Created by AI triage from the alert “Database pool exhaustion”, with 75% confidence.",
    created_at: "2026-10-08T09:12:45Z",
  },
  {
    id: 2,
    incident_id: 7,
    kind: "status",
    actor: "ops@cloudops.local",
    from_value: "triage",
    to_value: "investigating",
    body: "Status changed from triage to investigating.",
    created_at: "2026-10-08T09:20:00Z",
  },
];

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stubApi(sessionRole: string, incident: Record<string, unknown> = INCIDENT) {
  const calls: RecordedCall[] = [];

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: typeof init?.body === "string" ? init.body : undefined });

      if (url.includes("/auth/me")) {
        return jsonResponse({
          id: 2,
          email: "ops@cloudops.local",
          name: "Ops Person",
          role: sessionRole,
        });
      }
      if (url.includes("/activity")) {
        return jsonResponse({
          items: EVENTS,
          total: EVENTS.length,
          limit: 25,
          offset: 0,
          has_more: false,
        });
      }
      if (url.includes("/comments")) {
        return jsonResponse({ ...EVENTS[1], id: 3, kind: "comment", body: "Over to you." }, 201);
      }
      if (method === "PATCH") {
        return jsonResponse({ ...incident, status: "investigating" });
      }
      return jsonResponse(incident);
    })
  );

  return { calls };
}

function renderPage() {
  // A fresh cache per render: SWR keys by path, so without this the second test would be
  // served the first test's incident and role -- and would then assert against a page that
  // never asked the stubbed API anything.
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <IncidentDetailPage />
    </SWRConfig>
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("an operator", () => {
  it("renders the incident and its timeline oldest first", async () => {
    stubApi("operator");
    renderPage();

    expect(await screen.findByText("Database pool exhaustion")).toBeInTheDocument();
    expect(screen.getByText("Root cause: Connection leak")).toBeInTheDocument();

    const entries = await screen.findAllByRole("listitem");
    expect(entries.map((entry) => entry.textContent)).toEqual([
      expect.stringContaining("Created by AI triage"),
      expect.stringContaining("Status changed from triage to investigating"),
    ]);
    // The diff is shown as well as the sentence, so an entry can be scanned without reading it.
    expect(entries[1].textContent).toContain("triage");
    expect(entries[1].textContent).toContain("investigating");
  });

  it("patches the status the operator picks", async () => {
    const { calls } = stubApi("operator");
    renderPage();

    const select = await screen.findByLabelText("Status");
    fireEvent.change(select, { target: { value: "investigating" } });

    await waitFor(() => {
      const patch = calls.find((call) => call.method === "PATCH");
      expect(patch).toBeDefined();
      expect(patch?.url).toContain("/incidents/7");
      expect(patch?.body).toBe(JSON.stringify({ status: "investigating" }));
    });
  });

  it("sends a comment and clears the box", async () => {
    const { calls } = stubApi("operator");
    renderPage();

    const box = await screen.findByLabelText("Comment");
    fireEvent.change(box, { target: { value: "Over to you." } });
    fireEvent.click(screen.getByRole("button", { name: /add comment/i }));

    await waitFor(() => {
      const post = calls.find((call) => call.url.includes("/comments"));
      expect(post?.method).toBe("POST");
      expect(post?.body).toBe(JSON.stringify({ body: "Over to you." }));
    });
    await waitFor(() => expect(box).toHaveValue(""));
  });

  it("assigns an incident to a teammate", async () => {
    const { calls } = stubApi("operator");
    renderPage();

    fireEvent.change(await screen.findByLabelText("Assignee"), {
      target: { value: "night-ops@cloudops.local" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^assign$/i }));

    await waitFor(() => {
      const patch = calls.find(
        (call) => call.method === "PATCH" && call.body?.includes("night-ops@cloudops.local")
      );
      expect(patch?.body).toBe(JSON.stringify({ assignee: "night-ops@cloudops.local" }));
    });
  });
});

describe("a viewer", () => {
  it("can read the timeline but every control is disabled with the reason", async () => {
    const { calls } = stubApi("viewer");
    renderPage();

    expect(await screen.findByText(/Created by AI triage/)).toBeInTheDocument();
    expect(screen.getByText("Requires the operator role or higher.")).toBeInTheDocument();

    const status = screen.getByLabelText("Status");
    const comment = screen.getByLabelText("Comment");
    const assignButton = screen.getByRole("button", { name: /^assign$/i });
    const addComment = screen.getByRole("button", { name: /add comment/i });

    for (const control of [status, comment, assignButton, addComment]) {
      expect(control).toBeDisabled();
      expect(control).toHaveAttribute("title", "Requires the operator role or higher.");
    }

    // And nothing is sent even if a disabled control is forced to fire.
    fireEvent.change(comment, { target: { value: "hello" } });
    fireEvent.submit(assignButton.closest("form") as HTMLFormElement);

    expect(calls.filter((call) => call.method !== "GET")).toHaveLength(0);
  });
});

describe("an incident that does not exist", () => {
  it("shows the API's explanation instead of an empty page", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input).includes("/auth/me")) {
          return jsonResponse({ id: 2, email: "ops@cloudops.local", name: "Ops", role: "operator" });
        }
        return jsonResponse({ detail: "Incident not found" }, 404);
      })
    );

    renderPage();

    expect(await screen.findByText("Incident not found")).toBeInTheDocument();
  });
});
