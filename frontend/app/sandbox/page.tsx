"use client";

import { Copy, Send, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ApiError, type PublicError } from "@/lib/api";
import { useSession } from "@/lib/session";
import { streamChat } from "@/lib/stream";

interface Msg {
  role: "user" | "assistant";
  content: string;
  /** A refusal or provider failure that arrived after the response had already started. */
  error?: PublicError;
  stopped?: boolean;
}

/**
 * A failure that was thrown rather than streamed, in the same shape, so the note below renders
 * every failure one way. `ApiError.error` is the API's structured form when it sent one; its
 * message is a sentence the API wrote, which is safe to show.
 */
function toStreamError(err: unknown): PublicError {
  if (err instanceof ApiError && err.error) return err.error;
  const message =
    err instanceof Error && err.message
      ? err.message
      : "The response ended before it was finished.";
  return { code: "request_failed", message };
}

/**
 * The request id, as an action rather than a string on screen.
 *
 * It is a support handle -- "what happened at 14:02?" -- not part of the answer, so it stays
 * out of the conversation and is copied only when someone actually needs to quote it.
 */
function DiagnosticId({ id }: { id: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");

  async function copy() {
    try {
      await navigator.clipboard.writeText(id);
      setState("copied");
    } catch {
      // Clipboard access needs a secure context; when it is unavailable, say so instead of
      // printing the id, which would put it back in the conversation.
      setState("failed");
    }
  }

  return (
    <button
      type="button"
      onClick={copy}
      aria-label="Copy diagnostic ID"
      className="mt-1 inline-flex items-center gap-1 text-[11px] font-medium text-amber-800 underline underline-offset-2 hover:text-amber-950"
    >
      <Copy size={12} aria-hidden />
      {state === "copied" ? "Copied" : state === "failed" ? "Could not copy" : "Copy diagnostic ID"}
    </button>
  );
}

export default function SandboxPage() {
  const { user } = useSession();
  // Generating a response spends provider budget, not state, and this route reads the corpus
  // the deployment already holds -- so every signed-in role may ask, exactly as
  // ``POST /sandbox/chat`` and ``/sandbox/stream`` allow (see ``test_a_viewer_may_ask_the_sandbox``).
  // What bounds the spending is the project-wide provider quota and, on a shared demo login,
  // ``demo_guard``; a role would bound it in the wrong place. The control waits for the session
  // to resolve and is enabled for anyone the API would answer.
  const mayAsk = Boolean(user);
  const [history, setHistory] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // A navigation away from the page must not leave a generation running.
  useEffect(() => () => abortRef.current?.abort(), []);

  function patchLast(patch: (message: Msg) => Msg) {
    setHistory((items) => {
      const last = items[items.length - 1];
      if (!last || last.role !== "assistant") return items;
      const next = [...items];
      next[next.length - 1] = patch(last);
      return next;
    });
  }

  async function send() {
    const message = input.trim();
    if (!message || streaming) return;

    setHistory((items) => [
      ...items,
      { role: "user", content: message },
      { role: "assistant", content: "" },
    ]);
    setInput("");
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const { completed } = await streamChat(
        message,
        {
          onToken: (token) =>
            patchLast((entry) => ({ ...entry, content: entry.content + token })),
          // Quota exhaustion, a spent provider budget and upstream failures all arrive here:
          // once the first byte is sent the status line is fixed, so the API reports the rest
          // as events rather than as an HTTP error. It sends a code, its own sentence, an
          // optional wait and a request id -- never the provider's text, and the renderer
          // below trusts only that shape.
          onError: (error) => patchLast((entry) => ({ ...entry, error })),
          onDone: () => {},
        },
        controller.signal
      );
      if (!completed) {
        patchLast((entry) => ({
          ...entry,
          error: entry.error ?? {
            code: "stream_incomplete",
            message: "The response ended before it was finished.",
          },
        }));
      }
    } catch (err) {
      if (controller.signal.aborted) {
        // Stopping keeps whatever was already generated; nothing is thrown away.
        patchLast((entry) => ({
          ...entry,
          stopped: true,
          content: entry.content || "Stopped before any output.",
        }));
      } else {
        patchLast((entry) => ({ ...entry, error: toStreamError(err) }));
      }
    } finally {
      abortRef.current = null;
      setStreaming(false);
    }
  }

  function stop() {
    abortRef.current?.abort();
  }

  const lastIndex = history.length - 1;

  return (
    <div className="pt-2 sm:pt-4">
      <div className="mb-4">
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Sandbox</h1>
        <p className="mt-1 text-sm text-neutral-500">Prompt Gemini through the backend without exposing your API key to the browser. Answers stream as they are written.</p>
      </div>

      <div className="card flex min-h-[55vh] flex-col gap-3 p-3 sm:min-h-[520px] sm:p-4" aria-busy={streaming}>
        {history.length === 0 && (
          <div className="grid flex-1 place-items-center px-4 py-16 text-center text-sm text-neutral-500">
            Ask about an incident, e.g. “Postgres pool exhaustion on orders-api”.
          </div>
        )}
        {history.map((message, index) => {
          const working = streaming && index === lastIndex;
          const thinking = working && !message.content && !message.error;
          return (
            <div key={index} className={message.role === "user" ? "self-end" : "self-start"}>
              <div className={message.role === "user" ? "max-w-[85vw] rounded-2xl bg-neutral-900 px-4 py-3 text-sm text-white sm:max-w-[620px]" : "prose prose-sm max-w-[85vw] rounded-2xl border border-black/5 bg-white px-4 py-3 sm:max-w-[720px]"}>
                {message.role === "user" ? (
                  message.content
                ) : (
                  <>
                    {message.content && <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>}
                    {thinking && <span className="text-neutral-500">Thinking…</span>}
                    {working && message.content && (
                      <span className="ml-0.5 inline-block h-4 w-[7px] translate-y-[2px] animate-pulse rounded-sm bg-neutral-400" aria-hidden />
                    )}
                  </>
                )}
              </div>
              {message.error && (
                // The sentence is the API's own, already stripped of everything operational:
                // no provider payload, no URL, no model or quota metadata. The code rides
                // along as a data attribute for debugging and tests, not for display. The
                // request id is offered as a copy action instead of being printed.
                <div
                  data-error-code={message.error.code}
                  className="mt-1 max-w-[85vw] rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-900 sm:max-w-[720px]"
                >
                  <p className="break-words">⚠ {message.error.message}</p>
                  {message.error.request_id && <DiagnosticId id={message.error.request_id} />}
                </div>
              )}
              {message.stopped && (
                <p className="mt-1 text-xs text-neutral-500">Stopped by you. The partial answer above was kept.</p>
              )}
            </div>
          );
        })}
      </div>

      <div className="mt-3 flex gap-2">
        <input value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => event.key === "Enter" && send()} placeholder="Describe the incident…" className="input rounded-full" />
        {streaming ? (
          <button onClick={stop} className="btn-primary size-10 shrink-0 px-0 sm:w-auto sm:px-4" aria-label="Stop generating">
            <Square size={16} />
            <span className="ml-2 hidden sm:inline">Stop</span>
          </button>
        ) : (
          <button
            onClick={send}
            disabled={!mayAsk}
            title={mayAsk ? undefined : "Checking your session…"}
            className="btn-primary size-10 shrink-0 px-0 sm:w-auto sm:px-4"
            aria-label="Send message"
          >
            <Send size={16} />
            <span className="ml-2 hidden sm:inline">Send</span>
          </button>
        )}
      </div>

      {streaming && (
        <p className="mt-2 text-xs text-neutral-500">Streaming from the provider. Stop keeps the partial answer.</p>
      )}
    </div>
  );
}
