import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import PostMortemsPage from "./page";
import { jsonResponse, pageOf, renderPage, stubApi, VIEWER_REASON, writes } from "@/test/support";

/**
 * The post-mortem workspace: a list of documents, the sections of the one you open, and the three
 * things a reviewer does with it -- edit it, move it through review, download it.
 *
 * Two claims are worth more than the rest. The export has to point at the markdown endpoint
 * rather than at a rendered page, because the file is the artifact teams share. And a Viewer has
 * to be unable to write: the disabled controls are asserted together with the requests that were
 * never sent, since a control that looks disabled and still writes is the failure this page
 * cannot have.
 */

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={typeof href === "string" ? href : "#"}>{children}</a>
  ),
}));

// Two incidents already have a document, so the picker offers only the third: writing the same
// incident up twice is what the unique post_mortems.incident_id refuses.
const INCIDENTS = [
  { id: 1, title: "Database pool exhaustion", summary: "Pool exhausted", status: "resolved" },
  { id: 4, title: "S3 403 burst", summary: "403s rising", status: "resolved" },
  { id: 7, title: "Checkout latency spike", summary: "p99 climbing", status: "resolved" },
];

const DRAFT = {
  id: 5,
  incident_id: 1,
  incident_title: "Database pool exhaustion",
  content: "## Root cause\n\nConnection leak in orders-api.",
  timeline_summary: "09:12 alert, 09:13 triage, 09:31 mitigated.",
  root_cause: "Connection leak",
  contributing_factors: ["Pool size unchanged since March"],
  impact: "Four minutes of 500s on checkout.",
  status: "draft",
  author: "admin@cloudops.local",
  published_at: null as string | null,
  created_at: "2026-10-09T09:00:00Z",
  updated_at: "2026-10-09T09:05:00Z",
  action_items: [
    {
      id: 11,
      post_mortem_id: 5,
      text: "Add a pool-waiter alert",
      owner: "ops@cloudops.local",
      owner_id: 3,
      due_date: "2026-10-01",
      status: "open",
    },
  ],
};

const PUBLISHED = {
  ...DRAFT,
  id: 6,
  incident_id: 4,
  incident_title: "S3 403 burst",
  status: "published",
  published_at: "2026-10-09T10:00:00Z",
  action_items: [],
};

