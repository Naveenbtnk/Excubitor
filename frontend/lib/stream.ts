import {
  parsePublicError,
  requestIdOf,
  toApiError,
  type PublicError,
} from "@/lib/api";

/**
 * Server-sent events from `POST /sandbox/stream`.
 *
 * `EventSource` cannot be used here: it only issues GET requests, and this endpoint takes a
 * JSON body. So the response body is read as raw bytes and split into SSE frames, which is all
 * `EventSource` does on top of a stream anyway. The API sends `message` frames carrying tokens,
 * a terminal `error` frame when the answer cannot continue, and a final `done` frame.
 */
export interface StreamHandlers {
  onToken: (token: string) => void;
  /** A failure the API described in its own words -- never the provider's. */
  onError: (error: PublicError) => void;
  onDone: () => void;
}

export interface StreamResult {
  /** False when the stream ended without its `done` frame, e.g. the connection dropped. */
  completed: boolean;
}

/**
 * Shown when an `error` frame is not the structured shape: a proxy error page, a truncated
 * frame, an older build. The frame's text is dropped rather than displayed -- whatever is in
 * there, it is not our wording, and for a provider failure it is exactly the payload that must
 * not reach the browser.
 */
export const UNEXPECTED_ERROR_MESSAGE =
  "The response ended unexpectedly. Try again; if it keeps happening, share the diagnostic ID with support.";

export async function streamChat(
  message: string,
  handlers: StreamHandlers,
  signal?: AbortSignal
): Promise<StreamResult> {
  const res = await fetch("/api/sandbox/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
    credentials: "include",
    cache: "no-store",
    signal,
  });

  // Failures before the first token still have a real status: the per-user rate limit, an
  // expired session, and a malformed request all land here instead of in the event stream.
  if (!res.ok) {
    throw await toApiError(res);
  }
  if (!res.body) {
    throw new Error("The server returned no stream body.");
  }

  // Every response carries one, and an error frame that somehow lacks it still gets an id the
  // user can quote to support.
  const requestId = requestIdOf(res);

  let completed = false;
  const frames: StreamHandlers = {
    onToken: handlers.onToken,
    onError: handlers.onError,
    onDone: () => {
      completed = true;
      handlers.onDone();
    },
  };

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      // Incremental decode so a multi-byte character split across chunks is not mangled, and
      // CRLF normalised here because the server terminates its frames with \r\n.
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");

      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        dispatchFrame(buffer.slice(0, boundary), frames, requestId);
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf("\n\n");
      }
    }
  } finally {
    reader.releaseLock();
  }

  return { completed };
}

function dispatchFrame(frame: string, handlers: StreamHandlers, requestId?: string): void {
  let event = "message";
  const data: string[] = [];

  for (const line of frame.split("\n")) {
    // Blank lines separate frames; lines starting with a colon are keep-alive comments.
    if (line === "" || line.startsWith(":")) continue;

    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    const value = separator === -1 ? "" : line.slice(separator + 1).replace(/^ /, "");

    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }

  const payload = data.join("\n");
  if (event === "error") {
    handlers.onError(parseErrorFrame(payload, requestId));
  } else if (event === "done") {
    handlers.onDone();
  } else if (payload) {
    handlers.onToken(payload);
  }
}

/**
 * Read the API's structured failure out of an `error` frame.
 *
 * The frame normally holds the JSON object the backend built, so it is parsed and validated.
 * Anything else is replaced with our own sentence and the frame's text is thrown away: this is
 * the boundary where a provider payload -- valid JSON or not -- stops.
 */
function parseErrorFrame(text: string, requestId?: string): PublicError {
  const fallback: PublicError = {
    code: "provider_error",
    message: UNEXPECTED_ERROR_MESSAGE,
  };
  if (requestId) fallback.request_id = requestId;

  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return fallback;
  }

  const error = parsePublicError(parsed) ?? fallback;
  if (!error.request_id && requestId) error.request_id = requestId;
  return error;
}
