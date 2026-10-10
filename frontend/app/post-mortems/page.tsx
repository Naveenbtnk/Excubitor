"use client";

import { FileText, LoaderCircle, Plus } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { LoadMore } from "@/components/LoadMore";
import {
  PostMortemDocumentView,
  type PostMortemDocument,
} from "@/components/PostMortemDocument";
import { api } from "@/lib/api";
import { usePaged } from "@/lib/paged";
import { needsRole, useSession } from "@/lib/session";

/**
 * The post-mortem workspace: generate a draft from an incident, then review, edit and share the
 * document it produced.
 *
 * The list is whatever the API says this caller may read -- every document for an Operator or
 * Admin, the published ones for a Viewer -- so the page does not filter anything itself, and a
 * Viewer cannot be shown a draft by a mistake in this file. Generating stays behind the role
 * check twice over: the control is disabled, and the handler returns without a request.
 */

interface Incident {
  id: number;
  title: string;
  summary: string;
  status: string;
}

const STATUS_CHIPS: Record<string, string> = {
  draft: "bg-neutral-100 text-neutral-600",
  in_review: "bg-amber-50 text-amber-700",
  published: "bg-emerald-50 text-emerald-700",
};

const STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  in_review: "In review",
  published: "Published",
};

function openCount(document: PostMortemDocument): number {
  return document.action_items.filter((item) => item.status === "open").length;
}

export default function PostMortemsPage() {
  const { can } = useSession();
  const mayOperate = can("operator");
  const {
    items: documents,
    total,
    hasMore,
    loadingMore,
    loadMore,
    mutate,
  } = usePaged<PostMortemDocument>("/post-mortems");
  const { items: incidents } = usePaged<Incident>("/incidents");
  const [selected, setSelected] = useState<PostMortemDocument | null>(null);
  const [incidentId, setIncidentId] = useState("");
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const untriaged = incidents.filter(
    (incident) => !documents.some((document) => document.incident_id === incident.id)
  );

  async function generate() {
    // Spending provider budget and resolving an incident: refuse without a request as well as
    // disabling the control, so a forced click cannot write.
    if (!mayOperate || !incidentId) return;
    setGenerating(true);
    setError(null);
    try {
      const created = await api<PostMortemDocument>(`/incidents/${incidentId}/post-mortem`, {
        method: "POST",
      });
      setSelected(created);
      setIncidentId("");
      await mutate();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setGenerating(false);
    }
  }

  function onSaved(updated: PostMortemDocument) {
    setSelected(updated);
    // The list shows each document's state and open-item count, so it is now out of date.
    void mutate();
  }

  return (
    <div className="grid gap-5 pt-2 sm:pt-4 lg:grid-cols-3 lg:gap-6">
      <div className="grid content-start gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Post-mortems</h1>
          <p className="mt-1 text-sm text-neutral-500">
            Draft, review and publish the write-up for an incident.
          </p>
        </div>

        <div className="card p-4">
          <label className="mb-1.5 block text-sm font-medium" htmlFor="incident-to-write-up">
            Write up an incident
          </label>
          <div className="flex flex-col gap-2 sm:flex-row">
            <select
              id="incident-to-write-up"
              className="input"
              value={incidentId}
              disabled={!mayOperate}
              title={mayOperate ? undefined : needsRole("operator")}
              onChange={(event) => setIncidentId(event.target.value)}
            >
              <option value="">Choose an incident…</option>
              {untriaged.map((incident) => (
                <option key={incident.id} value={incident.id}>
                  {incident.id} · {incident.title}
                </option>
              ))}
            </select>
            <button
              className="btn-primary shrink-0 gap-2"
              disabled={!mayOperate || !incidentId || generating}
              title={mayOperate ? undefined : needsRole("operator")}
              onClick={generate}
            >
              {generating ? (
                <LoaderCircle size={15} aria-hidden className="animate-spin" />
              ) : (
                <Plus size={15} aria-hidden />
              )}
              Generate draft
            </button>
          </div>
          <p className="mt-2 text-xs text-neutral-500">
            The draft starts unpublished and is filled in from the incident record.
          </p>
        </div>

        {error && (
          <div role="alert" className="card p-4 text-sm text-rose-700">
            {error}
          </div>
        )}

        <div className="card divide-y divide-black/5 overflow-hidden">
          {documents.length === 0 && (
            <div className="p-8 text-center text-sm text-neutral-500">
              {mayOperate
                ? "No post-mortems yet. Write one up above."
                : "No published post-mortems yet."}
            </div>
          )}
          {documents.map((document) => (
            <button
              key={document.id}
              onClick={() => setSelected(document)}
              className={`grid w-full gap-1 p-4 text-left transition hover:bg-white/60 ${
                selected?.id === document.id ? "bg-white/80" : ""
              }`}
            >
              <div className="flex items-start justify-between gap-3">
                <span className="min-w-0 truncate text-sm font-medium">
                  {document.incident_title || `Incident ${document.incident_id}`}
                </span>
                <span
                  className={`shrink-0 rounded-full px-2 py-1 text-[10px] uppercase tracking-widest ${
                    STATUS_CHIPS[document.status] ?? STATUS_CHIPS.draft
                  }`}
                >
                  {STATUS_LABELS[document.status] ?? document.status}
                </span>
              </div>
              <div className="text-xs text-neutral-500">
                {openCount(document)} open · updated{" "}
                {new Date(document.updated_at).toLocaleDateString()}
              </div>
            </button>
          ))}
          <LoadMore
            className="px-4 py-3"
            showing={documents.length}
            total={total}
            hasMore={hasMore}
            loading={loadingMore}
            onLoadMore={loadMore}
          />
        </div>
      </div>

      <div className="lg:col-span-2">
        {selected ? (
          <PostMortemDocumentView
            key={selected.id}
            document={selected}
            mayOperate={mayOperate}
            onSaved={onSaved}
          />
        ) : (
          <div className="card grid min-h-[430px] place-items-center p-6 text-center text-sm text-neutral-500">
            <div>
              <div className="mx-auto mb-3 grid size-12 place-items-center rounded-2xl bg-rose-50 text-brand-rose">
                <FileText size={22} />
              </div>
              {documents.length > 0 ? (
                <>
                  Pick a document to read or review it.
                  <div className="mt-3">
                    <Link
                      href={`/incidents/${documents[0].incident_id}`}
                      className="text-xs text-brand-rose underline-offset-2 hover:underline"
                    >
                      Or open the incident timeline
                    </Link>
                  </div>
                </>
              ) : (
                "A post-mortem starts as an AI draft from an incident, then goes through review."
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
