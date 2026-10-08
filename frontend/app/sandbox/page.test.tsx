import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import SandboxPage from "./page";

/**
 * The Sandbox page driven through a stubbed `fetch`, so the real `streamChat` parser and the
 * real rendering sit in the loop: these tests exercise what a user actually sees for a stream
 * of bytes, not what a mock was told to call.
 */

const encoder = new TextEncoder();

/** Anything from this list appearing in the page means a provider payload is on screen. */
const LEAK_MARKERS = [
  "generativelanguage.googleapis.com",
  "quotaMetric",
  "quotaId",
  "quotaValue",
  "RetryInfo",
  "ai.google.dev",
  "RESOURCE_EXHAUSTED",
  "@type",
  "free_tier",
  "You exceeded your current quota",
];

const QUOTA_MESSAGE =
  "Gemini's daily quota has been reached. Sandbox responses will be available again in " +
  "approximately 15 hours 33 minutes. Switch to the local Ollama provider to keep working in " +
  "the meantime.";

interface StreamControl {
  send: (chunk: string) => void;
  end: () => void;
}

/** A 200 SSE response whose body this test controls chunk by chunk. */
function stubStream(): StreamControl {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });

  vi.stubGlobal(
    "fetch",
    vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      // Real `fetch` fails the body read when the caller aborts; mirror that, so the Stop
      // button exercises the page's own abort path rather than a shortcut.
      const signal = init?.signal;
      if (signal) {
        const abort = () =>
          controller.error(new DOMException("The operation was aborted.", "AbortError"));
        if (signal.aborted) abort();
        else signal.addEventListener("abort", abort, { once: true });
      }
      return new Response(body, { status: 200, headers: { "x-request-id": "req-test" } });
    })
  );

  return {
    send: (chunk: string) => controller.enqueue(encoder.encode(chunk)),
    end: () => controller.close(),
  };
}

function stubJsonFailure(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify(body), {
          status,
          headers: { "content-type": "application/json" },
        })
    )
  );
}

/** Push frames into the response body and let React process whatever they triggered. */
async function push(stream: StreamControl, ...chunks: string[]) {
  await act(async () => {
    for (const chunk of chunks) stream.send(chunk);
  });
}

async function finish(stream: StreamControl) {
  await act(async () => {
    stream.send("event: done\ndata: \n\n");
    stream.end();
  });
}

function submit(text = "Postgres pool exhaustion on orders-api") {
  fireEvent.change(screen.getByPlaceholderText("Describe the incident…"), {
    target: { value: text },
  });
  fireEvent.click(screen.getByLabelText("Send message"));
}

async function submitQuotaFrame(frame: Record<string, unknown>, stream: StreamControl) {
  submit();
  await push(stream, `event: error\ndata: ${JSON.stringify(frame)}\n\n`);
  await finish(stream);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("streaming", () => {
  it("renders tokens progressively and drops Thinking once they arrive", async () => {
    const stream = stubStream();
    render(<SandboxPage />);
    submit();

    expect(await screen.findByText("Thinking…")).toBeInTheDocument();

    await push(stream, "event: message\ndata: Pool \n\n");
    expect(await screen.findByText("Pool")).toBeInTheDocument();

    await push(stream, "event: message\ndata: exhaustion is plausible.\n\n");
    expect(await screen.findByText("Pool exhaustion is plausible.")).toBeInTheDocument();
    expect(screen.queryByText("Thinking…")).not.toBeInTheDocument();

    await finish(stream);
    expect(await screen.findByLabelText("Send message")).toBeInTheDocument();
  });

  it("keeps the partial answer when stopped before the first token", async () => {
    stubStream();
    render(<SandboxPage />);
    submit();
    expect(await screen.findByText("Thinking…")).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("Stop generating"));

    expect(await screen.findByText("Stopped before any output.")).toBeInTheDocument();
    expect(
      screen.getByText("Stopped by you. The partial answer above was kept.")
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Send message")).toBeInTheDocument();
  });

  it("keeps the partial answer when stopped after output has arrived", async () => {
    const stream = stubStream();
    render(<SandboxPage />);
    submit();

    await push(stream, "event: message\ndata: Half an answer.\n\n");
    expect(await screen.findByText("Half an answer.")).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("Stop generating"));

    expect(
      await screen.findByText("Stopped by you. The partial answer above was kept.")
    ).toBeInTheDocument();
    expect(screen.getByText("Half an answer.")).toBeInTheDocument();
    expect(screen.queryByText("Stopped before any output.")).not.toBeInTheDocument();
  });
});

