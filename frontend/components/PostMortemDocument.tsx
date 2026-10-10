"use client";

import { CheckCircle2, Download, LoaderCircle, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { needsRole } from "@/lib/session";

/**
 * The document itself: the sections a reviewer reads, the follow-ups they own, and the two
 * things anyone does with a post-mortem afterwards -- move it through review and download it.
 *
 * The whole document is one form, saved in one request, because the sections are read as a set:
 * a reviewer who changes the root cause and the action items together expects one save and one
 * timeline entry, not two. Marking a single follow-up done is the exception -- that is a
 * checkbox somebody ticks while reading the Overview, so it takes the one-field route.
 */

export interface ActionItem {
  id: number;
  post_mortem_id: number;
  text: string;
  owner: string;
  owner_id: number | null;
  due_date: string | null;
  status: string;
}

export interface PostMortemDocument {
  id: number;
  incident_id: number;
  incident_title: string;
  content: string;
  timeline_summary: string;
  root_cause: string;
  contributing_factors: string[];
  impact: string;
  status: string;
  author: string;
  published_at: string | null;
  created_at: string;
  updated_at: string;
  action_items: ActionItem[];
}

interface ItemDraft {
  id: number | null;
  text: string;
  owner: string;
  due_date: string;
  status: string;
}

interface FormState {
  content: string;
  timeline_summary: string;
  root_cause: string;
  factors: string;
  impact: string;
  items: ItemDraft[];
}

const STATUS_TONES: Record<string, string> = {
  draft: "bg-neutral-100 text-neutral-600",
  in_review: "bg-amber-50 text-amber-700",
  published: "bg-emerald-50 text-emerald-700",
};

const STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  in_review: "In review",
  published: "Published",
};

function toForm(document: PostMortemDocument): FormState {
  return {
    content: document.content,
    timeline_summary: document.timeline_summary,
    root_cause: document.root_cause,
    // One factor per line: a textarea that round-trips through a list without losing anything
    // a reviewer typed, including the empty lines they meant to keep.
    factors: document.contributing_factors.join("\n"),
    impact: document.impact,
    items: document.action_items.map((item) => ({
      id: item.id,
      text: item.text,
      owner: item.owner,
      due_date: item.due_date ?? "",
      status: item.status,
    })),
  };
}

export function isOverdue(item: ActionItem, today = new Date()): boolean {
  if (item.status !== "open" || !item.due_date) return false;
  return item.due_date < today.toISOString().slice(0, 10);
}

