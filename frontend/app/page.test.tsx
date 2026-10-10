import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import OverviewPage from "./page";
import { jsonResponse, pageOf, renderPage, stubApi, VIEWER_REASON, writes } from "@/test/support";

/**
 * The Overview page reads four endpoints at once (health, the alert queue, the runbook index
 * and the caller's role), so these tests are as much about it not falling over when one of
 * them is thin as about the numbers it prints.
 */

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={typeof href === "string" ? href : "#"}>{children}</a>
  ),
}));

const OVERVIEW = {
  status: "Degraded",
  active_alerts: 2,
  log_entries: 5,
  model: { provider: "gemini", name: "gemini-3.8-flash" },
  runbooks: [{ doc_id: "rb-1", title: "postgres-pool", source: "manual" }],
  action_items: {
    open: 2,
    overdue: 1,
    items: [
      {
        id: 11,
        text: "Right-size the pool",
        owner: "ops@cloudops.local",
        due_date: "2026-10-06",
        overdue: true,
        post_mortem_id: 5,
        incident_id: 1,
        incident_title: "Database pool exhaustion",
      },
      {
        id: 12,
        text: "Add a pool-waiter alert",
        owner: null,
        due_date: null,
        overdue: false,
        post_mortem_id: 5,
        incident_id: 1,
        incident_title: "Database pool exhaustion",
      },
    ],
  },
};

const ALERTS = [
  {
    id: 1,
    title: "Database pool exhaustion",
    severity: "critical",
    detail: "Pool at 100% for 4 minutes",
    source: "kubernetes",
    status: "open",
  },
  {
    id: 2,
    title: "S3 403 burst",
    severity: "high",
    detail: "403s rising on the asset bucket",
    source: "aws",
    status: "acknowledged",
  },
];

