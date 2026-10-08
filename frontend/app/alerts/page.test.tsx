import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import AlertsPage from "./page";
import { jsonResponse, pageOf, renderPage, stubApi, VIEWER_REASON, writes } from "@/test/support";

/**
 * The Alerts page is where an alert becomes an incident, so the two writes that matter are the
 * acknowledgement and the triage call. Both are Operator actions: a viewer must see them
 * disabled, and nothing may leave the page when they are.
 */

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={typeof href === "string" ? href : "#"}>{children}</a>
  ),
}));

const ALERTS = [
  {
    id: 1,
    source: "kubernetes",
    severity: "critical",
    title: "Database pool exhaustion",
    detail: "Pool at 100% for 4 minutes on orders-api.",
    status: "open",
    created_at: "2026-10-08T09:12:44Z",
  },
  {
    id: 2,
    source: "aws",
    severity: "high",
    title: "S3 403 burst",
    detail: "403s rising on the asset bucket.",
    status: "acknowledged",
    created_at: "2026-10-08T09:00:00Z",
  },
];

const INCIDENT = {
  id: 9,
  alert_id: 1,
  title: "Database pool exhaustion",
  summary: "Pool exhausted on orders-api",
  root_cause: "Connection leak",
  recommendation: "1. Drain idle sessions\n2. Restart orders-api",
  confidence: 0.75,
  status: "triage",
};

function routes(url: string, method: string) {
  // Before the `/alerts` prefix, which would also match this path.
  if (url.includes("/alerts/scenarios")) return jsonResponse({ scenarios: ["S3 403 burst"] });
  if (url.includes("/alerts/simulate") && method === "POST") return jsonResponse({}, 201);
  if (url.includes("/incidents/triage/")) return jsonResponse(INCIDENT, 201);
  if (url.includes("/ack")) return jsonResponse({ ...ALERTS[0], status: "acknowledged" });
  if (url.includes("/alerts")) return jsonResponse(pageOf(ALERTS));
  return undefined;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

it("lists the queue with severity and status", async () => {
  stubApi("operator", routes);
  renderPage(<AlertsPage />);

  expect(await screen.findByText("Database pool exhaustion")).toBeInTheDocument();
  // The severity is the badge; the line under the title is source and status.
  expect(screen.getByText("critical")).toBeInTheDocument();
  expect(screen.getByText("kubernetes · open")).toBeInTheDocument();
  expect(screen.getByText("aws · acknowledged")).toBeInTheDocument();
  expect(screen.getByText("Showing 2 of 2")).toBeInTheDocument();
});

it("acknowledges an alert and reloads the queue", async () => {
  const calls = stubApi("operator", routes);
  renderPage(<AlertsPage />);

  const ack = await screen.findAllByRole("button", { name: /ack/i });
  fireEvent.click(ack[0]);

  await waitFor(() => {
    const posted = calls.find((call) => call.url.includes("/ack"));
    expect(posted?.method).toBe("POST");
    expect(posted?.url).toContain("/alerts/1/ack");
  });
  await waitFor(() => expect(calls.filter((call) => call.url.includes("/alerts?limit="))).toHaveLength(2));
});

it("triages an alert and shows the recommendation with a link to its timeline", async () => {
  const calls = stubApi("operator", routes);
  renderPage(<AlertsPage />);

  const triage = await screen.findAllByRole("button", { name: /ai triage/i });
  fireEvent.click(triage[0]);

  await waitFor(() => {
    const posted = calls.find((call) => call.url.includes("/incidents/triage/"));
    expect(posted?.method).toBe("POST");
    expect(posted?.url).toContain("/incidents/triage/1");
  });

  expect(
    await screen.findByText("AI Recommendation · Database pool exhaustion")
  ).toBeInTheDocument();
  expect(screen.getByText("Confidence 75%")).toBeInTheDocument();
  expect(screen.getByText("Root cause: Connection leak")).toBeInTheDocument();
  expect(screen.getByText(/Drain idle sessions/)).toBeInTheDocument();

  const timeline = screen.getByRole("link", { name: /open timeline/i });
  expect(timeline).toHaveAttribute("href", "/incidents/9");
});

it("reports a triage failure instead of leaving the card blank", async () => {
  stubApi("operator", (url, method) => {
    if (url.includes("/incidents/triage/")) {
      return jsonResponse({ detail: "Gemini is temporarily unavailable." }, 503);
    }
    return routes(url, method);
  });
  renderPage(<AlertsPage />);

  const triage = await screen.findAllByRole("button", { name: /ai triage/i });
  fireEvent.click(triage[0]);

  expect(await screen.findByText("Gemini is temporarily unavailable.")).toBeInTheDocument();
});

describe("a viewer", () => {
  it("sees every write disabled, with the reason", async () => {
    const calls = stubApi("viewer", routes);
    renderPage(<AlertsPage />);

    const simulate = await screen.findByLabelText("Simulate alert");
    const triage = (await screen.findAllByRole("button", { name: /ai triage/i }))[0];
    const ack = screen.getAllByRole("button", { name: /ack/i })[0];

    for (const control of [simulate, triage, ack]) {
      await waitFor(() => expect(control).toBeDisabled());
      expect(control).toHaveAttribute("title", VIEWER_REASON);
    }
  });

  it("cannot start a triage by forcing the disabled control", async () => {
    const calls = stubApi("viewer", routes);
    renderPage(<AlertsPage />);

    const triage = (await screen.findAllByRole("button", { name: /ai triage/i }))[0];
    const ack = screen.getAllByRole("button", { name: /ack/i })[0];
    fireEvent.click(triage);
    fireEvent.click(ack);
    fireEvent.change(await screen.findByLabelText("Simulate alert"), {
      target: { value: "S3 403 burst" },
    });

    expect(writes(calls)).toHaveLength(0);
  });
});
