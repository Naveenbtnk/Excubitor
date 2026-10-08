// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, type PublicError } from "@/lib/api";
import { streamChat, UNEXPECTED_ERROR_MESSAGE } from "@/lib/stream";

/**
 * `streamChat` against crafted byte streams: the framing rules of SSE (CRLF, comments,
 * multi-line data), the incremental decode, and the one rule that matters most here -- an
 * `error` frame never becomes user-facing text unless it is the API's structured shape.
 */

const encoder = new TextEncoder();

/** Anything from this list reaching a handler means a provider payload escaped. */
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

const QUOTA_FRAME = {
  code: "quota_exhausted",
  message:
    "Gemini's daily quota has been reached. Sandbox responses will be available again in " +
    "approximately 15 hours 33 minutes. Switch to the local Ollama provider to keep working " +
    "in the meantime.",
  provider: "gemini",
  retry_after: 56023,
  request_id: "abc123",
};

function streamResponse(chunks: Array<string | Uint8Array>, init: ResponseInit = {}): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(typeof chunk === "string" ? encoder.encode(chunk) : chunk);
      }
      controller.close();
    },
  });
  return new Response(body, init);
}

function stubFetch(response: Response) {
  const fetchMock = vi.fn(async () => response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function handlers() {
  return {
    onToken: vi.fn<(token: string) => void>(),
    onError: vi.fn<(error: PublicError) => void>(),
    onDone: vi.fn<() => void>(),
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("frame framing", () => {
  it("delivers tokens in order across CRLF frames and completes on done", async () => {
    stubFetch(
      streamResponse([
        "event: message\r\ndata: Hel\r\n\r\n",
        "event: message\r\ndata: lo\r\n\r\n",
        "event: done\r\ndata: \r\n\r\n",
      ])
    );
    const seen = handlers();

    const result = await streamChat("hi", seen);

    expect(seen.onToken.mock.calls.map(([token]) => token)).toEqual(["Hel", "lo"]);
    expect(seen.onDone).toHaveBeenCalledTimes(1);
    expect(seen.onError).not.toHaveBeenCalled();
    expect(result.completed).toBe(true);
  });

  it("decodes a multi-byte character split across two chunks", async () => {
    // "🧪" is four bytes; cutting after the second splits the code point in half.
    const frame = encoder.encode("event: message\ndata: 🧪 ré\n\n");
    const emoji = frame.indexOf(0xf0);
    expect(emoji).toBeGreaterThan(-1);

    stubFetch(streamResponse([frame.slice(0, emoji + 2), frame.slice(emoji + 2)]));
    const seen = handlers();

    await streamChat("hi", seen);

    expect(seen.onToken).toHaveBeenCalledWith("🧪 ré");
  });

  it("skips comments and joins multi-line data", async () => {
    stubFetch(
      streamResponse([
        ": keep-alive\n\n",
        "event: message\ndata: line one\ndata: line two\n\n",
        "event: done\ndata: \n\n",
      ])
    );
    const seen = handlers();

    await streamChat("hi", seen);

    expect(seen.onToken).toHaveBeenCalledTimes(1);
    expect(seen.onToken).toHaveBeenCalledWith("line one\nline two");
  });

  it("reports a stream that ends without its done frame as incomplete", async () => {
    stubFetch(streamResponse(["event: message\ndata: partial\n\n"]));
    const seen = handlers();

    const result = await streamChat("hi", seen);

    expect(result.completed).toBe(false);
    expect(seen.onDone).not.toHaveBeenCalled();
    expect(seen.onToken).toHaveBeenCalledWith("partial");
  });
});

describe("error frames", () => {
  it("hands the structured error over as data", async () => {
    stubFetch(
      streamResponse(
        [`event: error\ndata: ${JSON.stringify(QUOTA_FRAME)}\n\n`, "event: done\ndata: \n\n"],
        { headers: { "x-request-id": "req-1" } }
      )
    );
    const seen = handlers();

    await streamChat("hi", seen);

    expect(seen.onError).toHaveBeenCalledWith(QUOTA_FRAME);
    expect(seen.onDone).toHaveBeenCalledTimes(1);
  });

  it("keeps an unfamiliar code but the server's own sentence", async () => {
    const frame = { code: "something_new", message: "Gemini could not complete this request." };
    stubFetch(streamResponse([`event: error\ndata: ${JSON.stringify(frame)}\n\n`]));
    const seen = handlers();

    await streamChat("hi", seen);

    expect(seen.onError).toHaveBeenCalledWith(frame);
  });

  it("replaces a raw provider payload with our own sentence", async () => {
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
                quotaMetric: "generativelanguage.googleapis.com/generate_content_free_tier_requests",
                quotaId: "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                quotaValue: "20",
              },
            ],
          },
          { "@type": "type.googleapis.com/google.rpc.RetryInfo", retryDelay: "56023s" },
        ],
      },
    })}`;

    stubFetch(
      streamResponse([`event: error\ndata: ${raw}\n\n`, "event: done\ndata: \n\n"], {
        headers: { "x-request-id": "req-1" },
      })
    );
    const seen = handlers();

    const result = await streamChat("hi", seen);

    const [error] = seen.onError.mock.calls[0];
    expect(error.message).toBe(UNEXPECTED_ERROR_MESSAGE);
    expect(error.code).toBe("provider_error");
    // The response's own request id still gives support something to look up.
    expect(error.request_id).toBe("req-1");
    expect(result.completed).toBe(true);

    const everythingHandedToThePage = JSON.stringify([
      seen.onError.mock.calls,
      seen.onToken.mock.calls,
    ]);
    for (const marker of LEAK_MARKERS) {
      expect(everythingHandedToThePage).not.toContain(marker);
    }
  });

  it("treats a well-formed provider payload as unrecognised, not as data", async () => {
    // Valid JSON, but not our vocabulary: the guard is the field set, not the parse.
    const payload = JSON.stringify({
      error: { code: 429, message: "https://ai.google.dev/gemini-api/docs/rate-limits" },
    });
    stubFetch(streamResponse([`event: error\ndata: ${payload}\n\n`]));
    const seen = handlers();

    await streamChat("hi", seen);

    const [error] = seen.onError.mock.calls[0];
    expect(error.code).toBe("provider_error");
    expect(error.message).toBe(UNEXPECTED_ERROR_MESSAGE);
    expect(JSON.stringify(error)).not.toContain("ai.google.dev");
  });
});

describe("responses that fail before the stream starts", () => {
  it("turns a 503 into an ApiError carrying the structured error", async () => {
    stubFetch(
      new Response(
        JSON.stringify({ detail: QUOTA_FRAME.message, error: { ...QUOTA_FRAME, request_id: undefined } }),
        { status: 503, headers: { "content-type": "application/json", "x-request-id": "req-9" } }
      )
    );
    const seen = handlers();

    const failure: unknown = await streamChat("hi", seen).catch((err) => err);

    expect(failure).toBeInstanceOf(ApiError);
    const apiError = failure as ApiError;
    expect(apiError.status).toBe(503);
    expect(apiError.message).toBe(QUOTA_FRAME.message);
    expect(apiError.error?.code).toBe("quota_exhausted");
    // The body omitted it; the id is recovered from the response header.
    expect(apiError.error?.request_id).toBe("req-9");
    expect(seen.onError).not.toHaveBeenCalled();
  });

  it("does not show a non-JSON body", async () => {
    stubFetch(
      new Response("<html><body>502 Bad Gateway</body></html>", {
        status: 502,
        statusText: "Bad Gateway",
        headers: { "content-type": "text/html" },
      })
    );

    const failure: unknown = await streamChat("hi", handlers()).catch((err) => err);

    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).message).toBe("502 Bad Gateway");
    expect((failure as ApiError).message).not.toContain("<html>");
  });
});