function routes(url: string, method: string) {
  if (url.includes("/overview")) return jsonResponse(OVERVIEW);
  if (url.includes("/alerts/scenarios")) {
    return jsonResponse({ scenarios: ["S3 403 burst", "Postgres pool exhaustion"] });
  }
  if (url.includes("/alerts/simulate") && method === "POST") return jsonResponse({}, 201);
  // The strip's "Mark done" closes one follow-up through the document it belongs to.
  if (url.includes("/post-mortems/") && url.includes("/actions/") && method === "PATCH") {
    return jsonResponse({ status: "done" });
  }
  if (url.includes("/alerts")) return jsonResponse(pageOf(ALERTS, 2, 5));
  return undefined;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

it("renders the health tiles, the alert queue and the runbook index", async () => {
  stubApi("operator", routes);
  renderPage(<OverviewPage />);

  expect(await screen.findByText("DEGRADED")).toBeInTheDocument();
  expect(screen.getByText("2 Pending")).toBeInTheDocument();
  expect(screen.getByText("5 Logged")).toBeInTheDocument();
  expect(screen.getByText("gemini-3.8-flash")).toBeInTheDocument();
  expect(screen.getByText("gemini (gemini-3.8-flash)")).toBeInTheDocument();

  // Scoped to the queue card: the follow-up strip names the same incident, which is the point
  // of the strip but would make an unscoped query ambiguous.
  const queue = screen
    .getByRole("heading", { name: /Active Incidents & AI Recommendations/i })
    .closest(".card") as HTMLElement;
  expect(within(queue).getByText("Database pool exhaustion")).toBeInTheDocument();
  expect(screen.getByText("kubernetes · critical · open")).toBeInTheDocument();
  expect(screen.getByText("postgres-pool")).toBeInTheDocument();
  expect(screen.getByText("Ready")).toBeInTheDocument();
});

it("says the stack is optimal when nothing is active", async () => {
  stubApi("operator", (url, method) => {
    if (url.includes("/overview")) {
      return jsonResponse({ ...OVERVIEW, status: "Healthy", active_alerts: 0, runbooks: [] });
    }
    return routes(url, method);
  });
  renderPage(<OverviewPage />);

  expect(await screen.findByText("ALL SYSTEMS OPTIMAL")).toBeInTheDocument();
  expect(screen.getByText("0 Pending")).toBeInTheDocument();
  expect(screen.getByText("No runbooks indexed. Add one in Knowledge.")).toBeInTheDocument();
});

it("simulates the scenario an operator picks", async () => {
  const calls = stubApi("operator", routes);
  renderPage(<OverviewPage />);

  const select = await screen.findByLabelText("Simulate incident");
  await waitFor(() => expect(select).toBeEnabled());
  fireEvent.change(select, { target: { value: "S3 403 burst" } });

  await waitFor(() => {
    const posted = calls.find((call) => call.url.includes("/alerts/simulate"));
    expect(posted?.method).toBe("POST");
    expect(posted?.body).toBe(JSON.stringify({ scenario: "S3 403 burst" }));
  });
  // The queue and the tiles are reloaded, so the new alert is on screen without a refresh.
  await waitFor(() =>
    expect(calls.filter((call) => call.url.includes("/alerts?limit="))).toHaveLength(2)
  );
});

it("offers a viewer no way to simulate anything", async () => {
  const calls = stubApi("viewer", routes);
  renderPage(<OverviewPage />);

  const select = await screen.findByLabelText("Simulate incident");
  await waitFor(() => expect(select).toBeDisabled());
  expect(select).toHaveAttribute("title", VIEWER_REASON);

  fireEvent.change(select, { target: { value: "S3 403 burst" } });

  expect(writes(calls)).toHaveLength(0);
});

it("shows the outstanding follow-ups with the overdue one first", async () => {
  stubApi("operator", routes);
  renderPage(<OverviewPage />);

  expect(await screen.findByText("Right-size the pool")).toBeInTheDocument();
  expect(screen.getByText("Add a pool-waiter alert")).toBeInTheDocument();
  expect(screen.getByText("2 open")).toBeInTheDocument();
  expect(screen.getByText("1 overdue")).toBeInTheDocument();
  expect(screen.getByText("Overdue")).toBeInTheDocument();
  expect(screen.getByText(/unassigned · due no date/)).toBeInTheDocument();

  const links = screen.getAllByRole("link", { name: "Database pool exhaustion" });
  expect(links[0]).toHaveAttribute("href", "/incidents/1");
});

it("closes a follow-up from the strip without touching the rest of the page", async () => {
  const calls = stubApi("operator", routes);
  renderPage(<OverviewPage />);

  const close = (await screen.findAllByRole("button", { name: /mark done/i }))[0];
  fireEvent.click(close);

  await waitFor(() => {
    const patched = calls.find((call) => call.url.includes("/post-mortems/5/actions/11"));
    expect(patched?.method).toBe("PATCH");
    expect(patched?.body).toBe(JSON.stringify({ status: "done" }));
  });
  // The strip is reloaded, so the count cannot lag behind the click.
  await waitFor(() => expect(calls.filter((call) => call.url.includes("/overview"))).toHaveLength(2));
});

it("offers a viewer no way to close somebody else's follow-up", async () => {
  const calls = stubApi("viewer", routes);
  renderPage(<OverviewPage />);

  const close = (await screen.findAllByRole("button", { name: /mark done/i }))[0];
  expect(close).toBeDisabled();
  expect(close).toHaveAttribute("title", VIEWER_REASON);

  fireEvent.click(close);

  expect(writes(calls)).toHaveLength(0);
});

it("shows the API's failure instead of a screen of zeros", async () => {
  stubApi("operator", (url, method) => {
    if (url.includes("/overview")) {
      return jsonResponse({ detail: "Internal server error" }, 500);
    }
    return routes(url, method);
  });
  renderPage(<OverviewPage />);

  expect(
    await screen.findByText("Backend not reachable: Internal server error")
  ).toBeInTheDocument();
});
