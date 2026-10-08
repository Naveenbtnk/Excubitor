"use client";

import { FileText, Sparkles } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useState } from "react";
import { LoadMore } from "@/components/LoadMore";
import { api } from "@/lib/api";
import { usePaged } from "@/lib/paged";
import { needsRole, useSession } from "@/lib/session";

interface Incident {
  id: number;
  title: string;
  summary: string;
  status: string;
}

interface PostMortem {
  id: number;
  incident_id: number;
  content: string;
}

export default function PostMortemsPage() {
  const { can } = useSession();
  const mayOperate = can("operator");
  const {
    items: incidents,
    total,
    hasMore,
    loadingMore,
    loadMore,
    mutate,
  } = usePaged<Incident>("/incidents");
  const [postMortem, setPostMortem] = useState<PostMortem | null>(null);
  const [loading, setLoading] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function generate(id: number) {
    setLoading(id);
    setError(null);
    try {
      setPostMortem(await api<PostMortem>(`/incidents/${id}/post-mortem`, { method: "POST" }));
      await mutate();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(null);
    }
  }

  return (
    <div className="grid gap-5 pt-2 sm:pt-4 lg:grid-cols-3 lg:gap-6">
      <div>
        <div className="mb-4">
          <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Post-Mortems</h1>
          <p className="mt-1 text-sm text-neutral-500">Generate markdown reports from AI-triaged incidents.</p>
        </div>

        {error && <div className="card mb-4 p-4 text-sm text-rose-700">{error}</div>}

        <div className="card divide-y divide-black/5 overflow-hidden">
          {incidents.length === 0 && <div className="p-8 text-center text-sm text-neutral-500">No incidents yet. Triage an alert first.</div>}
          {incidents.map((incident) => (
            <button
              key={incident.id}
              onClick={() => generate(incident.id)}
              disabled={!mayOperate}
              title={mayOperate ? undefined : needsRole("operator")}
              className="w-full p-4 text-left transition hover:bg-white/60 disabled:cursor-not-allowed"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{incident.title}</div>
                  <div className="mt-1 line-clamp-2 text-xs text-neutral-500">{incident.summary}</div>
                </div>
                <span className="shrink-0 rounded-full bg-neutral-100 px-2 py-1 text-[10px] uppercase tracking-widest text-neutral-600">
                  {incident.status}
                </span>
              </div>
              {loading === incident.id && <div className="mt-2 text-xs text-brand-rose">Generating…</div>}
            </button>
          ))}
          <LoadMore
            className="px-4 py-3"
            showing={incidents.length}
            total={total}
            hasMore={hasMore}
            loading={loadingMore}
            onLoadMore={loadMore}
          />
        </div>
      </div>

      <div className="lg:col-span-2">
        <div className="card min-h-[430px] p-4 sm:p-6">
          {!postMortem && (
            <div className="grid min-h-[360px] place-items-center text-center text-sm text-neutral-500">
              <div>
                <div className="mx-auto mb-3 grid size-12 place-items-center rounded-2xl bg-rose-50 text-brand-rose">
                  <FileText size={22} />
                </div>
                Select an incident to generate a post-mortem.
              </div>
            </div>
          )}
          {postMortem && (
            <div>
              <div className="mb-4 inline-flex items-center gap-2 rounded-full bg-rose-50 px-3 py-1 text-xs font-medium text-brand-rose">
                <Sparkles size={13} />
                Generated post-mortem
              </div>
              <article className="prose prose-sm max-w-none sm:prose-base">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{postMortem.content}</ReactMarkdown>
              </article>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
