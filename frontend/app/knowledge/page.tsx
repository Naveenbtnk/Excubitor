"use client";

import { Search, UploadCloud } from "lucide-react";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { LoadMore } from "@/components/LoadMore";
import { api } from "@/lib/api";
import { usePaged } from "@/lib/paged";
import { needsRole, useSession } from "@/lib/session";

interface Runbook {
  id: number;
  title: string;
  source: string;
  content: string;
}

interface Hit {
  title: string;
  source: string;
  content: string;
}

interface KnowledgeAnswer {
  answer: string;
  hits: Hit[];
}

export default function KnowledgePage() {
  const { can } = useSession();
  // Adding to the shared corpus is an Operator action; asking questions about it is reading,
  // so the query half of this page stays available to every role.
  const mayOperate = can("operator");
  const {
    items: runbooks,
    total,
    hasMore,
    loadingMore,
    loadMore,
    mutate,
  } = usePaged<Runbook>("/knowledge/runbooks");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<KnowledgeAnswer | null>(null);
  const [busy, setBusy] = useState(false);
  const [querying, setQuerying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function add() {
    // Indexing spends embedding budget and changes the shared corpus, so it needs the same
    // role the route does; asking a question stays open to every role.
    if (!mayOperate || !title.trim() || !content.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api("/knowledge/runbooks", {
        method: "POST",
        body: JSON.stringify({ title, content, source: "manual" }),
      });
      setTitle("");
      setContent("");
      await mutate();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function search() {
    if (!question.trim()) return;
    setQuerying(true);
    setError(null);
    try {
      setAnswer(await api<KnowledgeAnswer>("/knowledge/query", {
        method: "POST",
        body: JSON.stringify({ question }),
      }));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setQuerying(false);
    }
  }

  return (
    <div className="grid gap-5 pt-2 sm:pt-4 lg:grid-cols-2 lg:gap-6">
      <div>
        <div className="mb-4">
          <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Knowledge</h1>
          <p className="mt-1 text-sm text-neutral-500">Index runbooks into PostgreSQL pgvector and query them with Gemini.</p>
        </div>

        {error && <div className="card mb-4 p-4 text-sm text-rose-700">{error}</div>}

        <div className="card p-4 sm:p-5">
          <h2 className="mb-3 font-semibold">Add runbook</h2>
          {!mayOperate && <p className="mb-2 text-xs text-neutral-500">{needsRole("operator")}</p>}
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="Runbook title, e.g. database pool exhaustion"
            className="input mb-2"
            disabled={!mayOperate}
            title={mayOperate ? undefined : needsRole("operator")}
          />
          <textarea
            value={content}
            onChange={(event) => setContent(event.target.value)}
            placeholder="Paste runbook markdown or text here…"
            className="input min-h-[220px] resize-y"
            disabled={!mayOperate}
            title={mayOperate ? undefined : needsRole("operator")}
          />
          <button onClick={add} disabled={busy || !mayOperate} title={mayOperate ? undefined : needsRole("operator")} className="btn-primary mt-3 gap-2">
            <UploadCloud size={15} />
            {busy ? "Indexing…" : "Index runbook"}
          </button>
        </div>

        <div className="card mt-4 p-4 sm:p-5">
          <h2 className="mb-3 font-semibold">Indexed ({total})</h2>
          <div className="divide-y divide-black/5 text-sm">
            {runbooks.length === 0 && <div className="py-6 text-center text-neutral-500">No runbooks indexed yet.</div>}
            {runbooks.map((runbook) => (
              <div key={runbook.id} className="grid gap-1 py-3 sm:grid-cols-[1fr_auto] sm:items-center">
                <span className="truncate font-mono text-xs">{runbook.title}</span>
                <span className="text-xs text-emerald-600">{runbook.source} · Ready</span>
              </div>
            ))}
          </div>
          <LoadMore
            className="mt-3 border-t border-black/5 pt-3"
            showing={runbooks.length}
            total={total}
            hasMore={hasMore}
            loading={loadingMore}
            onLoadMore={loadMore}
          />
        </div>
      </div>

      <div>
        <div className="mb-4">
          <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">Runbook Query</h2>
          <p className="mt-1 text-sm text-neutral-500">Ask operational questions and inspect retrieved chunks.</p>
        </div>
        <div className="card p-4 sm:p-5">
          <div className="flex flex-col gap-2 sm:flex-row">
            <input value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="How do I fix a Postgres pool exhaustion?" onKeyDown={(event) => event.key === "Enter" && search()} className="input" />
            <button onClick={search} disabled={querying} className="btn-primary gap-2 sm:w-auto">
              <Search size={15} />
              {querying ? "Asking…" : "Ask"}
            </button>
          </div>

          {answer && (
            <div className="mt-5 space-y-4">
              <article className="prose prose-sm max-w-none rounded-2xl bg-white p-4">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{answer.answer}</ReactMarkdown>
              </article>
              <div className="space-y-3">
                <h3 className="text-sm font-semibold">Retrieved context</h3>
                {answer.hits.map((hit, index) => (
                  <div key={`${hit.title}-${index}`} className="rounded-xl border border-black/5 bg-white p-3">
                    <div className="mb-1 text-xs text-neutral-500">{hit.title} · {hit.source}</div>
                    <div className="text-sm text-neutral-700">{hit.content}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
