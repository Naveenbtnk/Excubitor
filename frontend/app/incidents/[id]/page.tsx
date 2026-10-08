"use client";

import {
  Activity,
  ArrowLeft,
  FileText,
  MessageSquare,
  Send,
  Sparkles,
  UserCheck,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";
import { api, fetcher } from "@/lib/api";
import { usePaged } from "@/lib/paged";
import { needsRole, useSession } from "@/lib/session";

interface Incident {
  id: number;
  title: string;
  summary: string;
  root_cause: string;
  recommendation: string;
  confidence: number;
  status: string;
  assignee: string | null;
  created_at: string;
}

interface IncidentEvent {
  id: number;
  kind: string;
  actor: string;
  from_value: string | null;
  to_value: string | null;
  body: string;
  created_at: string;
}

const STATUSES = ["triage", "investigating", "mitigated", "resolved"];

/** Icon and label per event kind, so an unknown kind still renders as an entry. */
const EVENT_STYLES: Record<string, { icon: typeof Activity; label: string; tone: string }> = {
  created: { icon: Sparkles, label: "AI triage", tone: "bg-rose-50 text-brand-rose" },
  status: { icon: Activity, label: "Status", tone: "bg-amber-50 text-amber-700" },
  assignee: { icon: UserCheck, label: "Assignment", tone: "bg-sky-50 text-sky-700" },
  comment: { icon: MessageSquare, label: "Comment", tone: "bg-neutral-100 text-neutral-700" },
  post_mortem: { icon: FileText, label: "Post-mortem", tone: "bg-emerald-50 text-emerald-700" },
};

function formatWhen(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export default function IncidentDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;
  const { can } = useSession();
  const mayOperate = can("operator");

  const { data: incident, error, mutate: mutateIncident } = useSWR<Incident>(
    id ? `/incidents/${id}` : null,
    fetcher
  );
  const {
    items: events,
    total,
    hasMore,
    loadingMore,
    loadMore,
    mutate: mutateActivity,
  } = usePaged<IncidentEvent>(`/incidents/${id}/activity`);

  const [comment, setComment] = useState("");
  const [assignee, setAssignee] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  async function refresh() {
    await Promise.all([mutateIncident(), mutateActivity()]);
  }

  async function patch(body: Record<string, unknown>) {
    // The controls are disabled for a viewer; returning here as well means a forced submit
    // cannot send a request the UI has decided not to offer. The API refuses it regardless.
    if (!mayOperate) return;
    setBusy(true);
    setActionError(null);
    try {
      await api(`/incidents/${id}`, { method: "PATCH", body: JSON.stringify(body) });
      await refresh();
    } catch (err) {
      setActionError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function addComment() {
    if (!mayOperate || !comment.trim()) return;
    setBusy(true);
    setActionError(null);
    try {
      await api(`/incidents/${id}/comments`, {
        method: "POST",
        body: JSON.stringify({ body: comment }),
      });
      setComment("");
      await mutateActivity();
    } catch (err) {
      setActionError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (error) {
    return (
      <div className="pt-2 sm:pt-4">
        <Link href="/post-mortems" className="mb-4 inline-flex items-center gap-1.5 text-sm text-brand-rose">
          <ArrowLeft size={14} /> Back to incidents
        </Link>
        <div className="card p-5 text-sm text-rose-700">
          {(error as Error).message || "This incident could not be loaded."}
        </div>
      </div>
    );
  }

  return (
    <div className="grid gap-5 pt-2 sm:pt-4 lg:grid-cols-[1.4fr_1fr] lg:gap-6">
      <div>
        <Link href="/post-mortems" className="mb-3 inline-flex items-center gap-1.5 text-sm text-brand-rose">
          <ArrowLeft size={14} /> All incidents
        </Link>

        <div className="card p-4 sm:p-5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-neutral-100 px-2 py-1 text-[10px] uppercase tracking-widest text-neutral-600">
              {incident?.status ?? "…"}
            </span>
            <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">
              {incident?.title ?? "Loading incident…"}
            </h1>
          </div>

          <div className="mt-3 text-sm text-neutral-600">{incident?.summary}</div>
          {incident && (
            <div className="mt-2 text-xs text-neutral-500">
              Assigned to {incident.assignee ?? "nobody yet"} · Confidence{" "}
              {Math.round(incident.confidence * 100)}%
            </div>
          )}
          {incident?.root_cause && (
            <div className="mt-4 rounded-xl bg-white p-3 text-sm text-neutral-700">
              Root cause: {incident.root_cause}
            </div>
          )}
          {incident?.recommendation && (
            <pre className="mt-3 whitespace-pre-wrap rounded-xl bg-neutral-950 p-4 font-sans text-sm text-white">
              {incident.recommendation}
            </pre>
          )}
        </div>

        <div className="card mt-4 p-4 sm:p-5">
          <h2 className="mb-3 font-semibold">Update incident</h2>

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-sm">
              <span className="mb-1 block text-xs uppercase tracking-widest text-neutral-500">
                Status
              </span>
              <select
                className="input"
                value={incident?.status ?? "triage"}
                disabled={!mayOperate || !incident || busy}
                title={mayOperate ? undefined : needsRole("operator")}
                onChange={(event) => patch({ status: event.target.value })}
              >
                {STATUSES.map((status) => (
                  <option key={status} value={status}>
                    {status}
                  </option>
                ))}
              </select>
            </label>

            <form
              className="text-sm"
              onSubmit={(event) => {
                event.preventDefault();
                patch({ assignee: assignee.trim() || null });
                setAssignee("");
              }}
            >
              <label className="mb-1 block text-xs uppercase tracking-widest text-neutral-500" htmlFor="assignee">
                Assignee
              </label>
              <div className="flex gap-2">
                <input
                  id="assignee"
                  className="input"
                  value={assignee}
                  placeholder={incident?.assignee ?? "teammate@cloudops.local"}
                  disabled={!mayOperate || busy}
                  title={mayOperate ? undefined : needsRole("operator")}
                  onChange={(event) => setAssignee(event.target.value)}
                />
                <button
                  type="submit"
                  className="btn-secondary"
                  disabled={!mayOperate || busy}
                  title={mayOperate ? undefined : needsRole("operator")}
                >
                  Assign
                </button>
              </div>
            </form>
          </div>

          <div className="mt-4">
            <label className="mb-1 block text-xs uppercase tracking-widest text-neutral-500" htmlFor="comment">
              Comment
            </label>
            <textarea
              id="comment"
              className="input min-h-[90px] resize-y"
              value={comment}
              placeholder="What have you tried, and what did it do?"
              disabled={!mayOperate || busy}
              title={mayOperate ? undefined : needsRole("operator")}
              onChange={(event) => setComment(event.target.value)}
            />
            <button
              onClick={addComment}
              className="btn-primary mt-2 gap-2"
              disabled={!mayOperate || busy || !comment.trim()}
              title={mayOperate ? undefined : needsRole("operator")}
            >
              <Send size={14} />
              Add comment
            </button>
          </div>

          {actionError && <div className="mt-3 text-sm text-rose-700">{actionError}</div>}
          {!mayOperate && (
            <div className="mt-3 text-xs text-neutral-500">{needsRole("operator")}</div>
          )}
        </div>
      </div>

      <div>
        <div className="mb-4">
          <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">Activity</h2>
          <p className="mt-1 text-sm text-neutral-500">
            Every change to this incident, oldest first — {total}{" "}
            {total === 1 ? "entry" : "entries"}.
          </p>
        </div>

        <div className="card p-4 sm:p-5">
          <ol className="relative space-y-4 border-l border-black/10 pl-5">
            {events.length === 0 && (
              <li className="pl-1 text-sm text-neutral-500">Nothing recorded yet.</li>
            )}
            {events.map((event) => {
              const style = EVENT_STYLES[event.kind] ?? {
                icon: Activity,
                label: event.kind,
                tone: "bg-neutral-100 text-neutral-700",
              };
              const Icon = style.icon;
              return (
                <li key={event.id} className="relative">
                  <span
                    className={`absolute -left-[30px] grid size-6 place-items-center rounded-full ${style.tone}`}
                  >
                    <Icon size={13} />
                  </span>
                  <div className="flex flex-wrap items-center gap-2 text-xs text-neutral-500">
                    <span className="font-medium text-neutral-700">{style.label}</span>
                    <span>·</span>
                    <span className="truncate">{event.actor}</span>
                    <span>·</span>
                    <time dateTime={event.created_at}>{formatWhen(event.created_at)}</time>
                  </div>
                  {(event.from_value || event.to_value) && (
                    <div className="mt-1 text-xs text-neutral-600">
                      {event.from_value ? <span className="line-through">{event.from_value}</span> : null}
                      {event.from_value ? " → " : null}
                      <span className="font-medium">{event.to_value ?? "unassigned"}</span>
                    </div>
                  )}
                  {event.body && <div className="mt-1 text-sm text-neutral-700">{event.body}</div>}
                </li>
              );
            })}
          </ol>

          <button
            onClick={loadMore}
            className="mt-4 w-full rounded-xl border border-black/5 py-2 text-sm text-neutral-600 disabled:opacity-50"
            disabled={!hasMore || loadingMore}
          >
            {loadingMore ? "Loading…" : hasMore ? "Load more activity" : "That is the whole timeline"}
          </button>
        </div>
      </div>
    </div>
  );
}
