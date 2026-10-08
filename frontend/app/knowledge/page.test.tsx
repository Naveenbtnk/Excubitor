import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import KnowledgePage from "./page";
import { jsonResponse, pageOf, renderPage, stubApi, VIEWER_REASON, writes } from "@/test/support";

/**
 * Two different permissions live on this page, which is the reason it is worth testing:
 * indexing a runbook is an Operator action because it spends embedding budget and changes the
 * shared corpus, while asking a question is reading it and stays open to a Viewer.
 */

const RUNBOOKS = [
  { id: 1, title: "postgres-pool", source: "manual", content: "Drain idle sessions." },
  { id: 2, title: "s3-403s", source: "manual", content: "Check the bucket policy." },
];

const ANSWER = {
  answer: "Pool exhaustion is likely. Drain idle sessions, then restart orders-api.",
  hits: [{ title: "postgres-pool", source: "manual", content: "Drain idle sessions." }],
};

function routes(url: string, method: string) {
  if (url.includes("/knowledge/query") && method === "POST") return jsonResponse(ANSWER);
  if (url.includes("/knowledge/runbooks") && method === "POST") {
    return jsonResponse({ id: 3, title: "new", source: "manual", content: "x" }, 201);
  }
  if (url.includes("/knowledge/runbooks")) return jsonResponse(pageOf(RUNBOOKS));
  return undefined;
}

async function indexRunbook(title = "orders-api failover", content = "Promote the replica.") {
  fireEvent.change(screen.getByPlaceholderText(/Runbook title/i), { target: { value: title } });
  fireEvent.change(screen.getByPlaceholderText(/Paste runbook/i), { target: { value: content } });
  fireEvent.click(screen.getByRole("button", { name: /index runbook/i }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("as an operator", () => {
  it("lists the indexed runbooks with the total", async () => {
    stubApi("operator", routes);
    renderPage(<KnowledgePage />);

    expect(await screen.findByText("Indexed (2)")).toBeInTheDocument();
    expect(screen.getByText("postgres-pool")).toBeInTheDocument();
    expect(screen.getByText("s3-403s")).toBeInTheDocument();
    expect(screen.getAllByText("manual · Ready")).toHaveLength(2);
  });

  it("indexes a runbook, then clears the form and reloads the index", async () => {
    const calls = stubApi("operator", routes);
    renderPage(<KnowledgePage />);

    await screen.findByText("Indexed (2)");
    await indexRunbook();

    await waitFor(() => {
      const posted = calls.find(
        (call) => call.method === "POST" && call.url.includes("/knowledge/runbooks")
      );
      expect(posted?.body).toBe(
        JSON.stringify({
          title: "orders-api failover",
          content: "Promote the replica.",
          source: "manual",
        })
      );
    });

    await waitFor(() =>
      expect(screen.getByPlaceholderText(/Runbook title/i)).toHaveValue("")
    );
    expect(screen.getByPlaceholderText(/Paste runbook/i)).toHaveValue("");
    await waitFor(() =>
      expect(calls.filter((call) => call.url.includes("/knowledge/runbooks?limit="))).toHaveLength(2)
    );
  });

  it("will not send a runbook with an empty title or body", async () => {
    const calls = stubApi("operator", routes);
    renderPage(<KnowledgePage />);

    await screen.findByText("Indexed (2)");
    fireEvent.change(screen.getByPlaceholderText(/Runbook title/i), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: /index runbook/i }));

    expect(writes(calls)).toHaveLength(0);
  });

  it("answers a question with the retrieved context", async () => {
    const calls = stubApi("operator", routes);
    renderPage(<KnowledgePage />);

    fireEvent.change(await screen.findByPlaceholderText(/How do I fix/i), {
      target: { value: "How do I fix a Postgres pool exhaustion?" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^ask$/i }));

    await waitFor(() => {
      const posted = calls.find((call) => call.url.includes("/knowledge/query"));
      expect(posted?.body).toBe(
        JSON.stringify({ question: "How do I fix a Postgres pool exhaustion?" })
      );
    });

    expect(await screen.findByText(/Pool exhaustion is likely/)).toBeInTheDocument();
    expect(screen.getByText("Retrieved context")).toBeInTheDocument();
    expect(screen.getByText("postgres-pool · manual")).toBeInTheDocument();
    expect(screen.getByText("Drain idle sessions.")).toBeInTheDocument();
  });

  it("reports a failed query rather than showing nothing", async () => {
    stubApi("operator", (url, method) => {
      if (url.includes("/knowledge/query")) {
        return jsonResponse({ detail: "The provider is temporarily unavailable." }, 503);
      }
      return routes(url, method);
    });
    renderPage(<KnowledgePage />);

    fireEvent.change(await screen.findByPlaceholderText(/How do I fix/i), {
      target: { value: "Anything" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^ask$/i }));

    expect(
      await screen.findByText("The provider is temporarily unavailable.")
    ).toBeInTheDocument();
  });
});

describe("a viewer", () => {
  it("may read the corpus and ask questions, but not add to it", async () => {
    const calls = stubApi("viewer", routes);
    renderPage(<KnowledgePage />);

    await screen.findByText("Indexed (2)");

    const index = screen.getByRole("button", { name: /index runbook/i });
    const title = screen.getByPlaceholderText(/Runbook title/i);
    const content = screen.getByPlaceholderText(/Paste runbook/i);
    const ask = screen.getByRole("button", { name: /^ask$/i });

    for (const control of [index, title, content]) {
      expect(control).toBeDisabled();
      expect(control).toHaveAttribute("title", VIEWER_REASON);
    }
    expect(screen.getByText(VIEWER_REASON)).toBeInTheDocument();
    // Reading the corpus is not a write, so the question box is still usable.
    expect(ask).toBeEnabled();

    fireEvent.click(index);
    expect(writes(calls)).toHaveLength(0);

    fireEvent.change(screen.getByPlaceholderText(/How do I fix/i), {
      target: { value: "Read-only question" },
    });
    fireEvent.click(ask);

    await waitFor(() => {
      const posted = calls.find((call) => call.url.includes("/knowledge/query"));
      expect(posted?.method).toBe("POST");
    });
  });
});