export function PostMortemDocumentView({
  document,
  mayOperate,
  onSaved,
}: {
  document: PostMortemDocument;
  mayOperate: boolean;
  onSaved: (updated: PostMortemDocument) => void;
}) {
  const [form, setForm] = useState<FormState>(() => toForm(document));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const readOnly = !mayOperate;
  const reason = mayOperate ? undefined : needsRole("operator");

  async function send(body: Record<string, unknown>, message: string) {
    if (!mayOperate) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const updated = await api<PostMortemDocument>(`/post-mortems/${document.id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      onSaved(updated);
      setForm(toForm(updated));
      setNotice(message);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function save() {
    return send(
      {
        content: form.content,
        timeline_summary: form.timeline_summary,
        root_cause: form.root_cause,
        contributing_factors: form.factors
          .split("\n")
          .map((factor) => factor.trim())
          .filter(Boolean),
        impact: form.impact,
        action_items: form.items
          .filter((item) => item.text.trim())
          .map((item) => ({
            id: item.id,
            text: item.text,
            owner: item.owner.trim() || null,
            due_date: item.due_date || null,
            status: item.status,
          })),
      },
      "Saved."
    );
  }

  async function toggleItem(item: ActionItem) {
    if (!mayOperate) return;
    const next = item.status === "open" ? "done" : "open";
    setBusy(true);
    setError(null);
    try {
      const updated = await api<ActionItem>(`/post-mortems/${document.id}/actions/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status: next }),
      });
      onSaved({
        ...document,
        action_items: document.action_items.map((row) =>
          row.id === updated.id ? updated : row
        ),
      });
      setForm((current) => ({
        ...current,
        items: current.items.map((row) =>
          row.id === updated.id ? { ...row, status: updated.status } : row
        ),
      }));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function updateItem(index: number, patch: Partial<ItemDraft>) {
    setForm((current) => ({
      ...current,
      items: current.items.map((row, position) =>
        position === index ? { ...row, ...patch } : row
      ),
    }));
  }

  const openItems = document.action_items.filter((item) => item.status === "open").length;

  return (
    <div className="card p-4 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={`rounded-full px-2 py-1 text-[10px] font-medium uppercase tracking-widest ${
                STATUS_TONES[document.status] ?? STATUS_TONES.draft
              }`}
            >
              {STATUS_LABELS[document.status] ?? document.status}
            </span>
            <h2 className="text-lg font-semibold tracking-tight sm:text-xl">
              {document.incident_title || `Incident ${document.incident_id}`}
            </h2>
          </div>
          <p className="mt-1.5 text-xs text-neutral-500">
            {document.author || "unknown author"} · updated{" "}
            {new Date(document.updated_at).toLocaleDateString()} ·{" "}
            {openItems === 0 ? "no open action items" : `${openItems} open action items`}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* A plain link, not a fetch: the export is a file, and the browser already knows how
              to download one through the session cookie this page is using. */}
          <a
            className="btn-secondary gap-2"
            href={`/api/post-mortems/${document.id}/export.md`}
            download={`post-mortem-${document.incident_id}.md`}
          >
            <Download size={15} />
            Download .md
          </a>
        </div>
      </div>

      {mayOperate && (
        <div className="mt-4 flex flex-wrap items-center gap-2 border-y border-black/5 py-3">
          <span className="text-xs uppercase tracking-widest text-neutral-500">Review</span>
          <button
            className="btn-secondary"
            disabled={busy || document.status === "in_review"}
            onClick={() => send({ status: "in_review" }, "Sent for review.")}
          >
            Send for review
          </button>
          <button
            className="btn-primary"
            disabled={busy || document.status === "published"}
            onClick={() => send({ status: "published" }, "Published.")}
          >
            Publish
          </button>
          {document.status !== "draft" && (
            <button
              className="text-xs text-neutral-500 underline-offset-2 hover:underline"
              disabled={busy}
              onClick={() => send({ status: "draft" }, "Back to draft.")}
            >
              Back to draft
            </button>
          )}
        </div>
      )}

      {readOnly && (
        <p className="mt-4 border-y border-black/5 py-3 text-xs text-neutral-500">{reason}</p>
      )}

      {error && (
        <div role="alert" className="mt-4 rounded-xl bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {error}
        </div>
      )}
      {notice && !error && (
        <div className="mt-4 flex items-center gap-2 text-sm text-emerald-700">
          <CheckCircle2 size={15} aria-hidden />
          {notice}
        </div>
      )}

      <div className="mt-4 grid gap-4">
        <Field label="Narrative" id="pm-narrative">
          <textarea
            id="pm-narrative"
            className="input min-h-[140px] resize-y"
            value={form.content}
            readOnly={readOnly}
            disabled={busy}
            onChange={(event) => setForm({ ...form, content: event.target.value })}
          />
        </Field>

        <Field label="Timeline summary" id="pm-timeline">
          <textarea
            id="pm-timeline"
            className="input min-h-[90px] resize-y"
            value={form.timeline_summary}
            readOnly={readOnly}
            disabled={busy}
            placeholder="What happened, in what order"
            onChange={(event) => setForm({ ...form, timeline_summary: event.target.value })}
          />
        </Field>

        <Field label="Root cause" id="pm-root-cause">
          <textarea
            id="pm-root-cause"
            className="input min-h-[70px] resize-y"
            value={form.root_cause}
            readOnly={readOnly}
            disabled={busy}
            onChange={(event) => setForm({ ...form, root_cause: event.target.value })}
          />
        </Field>

        <Field label="Contributing factors" id="pm-factors" hint="One per line">
          <textarea
            id="pm-factors"
            className="input min-h-[80px] resize-y"
            value={form.factors}
            readOnly={readOnly}
            disabled={busy}
            onChange={(event) => setForm({ ...form, factors: event.target.value })}
          />
        </Field>

        <Field label="Impact" id="pm-impact">
          <textarea
            id="pm-impact"
            className="input min-h-[70px] resize-y"
            value={form.impact}
            readOnly={readOnly}
            disabled={busy}
            onChange={(event) => setForm({ ...form, impact: event.target.value })}
          />
        </Field>

        <div>
          <div className="mb-1.5 flex items-center justify-between gap-3">
            <span className="text-sm font-medium">Action items</span>
            {mayOperate && (
              <button
                type="button"
                className="inline-flex items-center gap-1.5 text-xs text-brand-rose disabled:opacity-50"
                disabled={busy}
                onClick={() =>
                  setForm({
                    ...form,
                    items: [
                      ...form.items,
                      { id: null, text: "", owner: "", due_date: "", status: "open" },
                    ],
                  })
                }
              >
                <Plus size={13} />
                Add action item
              </button>
            )}
          </div>

          <div className="grid gap-2">
            {form.items.length === 0 && (
              <p className="rounded-xl border border-dashed border-black/10 px-3 py-4 text-center text-xs text-neutral-500">
                No follow-ups yet.
              </p>
            )}
            {form.items.map((item, index) => (
              <div
                key={item.id ?? `new-${index}`}
                className="grid gap-2 rounded-xl border border-black/5 bg-white/60 p-2 sm:grid-cols-[1fr_auto]"
              >
                <div className="grid gap-2 sm:grid-cols-[2fr_1.2fr_auto_auto]">
                  <label className="sr-only" htmlFor={`action-${index}-text`}>
                    Action {index + 1}
                  </label>
                  <input
                    id={`action-${index}-text`}
                    className="input"
                    value={item.text}
                    readOnly={readOnly}
                    disabled={busy}
                    placeholder="What needs to happen"
                    onChange={(event) => updateItem(index, { text: event.target.value })}
                  />
                  <label className="sr-only" htmlFor={`action-${index}-owner`}>
                    Owner of action {index + 1}
                  </label>
                  <input
                    id={`action-${index}-owner`}
                    className="input"
                    value={item.owner}
                    readOnly={readOnly}
                    disabled={busy}
                    placeholder="owner@example.com"
                    onChange={(event) => updateItem(index, { owner: event.target.value })}
                  />
                  <label className="sr-only" htmlFor={`action-${index}-due`}>
                    Due date of action {index + 1}
                  </label>
                  <input
                    id={`action-${index}-due`}
                    className="input"
                    type="date"
                    value={item.due_date}
                    readOnly={readOnly}
                    disabled={busy}
                    onChange={(event) => updateItem(index, { due_date: event.target.value })}
                  />
                  <label className="sr-only" htmlFor={`action-${index}-status`}>
                    Status of action {index + 1}
                  </label>
                  <select
                    id={`action-${index}-status`}
                    className="input"
                    value={item.status}
                    disabled={readOnly || busy}
                    onChange={(event) => updateItem(index, { status: event.target.value })}
                  >
                    <option value="open">open</option>
                    <option value="done">done</option>
                  </select>
                </div>

                <div className="flex items-center gap-2">
                  {item.id !== null && (
                    <button
                      type="button"
                      className="rounded-full border border-black/10 px-2.5 py-1 text-xs text-neutral-600 disabled:opacity-50"
                      disabled={busy}
                      title={reason ?? "Mark this item done without saving the whole document"}
                      onClick={() => {
                        const saved = document.action_items.find((row) => row.id === item.id);
                        if (saved) void toggleItem(saved);
                      }}
                    >
                      {item.status === "open" ? "Mark done" : "Reopen"}
                    </button>
                  )}
                  {mayOperate && (
                    <button
                      type="button"
                      aria-label={`Remove action ${index + 1}`}
                      className="grid size-8 place-items-center rounded-full text-neutral-500 hover:text-brand-rose disabled:opacity-50"
                      disabled={busy}
                      onClick={() =>
                        setForm({
                          ...form,
                          items: form.items.filter((_, position) => position !== index),
                        })
                      }
                    >
                      <Trash2 size={14} />
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>

        {mayOperate && (
          <div className="flex items-center gap-3 border-t border-black/5 pt-4">
            <button className="btn-primary gap-2" disabled={busy} onClick={save}>
              {busy && <LoaderCircle size={15} aria-hidden className="animate-spin" />}
              Save changes
            </button>
            <span className="text-xs text-neutral-500">
              Saving records nothing on the timeline unless the status changes.
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

function Field({
  label,
  id,
  hint,
  children,
}: {
  label: string;
  id: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      {/* The hint is beside the label rather than inside it, so the field's accessible name is
          the label and nothing else. */}
      <div className="mb-1.5 flex items-center gap-2">
        <label className="text-sm font-medium" htmlFor={id}>
          {label}
        </label>
        {hint && <span className="text-xs text-neutral-500">{hint}</span>}
      </div>
      {children}
    </div>
  );
}