describe("failures", () => {
  it("shows the quota sentence with the countdown the server supplied", async () => {
    const stream = stubStream();
    render(<SandboxPage />);
    await submitQuotaFrame(
      {
        code: "quota_exhausted",
        message: QUOTA_MESSAGE,
        provider: "gemini",
        retry_after: 56023,
        request_id: "req-abc123",
      },
      stream
    );

    expect(await screen.findByText(/daily quota has been reached/)).toBeInTheDocument();
    expect(screen.getByText(/15 hours 33 minutes/)).toBeInTheDocument();
    expect(screen.getByText(/switch to the local Ollama provider/i)).toBeInTheDocument();
    expect(document.querySelector('[data-error-code="quota_exhausted"]')).not.toBeNull();
    expect(screen.getByLabelText("Copy diagnostic ID")).toBeInTheDocument();

    for (const marker of LEAK_MARKERS) {
      expect(document.body.textContent).not.toContain(marker);
    }
  });

  it("shows no countdown when the server did not supply a wait", async () => {
    const stream = stubStream();
    render(<SandboxPage />);
    await submitQuotaFrame(
      {
        code: "quota_exhausted",
        message:
          "Gemini's daily quota has been reached. " +
          "Try again later or switch to the local Ollama provider.",
        provider: "gemini",
        request_id: "req-abc123",
      },
      stream
    );

    expect(await screen.findByText(/daily quota has been reached/)).toBeInTheDocument();
    // Nothing invents a countdown locally: no wait from the server means no promise about when.
    expect(document.body.textContent).not.toContain("approximately");
    expect(screen.getByLabelText("Copy diagnostic ID")).toBeInTheDocument();
  });

  it("copies the diagnostic id instead of printing it", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });

    const stream = stubStream();
    render(<SandboxPage />);
    await submitQuotaFrame(
      { code: "quota_exhausted", message: QUOTA_MESSAGE, request_id: "req-abc123" },
      stream
    );

    fireEvent.click(await screen.findByLabelText("Copy diagnostic ID"));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("req-abc123"));
    expect(await screen.findByText("Copied")).toBeInTheDocument();
    // The id is a support handle, not part of the answer.
    expect(document.body.textContent).not.toContain("req-abc123");
  });

  it("replaces a raw provider payload with our own sentence", async () => {
    const stream = stubStream();
    render(<SandboxPage />);
    submit();

    const raw = `429 RESOURCE_EXHAUSTED. ${JSON.stringify({
      error: {
        code: 429,
        message:
          "You exceeded your current quota, please check your plan and billing details. " +
          "For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits.",
        status: "RESOURCE_EXHAUSTED",
        details: [
          {
            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
            violations: [
              {
                quotaMetric:
                  "generativelanguage.googleapis.com/generate_content_free_tier_requests",
                quotaId: "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                quotaValue: "20",
              },
            ],
          },
          { "@type": "type.googleapis.com/google.rpc.RetryInfo", retryDelay: "56023s" },
        ],
      },
    })}`;

    await push(stream, `event: error\ndata: ${raw}\n\n`);
    await finish(stream);

    expect(await screen.findByText(/ended unexpectedly/)).toBeInTheDocument();
    for (const marker of LEAK_MARKERS) {
      expect(document.body.textContent).not.toContain(marker);
    }
    // The request id from the response header is still offered to support.
    expect(screen.getByLabelText("Copy diagnostic ID")).toBeInTheDocument();
  });

  it("shows the API's sentence for a failure that arrives as a status", async () => {
    stubJsonFailure(429, { detail: "Too many requests. Try again in 12 seconds." });
    render(<SandboxPage />);
    submit();

    // The note prefixes the sentence with a warning glyph, so match on the sentence itself.
    expect(await screen.findByText(/Too many requests. Try again in 12 seconds/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Copy diagnostic ID")).not.toBeInTheDocument();
  });
});
