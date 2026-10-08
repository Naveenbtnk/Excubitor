/**
 * The structured failure the API attaches to error responses (`error` alongside `detail`) and
 * to SSE `error` frames. Every field is written by our backend: `message` is a complete
 * sentence it composed, never the provider's own text, which only ever reaches the server logs.
 */
export interface PublicError {
  code: string;
  message: string;
  provider?: string;
  retry_after?: number;
  request_id?: string;
}

/**
 * Narrow an untrusted value to that shape, discarding anything unfamiliar.
 *
 * Deliberately strict: a provider payload is also an object with strings in it, and the one
 * thing that must never happen is echoing it into the UI because it happened to be well-formed
 * JSON. Only our exact vocabulary survives.
 */
export function parsePublicError(value: unknown): PublicError | null {
  if (!value || typeof value !== "object") return null;
  const { code, message, provider, retry_after, request_id } = value as Record<string, unknown>;
  if (typeof code !== "string" || !code || typeof message !== "string" || !message) return null;

  const error: PublicError = { code, message };
  if (typeof provider === "string" && provider) error.provider = provider;
  if (typeof retry_after === "number" && Number.isFinite(retry_after)) {
    error.retry_after = retry_after;
  }
  if (typeof request_id === "string" && request_id) error.request_id = request_id;
  return error;
}

/** The request id the API echoes on every response, for bodies that do not carry one. */
export function requestIdOf(res: Response): string | undefined {
  return res.headers.get("x-request-id") ?? undefined;
}

function extractDetail(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (typeof item === "string" ? item : (item as { msg?: string })?.msg))
      .filter((msg): msg is string => Boolean(msg));
    return messages.length > 0 ? messages.join("; ") : null;
  }
  if (detail && typeof detail === "object" && "msg" in detail) {
    return String((detail as { msg: unknown }).msg);
  }
  return null;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    /** The structured failure, when the API sent one. */
    public error?: PublicError
  ) {
    super(message);
  }
}

/**
 * Turn a failed response into an ApiError, preferring the API's own explanation: the backend
 * reports rate limits, provider errors and refusals as `detail`, and those messages are far
 * more useful to show a user than "503 Service Unavailable".
 *
 * A body that is not JSON is not shown at all. It comes from something that is not this API --
 * a proxy's HTML error page, a truncated response -- and it is neither safe nor useful to
 * paste into a chat bubble, so the status line stands in for it.
 */
export async function toApiError(res: Response): Promise<ApiError> {
  let message = `${res.status} ${res.statusText}`;
  let error: PublicError | undefined;
  try {
    const body = (await res.json()) as { detail?: unknown; error?: unknown };
    error = parsePublicError(body.error) ?? undefined;
    // `detail` is the API's own sentence and doubles as the message when there is no
    // structured form to render more richly.
    message = error?.message ?? extractDetail(body.detail) ?? message;
  } catch {
    // Deliberately swallowed: see above.
  }
  if (error && !error.request_id) {
    error.request_id = requestIdOf(res);
  }
  return new ApiError(res.status, message, error);
}

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (!(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`/api${path}`, {
    ...init,
    headers,
    credentials: "include",
    cache: "no-store",
  });

  if (!res.ok) {
    throw await toApiError(res);
  }

  if (res.status === 204) {
    return undefined as T;
  }
  return res.json();
}

export const fetcher = <T = unknown>(path: string) => api<T>(path);
