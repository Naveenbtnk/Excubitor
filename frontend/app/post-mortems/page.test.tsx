import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import PostMortemsPage from "./page";
import { jsonResponse, pageOf, renderPage, stubApi, VIEWER_REASON, writes } from "@/test/support";

/**
 * The page lists incidents and generates a post-mortem from one. Generating is an Operator
 * action that also resolves the incident, so the disabled state is asserted together with the
 * requests: a viewer reading the list must not be able to write one.
 */

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={typeof href === "string" ? href : "#"}>{children}</a>
  ),
}));

const INCIDENTS = [
  {
    id: 1,
    title: "Database pool exhaustion",
    summary: "Pool exhausted on orders-api",
    status: "triage",
  },
  { id: 2, title: "S3 403 burst", summary: "403s rising on the asset bucket", status: "resolved" },
];

const POST_MORTEM = {
  id: 5,
  incident_id: 1,
  content: "## Root cause\n\nConnection leak in orders-api.",
  created_at: "2026-10-08T10:00:00Z",
};

function routes(url: string, method: string) {
  if (url.includes("/post-mortem") && method === "POST") return jsonResponse(POST_MORTEM, 201);
  if (url.includes("/incidents")) return jsonResponse(pageOf(INCIDENTS));
  return undefined;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

it("lists the incidents and links each to its timeline", async () => {
  stubApi("operator", routes);
  renderPage(<PostMortemsPage />);

  expect(await screen.findByText("Database pool exhaustion")).toBeInTheDocument();
  expect(screen.getByText("S3 403 burst")).toBeInTheDocument();
  expect(screen.getByText("triage")).toBeInTheDocument();
  expect(screen.getByText("resolved")).toBeInTheDocument();

  const links = screen.getAllByRole("link", { name: /timeline/i });
  expect(links.map((link) => link.getAttribute("href"))).toEqual([
    "/incidents/1",
    "/incidents/2",
  ]);
});

it("generates a post-mortem and renders the markdown", async () => {
  const calls = stubApi("operator", routes);
  renderPage(<PostMortemsPage />);

  fireEvent.click(await screen.findByText("Database pool exhaustion"));

  await waitFor(() => {
    const posted = calls.find((call) => call.url.includes("/post-mortem"));
    expect(posted?.method).toBe("POST");
    expect(posted?.url).toContain("/incidents/1/post-mortem");
  });

  expect(await screen.findByText("Generated post-mortem")).toBeInTheDocument();
  expect(screen.getByText("Root cause")).toBeInTheDocument();
  expect(screen.getByText("Connection leak in orders-api.")).toBeInTheDocument();
});

it("reports a failed generation instead of an empty pane", async () => {
  stubApi("operator", (url, method) => {
    if (url.includes("/post-mortem")) {
      return jsonResponse({ detail: "Gemini is temporarily unavailable." }, 503);
    }
    return routes(url, method);
  });
  renderPage(<PostMortemsPage />);

  fireEvent.click(await screen.findByText("Database pool exhaustion"));

  expect(await screen.findByText("Gemini is temporarily unavailable.")).toBeInTheDocument();
});

describe("a viewer", () => {
  it("can read the list and open timelines, but cannot generate", async () => {
    const calls = stubApi("viewer", routes);
    renderPage(<PostMortemsPage />);

    const incident = await screen.findByText("Database pool exhaustion");
    const button = incident.closest("button") as HTMLButtonElement;

    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", VIEWER_REASON);
    expect(screen.getAllByRole("link", { name: /timeline/i })).toHaveLength(2);

    fireEvent.click(button);

    expect(writes(calls)).toHaveLength(0);
    expect(screen.queryByText("Generated post-mortem")).not.toBeInTheDocument();
  });
});
