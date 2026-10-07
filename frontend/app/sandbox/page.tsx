"use client";

import { Send } from "lucide-react";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "@/lib/api";

interface Msg {
  role: "user" | "assistant";
  content: string;
}

export default function SandboxPage() {
  const [history, setHistory] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  async function send() {
    const message = input.trim();
    if (!message || loading) return;
    setHistory((items) => [...items, { role: "user", content: message }]);
    setInput("");
    setLoading(true);
    try {
      const res = await api<{ reply: string }>("/sandbox/chat", {
        method: "POST",
        body: JSON.stringify({ message }),
      });
      setHistory((items) => [...items, { role: "assistant", content: res.reply }]);
    } catch (err) {
      setHistory((items) => [...items, { role: "assistant", content: `Error: ${(err as Error).message}` }]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="pt-2 sm:pt-4">
      <div className="mb-4">
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Sandbox</h1>
        <p className="mt-1 text-sm text-neutral-500">Prompt Gemini through the backend without exposing your API key to the browser.</p>
      </div>

      <div className="card flex min-h-[55vh] flex-col gap-3 p-3 sm:min-h-[520px] sm:p-4">
        {history.length === 0 && (
          <div className="grid flex-1 place-items-center px-4 py-16 text-center text-sm text-neutral-500">
            Ask about an incident, e.g. “Postgres pool exhaustion on orders-api”.
          </div>
        )}
        {history.map((message, index) => (
          <div key={index} className={message.role === "user" ? "self-end" : "self-start"}>
            <div className={message.role === "user" ? "max-w-[85vw] rounded-2xl bg-neutral-900 px-4 py-3 text-sm text-white sm:max-w-[620px]" : "prose prose-sm max-w-[85vw] rounded-2xl border border-black/5 bg-white px-4 py-3 sm:max-w-[720px]"}>
              {message.role === "assistant" ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown> : message.content}
            </div>
          </div>
        ))}
        {loading && <div className="self-start rounded-2xl bg-white px-4 py-3 text-sm text-neutral-500">Thinking…</div>}
      </div>

      <div className="mt-3 flex gap-2">
        <input value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => event.key === "Enter" && send()} placeholder="Describe the incident…" className="input rounded-full" />
        <button onClick={send} disabled={loading} className="btn-primary size-10 shrink-0 px-0 sm:w-auto sm:px-4" aria-label="Send message">
          <Send size={16} />
          <span className="ml-2 hidden sm:inline">Send</span>
        </button>
      </div>
    </div>
  );
}