function routes(overrides: Record<string, () => Response> = {}) {
  return (url: string, method: string) => {
    for (const [needle, handler] of Object.entries(overrides)) {
      if (url.includes(needle)) return handler();
    }
    // Specific paths first: "/post-mortems/5" also contains "/post-mortems".
    if (method === "PATCH" && /\/post-mortems\/\d+\/actions\/\d+$/.test(url)) {
      return jsonResponse({ ...DRAFT.action_items[0], status: "done" });
    }
    if (method === "PATCH" && /\/post-mortems\/\d+$/.test(url)) {
      return jsonResponse(DRAFT);
    }
    if (/\/incidents\/\d+\/post-mortem$/.test(url)) {
      return method === "POST" ? jsonResponse(DRAFT, 201) : undefined;
    }
    if (url.includes("/post-mortems")) return jsonResponse(pageOf([DRAFT, PUBLISHED], 2));
    if (url.includes("/incidents")) return jsonResponse(pageOf(INCIDENTS));
    return undefined;
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Open the workspace on the draft document. */
async function openDraft(role = "operator") {
  const calls = stubApi(role, routes());
  renderPage(<PostMortemsPage />);
  fireEvent.click(await screen.findByText("Database pool exhaustion"));
  await screen.findByLabelText("Narrative");
  return calls;
}

it("lists the documents with their review state and open follow-ups", async () => {
  stubApi("operator", routes());
  renderPage(<PostMortemsPage />);

  expect(await screen.findByText("Draft")).toBeInTheDocument();
  expect(screen.getByText("Published")).toBeInTheDocument();
  expect(screen.getByText(/1 open/)).toBeInTheDocument();
});

it("generates a draft from the incident that was chosen", async () => {
  const calls = stubApi("operator", routes());
  renderPage(<PostMortemsPage />);

  const picker = await screen.findByLabelText("Write up an incident");
  expect(screen.queryByRole("option", { name: /Database pool exhaustion/ })).not.toBeInTheDocument();

  fireEvent.change(picker, { target: { value: "7" } });
  fireEvent.click(screen.getByRole("button", { name: /generate draft/i }));

  await waitFor(() => {
    const posted = calls.find((call) => call.url.endsWith("/incidents/7/post-mortem"));
    expect(posted?.method).toBe("POST");
  });
  // The generated document opens for editing straight away.
  expect(await screen.findByLabelText("Root cause")).toHaveValue("Connection leak");
});

it("shows the structured sections and points the export at the markdown file", async () => {
  await openDraft();

  for (const label of [
    "Narrative",
    "Timeline summary",
    "Root cause",
    "Contributing factors",
    "Impact",
  ]) {
    expect(screen.getByLabelText(label)).toBeInTheDocument();
  }
  expect(screen.getByLabelText("Timeline summary")).toHaveValue(
    "09:12 alert, 09:13 triage, 09:31 mitigated."
  );
  expect(screen.getByLabelText("Contributing factors")).toHaveValue(
    "Pool size unchanged since March"
  );

  const exportLink = screen.getByRole("link", { name: /download \.md/i });
  expect(exportLink).toHaveAttribute("href", "/api/post-mortems/5/export.md");
  expect(exportLink).toHaveAttribute("download", "post-mortem-1.md");
});

it("saves every section and the follow-ups in one request", async () => {
  const calls = await openDraft();

  fireEvent.change(screen.getByLabelText("Root cause"), {
    target: { value: "The pool was sized for the old replica count." },
  });
  fireEvent.change(screen.getByLabelText("Contributing factors"), {
    target: { value: "Pool size unchanged since March\nNo alert on pool waiters" },
  });
  fireEvent.change(screen.getByLabelText("Action 1"), {
    target: { value: "Add a pool-waiter alert" },
  });
  fireEvent.change(screen.getByLabelText("Due date of action 1"), {
    target: { value: "2026-11-01" },
  });
  fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

  await waitFor(() => expect(screen.getByText("Saved.")).toBeInTheDocument());

  const saved = calls.find(
    (call) => call.method === "PATCH" && call.url.endsWith("/post-mortems/5")
  );
  const body = JSON.parse(saved?.body ?? "{}");
  expect(body.root_cause).toBe("The pool was sized for the old replica count.");
  expect(body.contributing_factors).toEqual([
    "Pool size unchanged since March",
    "No alert on pool waiters",
  ]);
  // The existing item keeps its id, so an edit updates the row instead of replacing it.
  expect(body.action_items).toEqual([
    {
      id: 11,
      text: "Add a pool-waiter alert",
      owner: "ops@cloudops.local",
      due_date: "2026-11-01",
      status: "open",
    },
  ]);
});

it("moves the document through review with a one-field request", async () => {
  const calls = await openDraft();

  fireEvent.click(screen.getByRole("button", { name: "Send for review" }));

  await waitFor(() => {
    const sent = calls.find((call) => call.method === "PATCH");
    expect(JSON.parse(sent?.body ?? "{}")).toEqual({ status: "in_review" });
  });

  fireEvent.click(screen.getByRole("button", { name: "Publish" }));

  await waitFor(() => {
    const published = calls.filter((call) => call.method === "PATCH").at(-1);
    expect(JSON.parse(published?.body ?? "{}")).toEqual({ status: "published" });
  });
});

it("closes one follow-up without saving the whole document", async () => {
  const calls = await openDraft();

  fireEvent.click(screen.getByRole("button", { name: "Mark done" }));

  await waitFor(() => {
    const closed = calls.find((call) => call.url.endsWith("/post-mortems/5/actions/11"));
    expect(closed?.method).toBe("PATCH");
    expect(closed?.body).toBe(JSON.stringify({ status: "done" }));
  });
  // Nothing was written to the document itself.
  expect(
    calls.filter((call) => call.method === "PATCH" && call.url.endsWith("/post-mortems/5"))
  ).toHaveLength(0);
});

it("reports a rejected save instead of pretending it worked", async () => {
  stubApi(
    "operator",
    routes({ "/post-mortems/5": () => jsonResponse({ detail: "Incident not found" }, 404) })
  );
  renderPage(<PostMortemsPage />);
  fireEvent.click(await screen.findByText("Database pool exhaustion"));
  await screen.findByLabelText("Narrative");

  fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Incident not found");
  expect(screen.queryByText("Saved.")).not.toBeInTheDocument();
});

describe("a viewer", () => {
  it("reads a document but is offered no way to write one", async () => {
    const calls = stubApi("viewer", routes());
    renderPage(<PostMortemsPage />);
    fireEvent.click(await screen.findByText("Database pool exhaustion"));
    await screen.findByLabelText("Narrative");

    expect(screen.queryByRole("button", { name: /save changes/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Publish" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /generate draft/i })).toBeDisabled();
    expect(screen.getByLabelText("Write up an incident")).toBeDisabled();
    expect(screen.getByLabelText("Write up an incident")).toHaveAttribute("title", VIEWER_REASON);
    // The section fields are readable, which is the point of publishing them.
    expect(screen.getByLabelText("Root cause")).toHaveAttribute("readonly");
    // The download is a read, so it is still offered.
    expect(screen.getByRole("link", { name: /download \.md/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Mark done" }));

    expect(writes(calls)).toHaveLength(0);
  });
});
